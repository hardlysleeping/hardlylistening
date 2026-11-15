// throwaway: phone tap still plays once, long-press menu order
const http = require('http'), fs = require('fs'), path = require('path');
const { chromium } = require('playwright');
const WEB = path.join(__dirname, '..', 'web');
const rows = JSON.parse(fs.readFileSync('/tmp/opencode/library.json', 'utf8'));
const types = { '.html': 'text/html', '.css': 'text/css', '.js': 'text/javascript', '.svg': 'image/svg+xml' };
const server = http.createServer((req, res) => {
  const p = req.url.split('?')[0];
  if (p.startsWith('/api/')) return res.writeHead(200, { 'content-type': 'application/json' })
    .end(JSON.stringify({ tracks: [], all_tracks: [], playlists: [], playlist_tracks: [], folders: [], folder_tracks: [], items: {}, ok: true, stats: {}, viewer: {}, favorites: [], pins: [] }));
  const fp = path.join(WEB, p === '/' ? '/index.html' : p);
  if (fs.existsSync(fp) && fs.statSync(fp).isFile())
    return res.writeHead(200, { 'content-type': types[path.extname(fp)] || 'application/octet-stream' }).end(fs.readFileSync(fp));
  res.writeHead(404).end('no');
});
(async () => {
  await new Promise(r => server.listen(8514, r));
  const browser = await chromium.launch({ headless: true });
  const page = await browser.newPage({ viewport: { width: 390, height: 844 }, hasTouch: true, isMobile: true });
  page.on('pageerror', e => console.log('pageerror', String(e).slice(0, 200)));
  await page.goto('http://127.0.0.1:8514/', { waitUntil: 'load' });
  await page.waitForTimeout(700);
  await page.evaluate(data => {
    lib = data; libAll = data; currentRows = data; libQueue = []; detail = null; playingId = null;
    window.__plays = 0;
    const inner = playTrackNow;
    window.__orig = inner;
    libraryScope = 'shared'; showLibraryTab(); libView = 'songs'; libSongLayout = 'cards'; renderLibrary();
    // count playTrackNow calls without changing behaviour
    window.__count = 0;
    const real = playFromLibrary;
  }, rows);
  await page.evaluate(() => { window.playFromLibrary = function (id) { window.__count++; return undefined; }; });
  const card = page.locator('#liblist .songcard:not(.acard)').first();
  await card.locator('.st b').tap();
  await page.waitForTimeout(500);
  console.log('phone text tap -> playFromLibrary calls', await page.evaluate(() => window.__count));
  console.log('row markup layout', await page.evaluate(() => ({ cards: document.querySelectorAll('#liblist .songcard').length, table: document.querySelectorAll('#liblist .songrow:not(.songcard)').length })));

  // long press a card body
  await page.evaluate(() => document.querySelector('#liblist').scrollTo?.(0, 0));
  const box = await card.locator('.st b').boundingBox();
  await page.touchscreen.tap(box.x + 5, box.y + 5);
  await page.waitForTimeout(200);
  await page.evaluate(() => { const t = document.querySelector('#liblist .songcard:not(.acard) .st b'); const r = t.getBoundingClientRect();
    t.dispatchEvent(new PointerEvent('pointerdown', { bubbles: true, pointerType: 'touch', isPrimary: true, clientX: r.left + 5, clientY: r.top + 5 })); });
  await page.waitForTimeout(800);
  console.log('long-press menu', await page.evaluate(() => [...document.querySelectorAll('[role=menu] [role=menuitem]')].map(x => x.textContent)));
  await page.screenshot({ path: '/tmp/opencode/phone-menu.png' });
  await browser.close(); server.close();
})();