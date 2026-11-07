'use strict';
const CACHE = 'hardlylistening-static-v1';
const STATIC = ['/offline.html','/polish.css','/lifecycle.js','/icon-192.png','/icon-512.png','/app.webmanifest'];
self.addEventListener('install', event => {
  event.waitUntil(caches.open(CACHE).then(cache => cache.addAll(STATIC)));
});
self.addEventListener('activate', event => {
  event.waitUntil(caches.keys().then(keys => Promise.all(keys.filter(key => key.startsWith('hardlylistening-static-') && key !== CACHE).map(key => caches.delete(key)))));
});
self.addEventListener('fetch', event => {
  const request = event.request, url = new URL(request.url);
  if (request.method !== 'GET' || url.origin !== self.location.origin || url.search) return;
  // Only the app's entry navigation gets an informational network-failure page.
  // Never store authenticated HTML, login/invite pages, API, audio or credentials.
  if (request.mode === 'navigate' && (url.pathname === '/' || url.pathname === '/index.html')){
    event.respondWith(fetch(request).catch(() => caches.match('/offline.html')));
    return;
  }
  if (!STATIC.includes(url.pathname)) return;
  event.respondWith(fetch(request).catch(() => caches.match(url.pathname)));
});
