// static/sw.js

const CACHE_NAME = 'futurapredict-v1.0.0';
const OFFLINE_URL = '/offline/';
const STATIC_ASSETS = [
    '/',
    '/static/css/main.css',
    '/static/css/dark-mode.css',
    '/static/css/animations.css',
    '/static/js/main.js',
    '/static/js/pwa.js',
    '/static/sw.js',
];

// Install event
self.addEventListener('install', event => {
    event.waitUntil(
        caches.open(CACHE_NAME)
            .then(cache => {
                return cache.addAll(STATIC_ASSETS).catch(err => {
                    console.log('Cache addAll error:', err);
                    return cache.addAll(STATIC_ASSETS.filter(asset => asset !== '/offline/'));
                });
            })
            .then(() => self.skipWaiting())
    );
});

// Activate event
self.addEventListener('activate', event => {
    event.waitUntil(
        caches.keys().then(cacheNames => {
            return Promise.all(
                cacheNames
                    .filter(cacheName => cacheName !== CACHE_NAME)
                    .map(cacheName => caches.delete(cacheName))
            );
        }).then(() => self.clients.claim())
    );
});

// Fetch event
self.addEventListener('fetch', event => {
    // Skip non-GET requests
    if (event.request.method !== 'GET') return;
    
    // Skip chrome-extension requests
    if (event.request.url.includes('chrome-extension')) return;
    
    // For API calls, use network-first strategy
    if (event.request.url.includes('/api/')) {
        event.respondWith(networkFirst(event.request));
        return;
    }
    
    // For static assets, use cache-first strategy
    event.respondWith(cacheFirst(event.request));
});

// Network first strategy
function networkFirst(request) {
    return fetch(request)
        .then(response => {
            if (!response || response.status !== 200) {
                return response;
            }
            
            const responseToCache = response.clone();
            caches.open(CACHE_NAME).then(cache => {
                cache.put(request, responseToCache);
            });
            
            return response;
        })
        .catch(() => {
            return caches.match(request)
                .then(response => response || createOfflineResponse());
        });
}

// Cache first strategy
function cacheFirst(request) {
    return caches.match(request)
        .then(cachedResponse => {
            if (cachedResponse) {
                return cachedResponse;
            }
            
            return fetch(request)
                .then(response => {
                    if (!response || response.status !== 200 || response.type !== 'basic') {
                        return response;
                    }
                    
                    const responseToCache = response.clone();
                    caches.open(CACHE_NAME).then(cache => {
                        cache.put(request, responseToCache);
                    });
                    
                    return response;
                })
                .catch(() => {
                    if (request.mode === 'navigate') {
                        return caches.match(OFFLINE_URL);
                    }
                });
        });
}

function createOfflineResponse() {
    return new Response(
        '<h1>You are offline</h1><p>Please check your internet connection.</p>',
        { status: 503, statusText: 'Service Unavailable', headers: new Headers({ 'Content-Type': 'text/html' }) }
    );
}

// Push notification event
self.addEventListener('push', event => {
    let data = { title: 'FuturaPredict', body: 'New prediction available!' };
    
    if (event.data) {
        try {
            data = event.data.json();
        } catch (e) {
            data.body = event.data.text();
        }
    }
    
    const options = {
        body: data.body,
        icon: '/static/images/icons/icon-192x192.png',
        badge: '/static/images/icons/badge-72x72.png',
        vibrate: [100, 50, 100],
        data: { url: data.url || '/' },
        actions: [
            {
                action: 'open',
                title: 'Open'
            },
            {
                action: 'close',
                title: 'Close'
            }
        ]
    };
    
    event.waitUntil(
        self.registration.showNotification(data.title, options)
    );
});

// Notification click event
self.addEventListener('notificationclick', event => {
    event.notification.close();
    
    if (event.action === 'open' || !event.action) {
        const url = event.notification.data.url || '/';
        event.waitUntil(
            clients.matchAll({ type: 'window' }).then(clientList => {
                for (let client of clientList) {
                    if (client.url === url && 'focus' in client) {
                        return client.focus();
                    }
                }
                if (clients.openWindow) {
                    return clients.openWindow(url);
                }
            })
        );
    }
});