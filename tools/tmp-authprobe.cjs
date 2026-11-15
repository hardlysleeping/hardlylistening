'use strict';
const pw = require('playwright');
const { execFileSync } = require('child_process');

const APP = '/var/home/bazzite/apps/hardlylistening';
const sid = execFileSync('python3', ['-c', `
import hashlib,secrets,sqlite3,time
con=sqlite3.connect("${APP}/hardlylistening.db")
m=con.execute("select member_id from browser_sessions limit 1").fetchone()[0]
sid=secrets.token_urlsafe(32)
con.execute("insert into browser_sessions(digest,member_id,expires,auth_method) values(?,?,?,'login_link')",(hashlib.sha256(sid.encode()).hexdigest(),m,time.time()+900))
con.commit(); print(sid)
`], { encoding: 'utf8' }).trim();

(async () => {
  const browser = await pw.chromium.launch();
  const ctx = await browser.newContext();
  const page = await ctx.newPage();

  const a = await page.request.get('http://127.0.0.1:8470/api/state', { headers: { cookie: `__Host-hl_session=${sid}` } });
  console.log('APIRequestContext /api/state ->', a.status());

  await ctx.route('**/api/**', async route => {
    await route.continue({ headers: { ...route.request().headers(), cookie: `__Host-hl_session=${sid}` } });
  });
  await page.goto('http://127.0.0.1:8470/login', { waitUntil: 'domcontentloaded' });
  const st = await page.evaluate(async () => {
    const r = await fetch('/api/state', { cache: 'no-store', credentials: 'same-origin' });
    return r.status;
  });
  console.log('in-page fetch /api/state with route cookie ->', st);

  await browser.close();
  execFileSync('python3', ['-c', `
import hashlib,sqlite3
con=sqlite3.connect("${APP}/hardlylistening.db")
con.execute("delete from browser_sessions where digest=?",(hashlib.sha256(b"${sid}").hexdigest(),)); con.commit()
`]);
})().catch(e => { console.error(e); process.exit(1); });
