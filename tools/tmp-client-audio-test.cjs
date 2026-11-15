'use strict';
// Measures the real player: does an <audio> pointed at the app's stream run past 29 s,
// and does the app's own playerAudio behave differently? Auth is provided by proxying
// every request through APIRequestContext (Chromium forbids a Cookie header on routes).
const pw = require('playwright');
const { execFileSync } = require('child_process');

const APP = '/var/home/bazzite/apps/hardlylistening';
const TRACK = process.argv[2] || '1330';
const SECONDS = Number(process.argv[3] || 40);

const sid = execFileSync('python3', ['-c', `
import hashlib,secrets,sqlite3,time
con=sqlite3.connect("${APP}/hardlylistening.db")
m=con.execute("select member_id from browser_sessions limit 1").fetchone()[0]
sid=secrets.token_urlsafe(32)
con.execute("insert into browser_sessions(digest,member_id,expires,auth_method) values(?,?,?,'login_link')",(hashlib.sha256(sid.encode()).hexdigest(),m,time.time()+900))
con.commit(); print(sid)
`], { encoding: 'utf8' }).trim();

(async () => {
  const browser = await pw.chromium.launch({ args: ['--autoplay-policy=no-user-gesture-required'] });
  const ctx = await browser.newContext();
  const page = await ctx.newPage();
  page.on('pageerror', e => console.log('pageerror:', String(e).slice(0, 160)));

  await ctx.route('**/*', async route => {
    const req = route.request();
    const out = { cookie: `__Host-hl_session=${sid}` };
    const h = req.headers();
    for (const key of ['range', 'content-type', 'accept']) if (h[key]) out[key] = h[key];
    try {
      const resp = await page.request.fetch(req.url(), {
        method: req.method(), headers: out, data: req.postDataBuffer() || undefined,
        failOnStatusCode: false, timeout: 30000
      });
      const body = await resp.body();
      await route.fulfill({ status: resp.status(), headers: resp.headers(), body });
    } catch (e) { await route.abort(); }
  });

  await page.goto('http://127.0.0.1:8470/', { waitUntil: 'domcontentloaded', timeout: 40000 });
  await page.waitForTimeout(4000);
  console.log('landed on:', page.url());

  const res = await page.evaluate(async ({ track, seconds }) => {
    const a = new Audio(`/api/track/${track}/audio`);
    const log = [];
    const t0 = performance.now();
    for (const e of ['loadedmetadata', 'canplay', 'play', 'pause', 'ended', 'error', 'stalled', 'waiting', 'suspend', 'abort', 'seeked'])
      a.addEventListener(e, () => log.push({ ev: e, at: +(performance.now() - t0).toFixed(1), ct: +a.currentTime.toFixed(2), dur: a.duration, ready: a.readyState }));
    a.volume = 0;
    const played = await a.play().then(() => 'started').catch(e => 'blocked: ' + e.name);
    await new Promise(r => setTimeout(r, seconds * 1000));
    const out = { played, log, final: { ct: +a.currentTime.toFixed(2), dur: a.duration, paused: a.paused, ended: a.ended } };
    a.pause();
    return out;
  }, { track: TRACK, seconds: SECONDS });

  console.log(JSON.stringify(res, null, 1));
  await browser.close();
  execFileSync('python3', ['-c', `
import hashlib,sqlite3
con=sqlite3.connect("${APP}/hardlylistening.db")
con.execute("delete from browser_sessions where digest=?",(hashlib.sha256(b"${sid}").hexdigest(),)); con.commit()
`]);
  console.log('temp session removed');
})().catch(e => { console.error('FAIL', e); process.exit(1); });
