'use strict';
// Why does the record play the synthetic noise instead of the track's own audio?
// Loads the real shell, plays a track, opens the full player and runs pfScratchPrepare().
const pw = require('playwright');
const { execFileSync } = require('child_process');
const APP = '/var/home/bazzite/apps/hardlylistening';
const TRACK = process.argv[2] || '1330';

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
  const logs = [];
  page.on('console', m => logs.push(m.type() + ': ' + m.text().slice(0, 220)));
  page.on('pageerror', e => logs.push('pageerror: ' + String(e).slice(0, 200)));

  await ctx.route('**/*', async route => {
    const req = route.request();
    const h = { cookie: `__Host-hl_session=${sid}` };
    for (const k of ['range', 'content-type', 'accept']) {
      const v = req.headers()[k];
      if (v) h[k] = v;
    }
    try {
      const resp = await page.request.fetch(req.url(), {
        method: req.method(), headers: h, data: req.postDataBuffer() || undefined,
        failOnStatusCode: false, timeout: 40000
      });
      await route.fulfill({ status: resp.status(), headers: resp.headers(), body: await resp.body() });
    } catch (e) { await route.abort(); }
  });

  await page.goto('http://127.0.0.1:8470/', { waitUntil: 'domcontentloaded', timeout: 40000 });
  await page.waitForTimeout(4000);

  const res = await page.evaluate(async (track) => {
    const out = {};
    out.hasPlay = typeof playFromLibrary === 'function';
    if (typeof playFromLibrary === 'function') playFromLibrary(track);
    await new Promise(r => setTimeout(r, 2500));
    out.src = (document.querySelector('#player-audio') || {}).src || null;
    out.duration = (document.querySelector('#player-audio') || {}).duration || null;
    out.playingId = typeof playingId !== 'undefined' ? playingId : 'n/a';
    if (typeof openFullPlayer === 'function') openFullPlayer();
    await new Promise(r => setTimeout(r, 300));
    out.scratchBefore = { track: typeof pfScratchTrack !== 'undefined' ? pfScratchTrack : 'x',
                          buffer: typeof pfScratchBuffer !== 'undefined' ? !!pfScratchBuffer : 'x',
                          loading: typeof pfScratchLoading !== 'undefined' ? !!pfScratchLoading : 'x' };
    if (typeof pfScratchPrepare === 'function') pfScratchPrepare();
    await new Promise(r => setTimeout(r, 8000));
    out.scratchAfter = { track: typeof pfScratchTrack !== 'undefined' ? pfScratchTrack : 'x',
                          buffer: typeof pfScratchBuffer !== 'undefined' ? !!pfScratchBuffer : 'x',
                          len: typeof pfScratchBuffer !== 'undefined' && pfScratchBuffer ? pfScratchBuffer.duration : null,
                          loading: typeof pfScratchLoading !== 'undefined' ? !!pfScratchLoading : 'x' };
    const card = document.querySelector('#pf-disc');
    const r = card ? card.getBoundingClientRect() : null;
    out.disc = r ? { w: Math.round(r.width), h: Math.round(r.height) } : null;
    return out;
  }, TRACK);

  console.log(JSON.stringify(res, null, 1));
  console.log('--- console lines mentioning scratch/noise/audio/decode:');
  for (const l of logs.filter(x => /scratch|noise|decode|audio|error|warn/i.test(x)).slice(-20)) console.log('  ' + l);
  await browser.close();
  execFileSync('python3', ['-c', `
import hashlib,sqlite3
con=sqlite3.connect("${APP}/hardlylistening.db")
con.execute("delete from browser_sessions where digest=?",(hashlib.sha256(b"${sid}").hexdigest(),)); con.commit()
`]);
})().catch(e => { console.error('FAIL', e); process.exit(1); });
