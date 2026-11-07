// Cache-ahead: the next queued song is downloaded into a Blob so it starts without
// waiting for the network. Fixture audio only; nothing plays and no server is used.
const fs = require('fs'), assert = require('assert'), { chromium } = require('playwright');
const html = fs.readFileSync('web/index.html', 'utf8');
const playerBar = html.slice(html.indexOf('<div id="player">'), html.indexOf('<audio id="player-audio"'));
const script = html.slice(html.indexOf('// ------------------------------------------------------------------ playback'), html.indexOf('// ------------------------------------------------------------------ init'))
  .replace('const playerAudio = $("#player-audio");', 'const playerAudio = window.__fakeAudio;');

(async () => {
  const browser = await chromium.launch({ headless: true });
  try {
    const page = await browser.newPage({ viewport: { width: 390, height: 844 } });
    const errors = [];
    page.on('pageerror', e => errors.push(String(e)));
    await page.goto('about:blank');
    await page.setContent(`<div id="player"></div>${playerBar}`);
    const boot = `
      const memory={};const localStorage={getItem:k=>memory[k]??null,setItem:(k,v)=>memory[k]=v};
      const SVG={play:s=>'<svg width="'+s+'"></svg>',pause:s=>'<svg width="'+s+'"></svg>',prev:()=>'<svg width="16"></svg>',next:()=>'<svg width="16"></svg>',shuffle:()=>'<svg width="16"></svg>',repeat:()=>'<svg width="16"></svg>',repeatOne:()=>'<svg width="16"></svg>',volume:()=>'<svg width="16"></svg>',mute:()=>'<svg width="16"></svg>'};
      function fmtClock(s){s=Math.max(0,Math.floor(s||0));return Math.floor(s/60)+':'+String(s%60).padStart(2,'0')}
      const el=()=>({value:'',innerHTML:'',textContent:'',dataset:{},style:{},
        classList:{toggle(){},add(){},remove(){},contains(){return false}},
        addEventListener(){},setAttribute(){},querySelectorAll:()=>[]});
      const $=s=>document.querySelector(s)||el();
      function renderLibrary(){} function commitPlaybackQueue(){} function showToast(){} function paintPlayerModes(){}
      let libSort='recent',libSortDir='asc',libView='songs',libraryScope='shared',libPlaylists=[],detail=null,renderedAlbums=[];
      function setView(){} function refresh(){}
      let libAll=[1,2,3,4].map(id=>({id,title:'Song '+id,artist_name:'A',album:'B',artwork:'',playable:true})),currentRows=libAll,libQueue=[],playingId=null,libIndex=-1,playbackQueueEdited=false;
      window.__fetches=[];
      window.fetch=async url=>{window.__fetches.push(String(url));return {ok:true,blob:async()=>new Blob([new Uint8Array(20000)],{type:'audio/mpeg'})};};
      window.__fakeAudio={src:'',paused:true,duration:200,currentTime:0,volume:1,dataset:{},handlers:{},
        play(){this.paused=false;return Promise.resolve()},pause(){this.paused=true},
        addEventListener(n,f){(this.handlers[n]??=[]).push(f)},
        fire(n){(this.handlers[n]||[]).forEach(f=>f())},
        removeAttribute(){this.src='';},load(){}};
    `;
    await page.addScriptTag({ content: boot + script });
    const run = s => page.evaluate(s);

    // Playing track 1 must fetch track 2 ahead — and nothing else.
    await run(`libQueue=[1,2,3,4].map(id=>libAll.find(t=>t.id===id));libIndex=0;playFromLibrary(1)`);
    // The prefetch waits for the current song to be playable, so the two never race.
    await page.waitForTimeout(120);
    assert.deepEqual(await run(`window.__fetches.slice()`), [], 'nothing is cached before the current track can play');
    await run(`playerAudio.fire('canplay')`);
    await page.waitForTimeout(120);
    let fetches = await run(`window.__fetches.slice()`);
    assert.deepEqual(fetches, ['/api/track/2/audio'], 'only the next queued track is cached ahead: ' + fetches.join());
    assert.equal(await run(`playerAudio.src`), '/api/track/1/audio', 'the first track still streams');

    // The cached blob exists, and stepping forward uses it with no further request.
    const cached = await run(`aheadAudio.has(2) && aheadAudio.get(2).slice(0,5)`);
    assert.equal(cached, 'blob:', 'track 2 was not held as a Blob');
    await run(`stepTrack(1)`);
    await run(`playerAudio.fire('canplay')`);
    await page.waitForTimeout(120);
    assert.equal(await run(`playerAudio.src.slice(0,5)`), 'blob:', 'the prefetched track played from the network instead of the cache');
    assert.equal(await run(`playerAudio.dataset.ahead`), '1');
    fetches = await run(`window.__fetches.slice()`);
    assert.deepEqual(fetches, ['/api/track/2/audio', '/api/track/3/audio'], 'a cached track must not be refetched, and the new next one is cached: ' + fetches.join());

    // At most two blobs are kept, and shuffle (unknown next) stops prefetching.
    assert(await run(`aheadAudio.size <= 2`), 'cache grew past its bound');
    const before = await run(`window.__fetches.length`);
    await run(`plShuffle=true;prefetchAhead()`);
    await page.waitForTimeout(80);
    assert.equal(await run(`window.__fetches.length`), before, 'shuffle must not prefetch a track it cannot predict');

    // A link that leaves the queue is not cached: the last track has nothing after it.
    await run(`plShuffle=false;libIndex=3;playFromLibrary(4)`);
    await page.waitForTimeout(120);
    assert(!(await run(`window.__fetches.slice(-1)[0].includes('/api/track/')&&window.__fetches.slice(-1)[0].startsWith('/api/track/5')`)), 'no phantom cache of a missing track');

    assert.deepEqual(errors, [], 'page errors: ' + errors.join('; '));
    await page.close();
    console.log('Cache ahead: next queued track fetched once, played from Blob with no refetch, bounded to two blobs, shuffle and queue end skipped PASS');
  } finally { await browser.close(); }
})().catch(e => { console.error(e); process.exitCode = 1; });
