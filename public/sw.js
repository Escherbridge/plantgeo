// v2: purge v1 caches that trapped pre-launch API responses; API requests are
// now network-only (never cached) so data can never go stale behind the SW.
const CACHE_VERSION = 'v2';
const CACHE_NAME = 'plantgeo-' + CACHE_VERSION;
const CACHE_SIZE_LIMIT_BYTES = 500 * 1024 * 1024; // 500MB

const APP_SHELL = [
  '/',
  '/manifest.webmanifest',
];

// Immutable-enough hosts: cache-first, because the bytes for a given z/x/y never change.
const TILE_URL_PATTERNS = [
  'arcgisonline.com',
  'elevation-tiles-prod',
  'basemaps-assets',
];

// Dynamic Martin tiles: stale-while-revalidate, NOT cache-first and NOT network-first.
//
// These used to fall through to networkFirst, which is the worst available strategy here --
// it waits on the network on EVERY request and only reads the cache when the network
// actually fails. A slow tile is not a failed tile, so a perfectly good cached copy sat
// unused while the user waited. Martin sends no `Cache-Control` either (measured 2026-08-21:
// only an etag), so the browser's own HTTP cache cannot stand in. Between them, nothing was
// reusable and every pan re-paid full origin cost.
//
// The original exclusion note was right that Martin data changes between cron runs, but
// cache-first would have pinned stale tiles indefinitely. Stale-while-revalidate serves the
// cached tile instantly AND refreshes it in the background, so a tile is at most one
// interaction behind and the map paints immediately. `Clear saved days` in the UI remains the
// manual escape hatch.
//
// PMTiles stays excluded and must stay excluded: those are Range requests answered with 206,
// which the Cache API cannot store (`cache.put` throws on a partial response).
const DYNAMIC_TILE_URL_PATTERNS = [
  'plantgeo-martin',
];

// Matches Martin's `{source}/{z}/{x}/{y}` shape so a custom domain or a renamed service still
// resolves. Source ids are the bare function names (`sensor_tiles`), and a composite is
// comma-joined, so commas are legal here. Anchored to the end to avoid matching a longer path.
const DYNAMIC_TILE_PATH = /\/[a-z0-9_,]+\/\d+\/\d+\/\d+(?:\.[a-z]+)?$/i;

const API_URL_PATTERNS = [
  '/api/',
  '/trpc/',
];

function isTileRequest(url) {
  return TILE_URL_PATTERNS.some(function(pattern) {
    return url.includes(pattern);
  });
}

function isApiRequest(url) {
  return API_URL_PATTERNS.some(function(pattern) {
    return url.includes(pattern);
  });
}

function isDynamicTileRequest(url) {
  var hostMatch = DYNAMIC_TILE_URL_PATTERNS.some(function(pattern) {
    return url.includes(pattern);
  });
  if (!hostMatch) return false;
  // PMTiles are served from a different host, but guard the shape anyway: a Range request
  // yields a 206 that cache.put() rejects, so it must never reach a caching strategy.
  if (url.indexOf('.pmtiles') !== -1) return false;
  try {
    return DYNAMIC_TILE_PATH.test(new URL(url).pathname);
  } catch (e) {
    return false;
  }
}

self.addEventListener('install', function(event) {
  event.waitUntil(
    caches.open(CACHE_NAME).then(function(cache) {
      return cache.addAll(APP_SHELL);
    }).then(function() {
      return self.skipWaiting();
    })
  );
});

self.addEventListener('activate', function(event) {
  event.waitUntil(
    caches.keys().then(function(cacheNames) {
      return Promise.all(
        cacheNames
          .filter(function(name) {
            return name.startsWith('plantgeo-') && name !== CACHE_NAME;
          })
          .map(function(name) {
            return caches.delete(name);
          })
      );
    }).then(function() {
      return self.clients.claim();
    })
  );
});

function cacheFirst(request) {
  return caches.open(CACHE_NAME).then(function(cache) {
    return cache.match(request).then(function(cached) {
      if (cached) {
        return cached;
      }
      return fetch(request).then(function(response) {
        // Only full 200 responses are cacheable; cache.put throws on 206.
        if (response.status === 200) {
          var copy = response.clone();
          cache.put(request, copy).then(evictIfNeeded);
        }
        return response;
      });
    });
  });
}

function networkFirst(request) {
  return fetch(request).then(function(response) {
    if (response.status === 200) {
      // Clone synchronously, before the page can consume the body.
      var copy = response.clone();
      caches.open(CACHE_NAME).then(function(cache) {
        cache.put(request, copy);
      });
    }
    return response;
  }).catch(function() {
    return caches.open(CACHE_NAME).then(function(cache) {
      return cache.match(request).then(function(cached) {
        return cached || Response.error();
      });
    });
  });
}

/**
 * Serve the cached tile immediately when there is one, and refresh it in the background.
 *
 * The `cached || network` return is the whole point: a hit resolves in ~0 ms without ever
 * waiting on Martin, while the network promise keeps running to update the cache for next
 * time. A miss falls back to the network exactly as before. `event.waitUntil` keeps the worker
 * alive for the background half, which otherwise gets killed once respondWith settles.
 */
function staleWhileRevalidate(request, event) {
  return caches.open(CACHE_NAME).then(function(cache) {
    return cache.match(request).then(function(cached) {
      var network = fetch(request).then(function(response) {
        // cache.put throws on 206; Martin answers whole tiles with 200, and 204 (no features
        // in this envelope) carries no body worth storing.
        if (response.status === 200) {
          cache.put(request, response.clone()).then(evictIfNeeded);
        }
        return response;
      }).catch(function() {
        // Offline or Martin down: the cached copy is the answer if we have one.
        return cached || Response.error();
      });
      if (event && cached) event.waitUntil(network);
      return cached || network;
    });
  });
}

function evictIfNeeded() {
  if (!navigator.storage || !navigator.storage.estimate) return;

  navigator.storage.estimate().then(function(estimate) {
    if (estimate.usage && estimate.usage > CACHE_SIZE_LIMIT_BYTES) {
      caches.open(CACHE_NAME).then(function(cache) {
        cache.keys().then(function(keys) {
          var toDelete = Math.ceil(keys.length * 0.1);
          var oldest = keys.slice(0, toDelete);
          return Promise.all(oldest.map(function(key) {
            return cache.delete(key);
          }));
        });
      });
    }
  });
}

self.addEventListener('fetch', function(event) {
  var url = event.request.url;

  if (event.request.method !== 'GET') return;

  // API traffic passes straight through -- freshness beats offline here.
  if (isApiRequest(url)) return;

  if (isTileRequest(url)) {
    event.respondWith(cacheFirst(event.request));
    return;
  }

  // Martin tiles: paint from cache now, refresh behind it. See DYNAMIC_TILE_URL_PATTERNS for
  // why this is neither cacheFirst (would pin stale tiles) nor networkFirst (waits on every
  // request even with a good copy in hand).
  if (isDynamicTileRequest(url)) {
    event.respondWith(staleWhileRevalidate(event.request, event));
    return;
  }

  event.respondWith(networkFirst(event.request));
});

self.addEventListener('message', function(event) {
  if (!event.data || typeof event.data !== 'object') return;

  if (event.data.type === 'PREFETCH_TILES' && Array.isArray(event.data.urls)) {
    var urls = event.data.urls;
    var total = urls.length;
    var completed = 0;

    caches.open(CACHE_NAME).then(function(cache) {
      function prefetchNext(index) {
        if (index >= total) {
          if (event.source && event.source.postMessage) {
            event.source.postMessage({ type: 'PREFETCH_COMPLETE', total: total });
          }
          return;
        }

        var url = urls[index];
        cache.match(url).then(function(existing) {
          if (existing) {
            completed++;
            if (event.source && event.source.postMessage) {
              event.source.postMessage({ type: 'PREFETCH_PROGRESS', completed: completed, total: total });
            }
            prefetchNext(index + 1);
          } else {
            fetch(url).then(function(response) {
              if (response.status === 200) {
                cache.put(url, response.clone());
              }
              completed++;
              if (event.source && event.source.postMessage) {
                event.source.postMessage({ type: 'PREFETCH_PROGRESS', completed: completed, total: total });
              }
              prefetchNext(index + 1);
            }).catch(function() {
              completed++;
              prefetchNext(index + 1);
            });
          }
        });
      }

      prefetchNext(0);
    });
  } else if (event.data.type === 'CLEAR_TILE_CACHE') {
    caches.delete(CACHE_NAME).then(function() {
      caches.open(CACHE_NAME).then(function(cache) {
        return cache.addAll(APP_SHELL);
      });
      if (event.source && event.source.postMessage) {
        event.source.postMessage({ type: 'CLEAR_CACHE_COMPLETE' });
      }
    });
  }
});

