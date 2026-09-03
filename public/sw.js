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
  } catch (_e) {
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
 * Delete cached dynamic-tile entries, optionally narrowed to one Martin source id.
 *
 * This is the ONLY thing that causes a dynamic tile to be fetched twice. See the routing
 * comment in the fetch handler for why refresh is consumer-driven rather than automatic.
 * Returns the number of entries dropped so a caller can report it.
 */
function refreshDynamicTiles(sourceId) {
  return caches.open(CACHE_NAME).then(function(cache) {
    return cache.keys().then(function(keys) {
      var doomed = keys.filter(function(request) {
        if (!isDynamicTileRequest(request.url)) return false;
        if (!sourceId) return true;
        // Match the source segment exactly, so "sensor_tiles" never sweeps away a
        // hypothetical "sensor_tiles_v2", and a comma-joined composite still matches.
        try {
          var segments = new URL(request.url).pathname.split('/').filter(Boolean);
          // Martin path is {source}/{z}/{x}/{y}; the source sits 4 from the end.
          var source = segments[segments.length - 4];
          if (!source) return false;
          return source.split(',').indexOf(sourceId) !== -1;
        } catch (_e) {
          return false;
        }
      });
      return Promise.all(doomed.map(function(request) {
        return cache.delete(request);
      })).then(function() {
        return doomed.length;
      });
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

  // Martin tiles: cache-first, and a hit does NOT trigger a background refresh.
  //
  // Refetching is consumer-driven on purpose. A background revalidate on every hit paints
  // instantly but leaves origin load exactly where it was -- the map still issues one Martin
  // request per tile per view, which is the cost this cache exists to remove. Since the whole
  // point is "pull only what is missing or new", the cache never decides on its own that a
  // tile is stale; a consumer says so by posting REFRESH_DYNAMIC_TILES.
  //
  // What makes cache-first safe here: a tile's bytes do not vary with the selected date. No
  // tile function has a date predicate -- day filtering happens client-side as a MapLibre
  // style filter over the `observed_day` MVT attribute -- so moving the time slider needs no
  // refetch at all, and a cached tile already carries every day it will ever be asked for.
  // Only an ingest run landing NEW features changes what a tile should contain, and that is
  // exactly the moment a consumer should ask for a refresh.
  if (isDynamicTileRequest(url)) {
    event.respondWith(cacheFirst(event.request));
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
  } else if (event.data.type === 'REFRESH_DYNAMIC_TILES') {
    // Consumer-driven tile invalidation: the only path that re-fetches a cached Martin tile.
    // Pass `sourceId` (e.g. "sensor_tiles") to drop one layer, or omit it to drop all dynamic
    // tiles while leaving prefetched basemap tiles and the app shell alone -- which is what
    // separates this from CLEAR_TILE_CACHE below.
    refreshDynamicTiles(event.data.sourceId || null).then(function(dropped) {
      if (event.source && event.source.postMessage) {
        event.source.postMessage({
          type: 'REFRESH_DYNAMIC_TILES_COMPLETE',
          sourceId: event.data.sourceId || null,
          dropped: dropped,
        });
      }
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

