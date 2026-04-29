/* Saga service worker.
 *
 * Strategy:
 *   - Pre-cache the app shell (HTML/CSS/JS/icons) on install.
 *   - Network-first for /api/* — keep data fresh, fall back to cache only
 *     when offline so the UI still loads with the last-known shelf.
 *   - Cache-first for static assets and previously-fetched audio mp3s.
 *   - Audio is cached opportunistically as the user plays it; cap audio
 *     cache so we don't fill the device.
 */

const VERSION = 'saga-v2';
const SHELL_CACHE  = `${VERSION}-shell`;
const STATIC_CACHE = `${VERSION}-static`;
const API_CACHE    = `${VERSION}-api`;
const AUDIO_CACHE  = `${VERSION}-audio`;

const SHELL_ASSETS = [
    '/',
    '/static/shared.js',
    '/static/book-reader.js',
    '/static/css/books.css',
    '/static/css/luminance.css',
    '/static/manifest.webmanifest',
    '/static/pwa/icon-192.svg',
    '/static/pwa/icon-512.svg',
];

const AUDIO_CACHE_MAX_ENTRIES = 60;

self.addEventListener('install', (event) => {
    event.waitUntil(
        caches.open(SHELL_CACHE).then((cache) => cache.addAll(SHELL_ASSETS))
            .then(() => self.skipWaiting())
    );
});

self.addEventListener('activate', (event) => {
    event.waitUntil(
        caches.keys().then((keys) => Promise.all(
            keys
                .filter((k) => !k.startsWith(VERSION))
                .map((k) => caches.delete(k))
        )).then(() => self.clients.claim())
    );
});

async function trimCache(cacheName, maxEntries) {
    const cache = await caches.open(cacheName);
    const keys = await cache.keys();
    if (keys.length <= maxEntries) return;
    const toDelete = keys.slice(0, keys.length - maxEntries);
    await Promise.all(toDelete.map((req) => cache.delete(req)));
}

async function networkFirstApi(request) {
    const cache = await caches.open(API_CACHE);
    try {
        const fresh = await fetch(request);
        if (fresh && fresh.ok && request.method === 'GET') {
            cache.put(request, fresh.clone()).catch(() => {});
        }
        return fresh;
    } catch (e) {
        const cached = await cache.match(request);
        if (cached) return cached;
        return new Response(JSON.stringify({ offline: true, error: String(e) }), {
            status: 503,
            headers: { 'Content-Type': 'application/json' }
        });
    }
}

async function staleWhileRevalidateStatic(request) {
    const cache = await caches.open(STATIC_CACHE);
    const cached = await cache.match(request);
    // Always kick off a network fetch in the background to refresh the
    // cache, so the next visit gets the new code without needing the user
    // to manually clear the cache. Cache-first alone causes JS/CSS edits
    // to never reach the browser until the SW VERSION is bumped.
    const networkPromise = fetch(request).then((fresh) => {
        if (fresh && fresh.ok) cache.put(request, fresh.clone()).catch(() => {});
        return fresh;
    }).catch(() => null);
    return cached || networkPromise;
}

async function audioCachedFirst(request) {
    const cache = await caches.open(AUDIO_CACHE);
    const cached = await cache.match(request);
    if (cached) return cached;
    const fresh = await fetch(request);
    if (fresh && fresh.ok) {
        cache.put(request, fresh.clone())
            .then(() => trimCache(AUDIO_CACHE, AUDIO_CACHE_MAX_ENTRIES))
            .catch(() => {});
    }
    return fresh;
}

self.addEventListener('fetch', (event) => {
    const req = event.request;
    if (req.method !== 'GET') return;
    const url = new URL(req.url);

    // Audio mp3 endpoints
    if (url.pathname.startsWith('/api/books/') && url.pathname.includes('/audio')) {
        event.respondWith(audioCachedFirst(req));
        return;
    }
    // Other API: network-first
    if (url.pathname.startsWith('/api/')) {
        event.respondWith(networkFirstApi(req));
        return;
    }
    // Static assets — stale-while-revalidate so updates apply on next visit
    if (url.pathname.startsWith('/static/')) {
        event.respondWith(staleWhileRevalidateStatic(req));
        return;
    }
    // App shell — try network, then cached '/'
    if (req.mode === 'navigate') {
        event.respondWith(
            fetch(req).catch(() => caches.match('/'))
        );
        return;
    }
});

// Allow the page to instruct the worker (clear caches on user demand)
self.addEventListener('message', (event) => {
    if (event.data && event.data.type === 'clear-audio-cache') {
        caches.delete(AUDIO_CACHE).then(() => {
            event.source && event.source.postMessage({ type: 'audio-cache-cleared' });
        });
    }
});
