// Fullscreen player: opened from the bar, mirrors the one <audio>, closes cleanly.
// No network, no media: playerAudio is a fixture and every fetch is blocked.
const fs = require('fs'), assert = require('assert'), { chromium } = require('playwright');
const html = fs.readFileSync('web/index.html', 'utf8'), style = require('./_css.cjs').appCss(), css = "";
const playerBar = html.slice(html.indexOf('<div id="player">'), html.indexOf('<audio id="player-audio"'));
const overlay = html.slice(html.indexOf('<div id="player-full"'), html.indexOf('<dialog id="dlg-path"'));
// The page script binds to containers this harness does not draw (the library list and its
// toolbar, the volume slot, the offline dialog). They must exist or the whole script stops
// evaluating before it wires the player.
const stubs = ['liblist', 'libsearch', 'libplaylist', 'libsort', 'libviews', 'shelfmode',
  'dlg-offline', 'offline-budget', 'offline-status', 'plvolslot', 'plvolbtn']
  .map(id => `<div id="${id}"></div>`).join('');
// The artwork helpers live above the playback block and the module is a separate file: both
// must exist before the page script's own `attach` runs.
const artHelpers = html.slice(html.indexOf('function artHQ('), html.indexOf('function artImg('));
const playbackModule = fs.readFileSync('web/js/playback.js', 'utf8');
const playerScript = html.slice(html.indexOf('// ------------------------------------------------------------------ playback'), html.indexOf('// ------------------------------------------------------------------ init'))
  // The real element is replaced by the fixture so nothing can play; keep one owner.
  .replace('const playerAudio = $("#player-audio");', 'const playerAudio = window.__fakeAudio;');

(async () => {
  const browser = await chromium.launch({ headless: true });
  try {
    for (const vp of [{ width: 390, height: 844 }, { width: 1280, height: 900 }]) {
      const page = await browser.newPage({ viewport: vp });
      let network = 0;
      await page.route('**/*', r => { network++; r.abort(); });
      await page.goto('about:blank');
      await page.setContent(`<style>${style}${css}</style>${playerBar}${overlay}${stubs}`);
      const boot = `
        const memory={};const localStorage={getItem:k=>memory[k]??null,setItem:(k,v)=>memory[k]=v};
        const SVG={play:s=>'<svg width="'+s+'" height="'+s+'"></svg>',pause:s=>'<svg width="'+s+'" height="'+s+'"></svg>',prev:()=>'<svg width="20" height="20"></svg>',next:()=>'<svg width="20" height="20"></svg>',shuffle:()=>'<svg width="16" height="16"></svg>',repeat:()=>'<svg width="16" height="16"></svg>',repeatOne:()=>'<svg width="16" height="16"></svg>',volume:()=>'<svg width="16" height="16"></svg>',mute:()=>'<svg width="16" height="16"></svg>'};
        function fmtClock(s){s=Math.max(0,Math.floor(s||0));return Math.floor(s/60)+':'+String(s%60).padStart(2,'0')}
        let libAll=[{id:7,title:'Hands All Over Me',artist_name:'Whales',album:'Monstercat',artwork:'/art.jpg',playable:true}],currentRows=libAll,libQueue=[],playingId=null,libIndex=-1,playbackQueueEdited=false;
        const calls=[];
        window.__fakeAudio={src:'',paused:true,duration:214,currentTime:42,volume:1,dataset:{},
          play(){this.paused=false;calls.push('play');return Promise.resolve()},pause(){this.paused=true;calls.push('pause')},
          load(){calls.push('load')},removeAttribute(n){this.src='';calls.push('removeAttribute')},
          addEventListener(n,f){(this.handlers||=[]).push([n,f])},dispatchEvent(e){(this.handlers||[]).filter(h=>h[0]===e.type).forEach(h=>h[1](e))}};
        const $=s=>document.querySelector(s);
        function renderLibrary(){} function commitPlaybackQueue(){} function showToast(){}
        function listeningSaveControl(){return ''}
        function rememberPlayed(){} function saveNowState(){}
        function autoDjBpm(){return 0} function autoDjMatch(){} function albumsOf(){return []} function paintPlaybackQueue(){}
        // The page warms covers and reports plays; this harness is about the element's lifecycle.
        window.Image=class{set src(v){}};
        window.fetch=()=>Promise.resolve({ok:true,headers:{get:()=>null},blob:()=>Promise.resolve(new Blob())});
        window.__calls=calls;
      `;
      // Concatenated, not interpolated: the player script has its own template literals.
      await page.addScriptTag({ content: artHelpers + playbackModule + boot + playerScript });
      const run = s => page.evaluate(s);
      // Nothing playing: the fullscreen view must refuse to open.
      await run(`openFullPlayer()`);
      assert.equal(await run(`getComputedStyle(document.querySelector('#player-full')).display`), 'none', 'opens with no track');

      await run(`playingId=7;document.querySelector('#player').classList.add('on');`);
      // The bar has no separate fullscreen button any more: the title/artist block is the door.
      await page.click('#plmeta');
      assert.equal(await run(`getComputedStyle(document.querySelector('#player-full')).display`), 'flex', 'did not open from the bar');
      const state = await run(`({
        title: document.querySelector('#pftitle').textContent,
        sub: document.querySelector('#pfsub').textContent,
        time: document.querySelector('#pftime').textContent + ' / ' + document.querySelector('#pfdur').textContent,
        seek: Math.round(Number(document.querySelector('#pfseek').value)),
        art: document.querySelector('#pfart').getAttribute('src'),
        covers: Math.round(document.querySelector('#player-full').getBoundingClientRect().width) === innerWidth,
        overflow: document.documentElement.scrollWidth > innerWidth,
        locked: getComputedStyle(document.body).overflow
      })`);
      assert.equal(state.title, 'Hands All Over Me');
      assert.equal(state.sub, 'Whales · Monstercat');
      assert.equal(state.time, '0:42 / 3:34');
      assert(Math.abs(state.seek - 196) <= 1, 'seek not positioned: ' + state.seek);
      assert.equal(state.art, '/art.jpg');
      assert(state.covers, 'overlay does not cover the viewport');
      assert(!state.overflow, 'horizontal overflow');
      assert.equal(state.locked, 'hidden');

      // The overlay drives the single audio element, not a second one.
      await page.click('#pfplay');
      assert.deepEqual(await run(`window.__calls`), ['play']);
      assert.equal(await run(`document.querySelectorAll('audio').length`), 0, 'the overlay must drive the one audio element, not add another');
      await page.click('#pfshuffle');
      assert.equal(await run(`JSON.stringify(plShuffle)`), 'true');
      assert(await run(`document.querySelector('#pfshuffle').classList.contains('on')`));
      await page.click('#pfrepeat');
      assert.equal(await run(`plRepeat`), 'all');
      assert(await run(`document.querySelector('#pfrepeat').classList.contains('on')`));
      await page.keyboard.press('Escape');
      assert.equal(await run(`getComputedStyle(document.querySelector('#player-full')).display`), 'none', 'Escape did not close');
      // The title in the bar opens it too (the "tab" a thumb reaches first).
      await page.click('#plmeta');
      assert.equal(await run(`getComputedStyle(document.querySelector('#player-full')).display`), 'flex');
      await page.click('#pfclose');
      assert.equal(await run(`getComputedStyle(document.querySelector('#player-full')).display`), 'none');

      // A song that ends is the ordinary case. Tearing the element down deletes the phone's
      // now-playing item, the head unit falls back to its source list, and the next play() can be
      // refused in the background — that is the car bug. A skip asked for by hand still resets,
      // because that reset is what stopped iOS freezing on the previous cover and title.
      await run(`libQueue=[...libAll,{id:8,title:'Second',artist_name:'Whales',album:'Monstercat',artwork:'/art2.jpg',playable:true}];
        playingId=7;libIndex=0;playerAudio.src='/api/track/7/audio';playerAudio.paused=false;window.__calls.length=0;`);
      await run(`stepTrack(1,'auto')`);
      assert.equal(await run(`window.__calls.filter(c=>c==='load').length`), 0,
        'a finished song must not tear the element down');
      assert.equal(await run(`playingId`), 8);
      await run(`stepTrack(1,'manual')`);
      assert.equal(await run(`window.__calls.filter(c=>c==='load').length`), 1,
        'a manual skip keeps the reset that fixed the stale tag');
      assert.equal(await run(`playingId`), 7);
      assert.equal(network, 0, 'the overlay must not fetch anything');
      await page.close();
      console.log(`Fullscreen player ${vp.width}: opens from bar button + title, mirrors metadata/time/seek, drives the one audio element, shuffle/repeat/queue controls, Escape/close, no overflow PASS`);
    }
  } finally { await browser.close(); }
})().catch(e => { console.error(e); process.exitCode = 1; });
