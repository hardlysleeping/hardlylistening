'use strict';
const CACHE = 'hardlylistening-static-v9';
const AUDIO = 'hardlylistening-audio-v1';   // named in web/index.html as well
// The app's stylesheet is a tree (app.css imports each owner under @layer). Every file in it
// is precached, because the browser fetches each import as its own request: a missing entry
// would leave an offline page unstyled. Adding a stylesheet means adding it here.
const STYLES = [
  '/styles/app.css', '/styles/tokens.css', '/styles/base.css', '/styles/layout.css',
  '/styles/motion.css', '/styles/utilities.css',
  '/styles/components/app-chrome.css', '/styles/components/library-controls.css',
  '/styles/components/library-row.css', '/styles/components/library-cards.css',
  '/styles/components/social.css', '/styles/components/player.css',
  '/styles/components/playback-queue.css', '/styles/components/queue-view.css',
  '/styles/components/feed.css', '/styles/components/artist.css',
  '/styles/components/activity.css', '/styles/components/dialogs.css',
  '/styles/components/status.css', '/styles/components/preferences.css',
  '/styles/components/genres.css', '/styles/components/lyrics.css',
];
// Feature scripts the shell loads by URL. index.html is never cached (it is authenticated HTML),
// but a cached copy of its scripts is what lets a same-origin reload paint without the network.
const SCRIPTS = ['/js/playback-queue.js', '/js/playback.js', '/js/repo-discovery.js', '/js/social-feed.js', '/js/library.js'];
const STATIC = ['/offline.html','/lifecycle.js','/icon-192.png','/icon-512.png','/app.webmanifest',
                ...STYLES, ...SCRIPTS];
// The app shell and the last library listing (HL-309). Both are the signed-in member's own
// data on the device that asked for it — the same trust boundary as the kept audio — and
// both live in the versioned CACHE, so a deploy throws them away rather than serving an
// old shell over new scripts.
const LIBRARY_API = '/api/library';
self.addEventListener('install', event => {
  event.waitUntil(caches.open(CACHE).then(cache => cache.addAll(STATIC)));
});
self.addEventListener('install', event => { self.skipWaiting(); });
self.addEventListener('activate', event => {
  event.waitUntil(self.clients.claim());
  event.waitUntil(caches.keys().then(keys => Promise.all(keys.filter(key => key.startsWith('hardlylistening-static-') && key !== CACHE).map(key => caches.delete(key)))));
});

// ---------------------------------------------------------------- offline audio
// A song the listener has played is kept whole in the audio cache (web/index.html does the
// storing and the budget). The audio element always asks with a Range header, and a cached
// copy is one 200, so a range request is answered here with a 206 slice of it — that is what
// lets seeking work on a copy that never touches the network.
const AUDIO_PATH = /^\/api\/track\/\d+\/audio$/;
async function cachedAudio(request, url) {
  const cache = await caches.open(AUDIO);
  const hit = await cache.match(url.pathname);
  if (!hit) return fetch(request);          // not kept yet: stream it as before
  const blob = await hit.blob();
  const size = blob.size;
  const headers = new Headers({
    'Content-Type': hit.headers.get('Content-Type') || 'audio/mpeg',
    'Accept-Ranges': 'bytes',
    // The browser's own HTTP cache must not keep a second copy of this.
    'Cache-Control': 'no-store'
  });
  const range = request.headers.get('Range');
  if (!range) {
    headers.set('Content-Length', String(size));
    return new Response(blob, { status: 200, headers });
  }
  const match = /bytes=(\d*)-(\d*)/.exec(range);
  if (!match) return fetch(request);
  let start, end;
  if (!match[1]) {
    // "bytes=-500": the last 500 bytes. An empty range is not a range.
    const tail = Number(match[2] || 0);
    if (!tail) return fetch(request);
    start = Math.max(0, size - tail);
    end = size - 1;
  } else {
    start = Number(match[1]);
    end = match[2] ? Math.min(Number(match[2]), size - 1) : size - 1;
  }
  if (start >= size || start > end) {
    return new Response(null, { status: 416, headers: { 'Content-Range': `bytes */${size}` } });
  }
  const slice = blob.slice(start, end + 1);
  headers.set('Content-Range', `bytes ${start}-${end}/${size}`);
  headers.set('Content-Length', String(slice.size));
  return new Response(slice, { status: 206, headers });
}

// -------------------------------------------------------------- offline shell
// HTML is read twice: once to decide whether this is the app (the marker is on <html>),
// once to hand to the page. A clone is cheap; guessing from the status code is not, because
// a sign-in page is also a 200.
const SHELL_MARK = 'data-hl-shell';
async function appShell(request) {
  try {
    const response = await fetch(request);
    const kind = response.headers.get('Content-Type') || '';
    if (response.ok && !response.redirected && kind.includes('text/html')) {
      const body = await response.clone().text();
      if (body.includes(SHELL_MARK)) {
        const cache = await caches.open(CACHE);
        await cache.put('/', new Response(body, {
          headers: {'Content-Type': 'text/html; charset=utf-8'}}));
      }
    }
    return response;
  } catch (error) {
    const cached = await caches.match('/');
    if (cached) return cached;
    const note = await caches.match('/offline.html');
    return note || new Response('Offline', {status: 503,
      headers: {'Content-Type': 'text/plain; charset=utf-8'}});
  }
}

async function librarySnapshot(request) {
  try {
    const response = await fetch(request);
    const kind = response.headers.get('Content-Type') || '';
    if (response.ok && !response.redirected && kind.includes('application/json')) {
      const body = await response.clone().text();
      const cache = await caches.open(CACHE);
      await cache.put(LIBRARY_API, new Response(body, {
        headers: {'Content-Type': 'application/json; charset=utf-8'}}));
    }
    return response;
  } catch (error) {
    const cached = await caches.match(LIBRARY_API);
    if (cached) return cached;
    throw error;      // nothing kept yet: the page's own failure path is the honest answer
  }
}

self.addEventListener('fetch', event => {
  const request = event.request, url = new URL(request.url);
  // Versioned stylesheet URLs (`/styles/app.css?v=…`) must still be served from the cache by
  // path, so the query string is deliberately not a reason to skip this handler: every branch
  // below keys its cache lookup on url.pathname and leaves the network request intact.
  if (request.method !== 'GET' || url.origin !== self.location.origin) return;
  // A kept song is served from the copy, with Range support. Everything else about the
  // route — auth, headers, a missing file — stays the server's business.
  if (AUDIO_PATH.test(url.pathname)) {
    event.respondWith(cachedAudio(request, url));
    return;
  }
  // The entry navigation, network first: what the server just sent is what paints, and a
  // copy is kept only when it is the app itself. A redirect (to /login) or a page without
  // the shell marker is never stored, so a signed-out browser cannot overwrite the shell
  // with a sign-in form.
  if (request.mode === 'navigate' && (url.pathname === '/' || url.pathname === '/index.html')){
    event.respondWith(appShell(request));
    return;
  }
  // The library listing, so an installed app opens with something to look at. Other API
  // responses — credentials, member data, artwork — are deliberately not kept.
  if (url.pathname === LIBRARY_API && request.method === 'GET'){
    event.respondWith(librarySnapshot(request));
    return;
  }
  if (!STATIC.includes(url.pathname)) return;
  event.respondWith(fetch(request).catch(() => caches.match(url.pathname)));
});