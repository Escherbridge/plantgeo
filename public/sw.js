// v2: purge v1 caches that trapped pre-launch API responses; API requests are
// now network-only (never cached) so data can never go stale behind the SW.
const CACHE_VERSION = 'v2';
const CACHE_NAME = 'plantgeo-' + CACHE_VERSION;
const CACHE_SIZE_LIMIT_BYTES = 500 * 1024 * 1024; // 500MB

const APP_SHELL = [
  '/',
  '/manifest.webmanifest',
];

// Static raster/vector tile hosts only. Dynamic Martin tiles and the PMTiles
// archive are excluded: Martin data changes between cron runs, and PMTiles
// range requests return 206 responses the Cache API cannot store.
const TILE_URL_PATTERNS = [
  'arcgisonline.com',
  'elevation-tiles-prod',
  'basemaps-assets',
];

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

  event.respondWith(networkFirst(event.request));
});
