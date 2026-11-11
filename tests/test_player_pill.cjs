// Phone mini player: four named rows, a scrub you can actually see and drag.
const fs=require('fs'),assert=require('assert'),{chromium}=require('playwright');
const html=fs.readFileSync('web/index.html','utf8'),css=require('./_css.cjs').appCss(),prefs='';
const inlineStyle='';
const doc=html.replace(/<script[\s\S]*?<\/script>/g,'').replace(/<link[^>]*>/g,'').replace('</head>',`<style>${inlineStyle}${css}${prefs}</style></head>`);
(async()=>{const b=await chromium.launch({headless:true});try{
 for(const width of [390,430]){
  const p=await b.newPage({viewport:{width,height:820}});await p.route('**/*',r=>r.abort());
  await p.setContent(doc);
  await p.evaluate(()=>{
    document.querySelector('#player').classList.add('on');
    document.querySelector('#pltitle').textContent='Bassline Crusher';
    document.querySelector('#plsub').textContent='An Artist · An Album';
    document.querySelector('#pllyrics').disabled=false;
  });
  const m=await p.evaluate(()=>{
    const pl=document.querySelector('#player'), pr=pl.getBoundingClientRect();
    const kids=['#plartwrap','#plmeta','.pctrl','#pltime','#plseek','#pldur','.pvol'];
    const place=kids.map(s=>{const e=document.querySelector(s);return [s, getComputedStyle(e).gridArea];});
    const box=s=>document.querySelector(s).getBoundingClientRect();
    return {
      pill:+pr.height.toFixed(1),
      unplaced: place.filter(([,a])=>!a||a==='auto').map(([s])=>s),
      seekH:+box('#plseek').height.toFixed(1),
      seekW:+box('#plseek').width.toFixed(1),
      timeW:+box('#pltime').width.toFixed(1),
      durW:+box('#pldur').width.toFixed(1),
      order:{time:box('#pltime').left, seek:box('#plseek').left, dur:box('#pldur').left},
      rowsAligned: Math.abs(box('#pltime').top - box('#plseek').top) < 12 && Math.abs(box('#plseek').top - box('#pldur').top) < 12,
      mute:box('#plmute').left, vol:box('#plvol').left,
      // Lyrics is parked until the feature works: the button must not take space.
      lyricsHidden: getComputedStyle(document.querySelector('#pllyrics')).display === 'none'
        && box('#pllyrics').width === 0,
      overflow: document.documentElement.scrollWidth > innerWidth
    };
  });
  assert.deepEqual(m.unplaced, [], 'every child owns a named grid area: '+m.unplaced);
  assert(m.seekH >= 18, 'the scrub must be a full-height box, got '+m.seekH);
  assert(m.seekW > m.timeW + m.durW, 'the scrub must own the row');
  assert(m.durW < 70, 'the duration cell must not stretch across a shared column, got '+m.durW);
  assert(m.rowsAligned, 'time, scrub and duration share one row');
  assert(m.order.time < m.order.seek && m.order.seek < m.order.dur, '0:00 | scrub | 0:00');
  assert(m.mute < m.vol, 'mute comes before the volume slider');
  assert(m.lyricsHidden, 'the parked Lyrics button must render nothing');
  assert(!m.overflow, 'no horizontal overflow at '+width);

  // The "…" panel must sit above the player pill: raising the panel's own z-index is
  // not enough, because it is a child of the nav's stacking context.
  const stack=await p.evaluate(()=>{
    document.querySelector('#appnav').classList.add('more-open');
    const nav=document.querySelector('#appnav'), player=document.querySelector('#player');
    const navZ=Number(getComputedStyle(nav).zIndex)||0, playerZ=Number(getComputedStyle(player).zIndex)||0;
    const items=[...document.querySelectorAll('#navpanel .tab')].filter(el=>el.getBoundingClientRect().height>0);
    const hits=items.map(el=>{const r=el.getBoundingClientRect();
      const hit=document.elementFromPoint(r.left+r.width/2, r.top+r.height/2);
      return {label:el.textContent.trim(), inside: !!(hit && hit.closest('#navpanel'))};});
    document.querySelector('#appnav').classList.remove('more-open');
    return {navZ, playerZ, hits};
  });
  assert(stack.navZ > stack.playerZ, 'the dock must outrank the player pill: '+stack.navZ+' vs '+stack.playerZ);
  for (const hit of stack.hits) assert(hit.inside, 'the "…" panel item '+hit.label+' is covered by the player pill');
  await p.close();
 }
 console.log('Phone player pill: named rows, full-height scrub between both clocks, mute then volume, no parked Lyrics button, no overflow PASS');
}finally{await b.close();}})().catch(e=>{console.error(e);process.exitCode=1});
