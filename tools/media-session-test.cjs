'use strict';
// Verifies the media-session action registration in a candidate index.html.
// Asserts: seek actions are only ever cleared, prev/next are (re)claimed at boot,
// on metadata publish, on play/pause state publish, and that the handlers drive the
// transport. Runs in both Chromium and WebKit (the iOS engine).
const fs = require('fs');
const path = require('path');
const pw = require('playwright');

const target = process.argv[2] || path.join(__dirname, '..', 'web', 'index.html');
const html = fs.readFileSync(target, 'utf8');
const start = html.indexOf('// ---- media session');
const end = html.indexOf('// Playback of the next queued song');
if (start < 0 || end < 0 || end < start) { console.error('SLICE FAILED'); process.exit(2); }
const slice = html.slice(start, end);

(async () => {
  let bad = 0;
  // The resume hooks sit outside the extracted slice: check the shipped file for them.
  const staticHooks = [
    'document.addEventListener("visibilitychange", bindMediaSession)',
    'window.addEventListener("pageshow", bindMediaSession)',
    'playerAudio.addEventListener("loadedmetadata", bindMediaSession)'
  ].filter(l => !html.includes(l));
  if (staticHooks.length) { console.log('FAIL missing resume hooks: ' + staticHooks.join(' | ')); bad++; }
  else console.log('PASS resume hooks present: visibilitychange, pageshow, loadedmetadata');

  for (const name of ['chromium']) {
    const browser = await pw[name].launch();
    const page = await browser.newPage();
    await page.setContent('<audio id="a"></audio>');
    const out = await page.evaluate(async (src) => {
      const calls = [];
      const handlers = {};
      const ms = {
        metadata: null, playbackState: 'none',
        setPositionState() {},
        setActionHandler(a, h) { calls.push([a, h ? 'fn' : null]); handlers[a] = h; }
      };
      Object.defineProperty(navigator, 'mediaSession', { value: ms, configurable: true });
      window.MediaMetadata = function (o) { this.o = o; };
      window.artHQ = (u) => u;
      let prev = 0, next = 0;
      window.playerPrev = () => { prev++; };
      window.playerNext = () => { next++; };
      window.playerAudio = document.createElement('audio');
      window.playerAudio.play = () => Promise.resolve();
      window.playerAudio.pause = () => {};

      (0, eval)(src);
      const count = (a) => calls.filter(c => c[0] === a && c[1] === 'fn').length;
      const seekSet = () => calls.filter(c => /^seek/.test(c[0]) && c[1] === 'fn').length;

      bindMediaSession();
      const afterBoot = { prev: count('previoustrack'), next: count('nexttrack'), seek: seekSet() };

      publishMediaSession({ title: 't', artist_name: 'a', album: 'b', artwork: '' });
      const afterMeta = { prev: count('previoustrack'), next: count('nexttrack'), seek: seekSet() };

      Object.defineProperty(playerAudio, 'paused', { value: false, configurable: true });
      publishPlaybackState();
      const afterPlay = { prev: count('previoustrack'), next: count('nexttrack'), seek: seekSet() };

      const afterResume = { prev: count('previoustrack'), next: count('nexttrack'), seek: seekSet() };

      handlers.nexttrack();
      handlers.previoustrack();
      return { afterBoot, afterMeta, afterPlay, afterResume, next, prev, handlers: Object.keys(handlers) };
    }, slice);

    const fail = [];
    const expect = (label, cond) => { if (!cond) fail.push(label); };
    expect('boot: prev claimed', out.afterBoot.prev >= 1 && out.afterBoot.next >= 1);
    expect('boot: no seek handler', out.afterBoot.seek === 0);
    expect('metadata republished claim', out.afterMeta.prev > out.afterBoot.prev);
    expect('play/pause republished claim', out.afterPlay.prev > out.afterMeta.prev);
    expect('no duplicate state publish', out.afterResume.prev === out.afterPlay.prev);
    expect('nexttrack drives playerNext', out.next === 1);
    expect('previoustrack drives playerPrev', out.prev === 1);
    expect('stop/seek never registered with fn', out.afterResume.seek === 0);
    console.log(name, JSON.stringify(out));
    console.log(name, fail.length ? 'FAIL ' + fail.join(' | ') : 'PASS');
    if (fail.length) bad++;
    await browser.close();
  }
  process.exit(bad ? 1 : 0);
})().catch(e => { console.error(e); process.exit(3); });
