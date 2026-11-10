// Fullscreen vinyl-disc scrub: real handler block, fake audio, no media/HTTP.
const fs=require('fs'),vm=require('vm'),assert=require('assert');
const html=fs.readFileSync('web/index.html','utf8');
const start=html.indexOf('const pfDisc = $("#pf-disc");');
const endMarker='playerAudio.addEventListener(\'loadstart\'';
const end=html.indexOf(endMarker,start);
assert(start>0 && end>start, 'disc block located');
const src=html.slice(start,end);

function makeEl(id){
  const listeners={};
  return {
    id, value:'', textContent:'', style:{}, attrs:{},
    classList:{set:new Set(), add(c){this.set.add(c)}, remove(...c){c.forEach(x=>this.set.delete(x))}, contains(c){return this.set.has(c)}},
    setAttribute(k,v){this.attrs[k]=String(v)},
    addEventListener(type,fn){(listeners[type]=listeners[type]||[]).push(fn)},
    fire(type,event){for(const fn of listeners[type]||[])fn(event)},
    hasPointerCapture:()=>true, releasePointerCapture(){}, setPointerCapture(){},
    getBoundingClientRect:()=>({left:0,top:0,width:100,height:100,right:100,bottom:100})
  };
}
const els={'#pf-disc':makeEl('pf-disc'),'#pfseek':makeEl('pfseek'),'#pftime':makeEl('pftime'),'#pfdur':makeEl('pfdur')};
let paused=true, playCalls=0, pauseCalls=0;
const audio={duration:10,currentTime:0,get paused(){return paused},pause(){paused=true;pauseCalls++},play(){paused=false;playCalls++;return Promise.resolve()}};
const ctx=vm.createContext({
  $:id=>els[id], fmtClock:s=>'T'+Number(s).toFixed(2), playerAudio:audio,
  fullPlayerOpen:()=>true, performance:{now:()=>Number(ctx.__now||0)},
  getComputedStyle:()=>({transform:'none'}), console
});
vm.runInContext(src,ctx);
const disc=els['#pf-disc'];
const ev=(type,angle,extra={})=>Object.assign({button:0,pointerId:7,clientX:50+50*Math.cos(angle),clientY:50+50*Math.sin(angle),preventDefault(){},...extra},type?{type}:{});

// Start playing at 2s, grab the disc at 3 o'clock.
audio.currentTime=2; paused=false;
let t=0; ctx.__now=0;
disc.fire('pointerdown',ev('pointerdown',0));
assert.equal(paused,false,'a tap must not pause playback');
// Quarter turn clockwise = 3s further, well past the 60ms seek interval.
ctx.__now=100;
disc.fire('pointermove',ev('pointermove',Math.PI/2));
assert.equal(els['#pftime'].textContent,'T5.00','clock follows the finger on the first move');
assert.equal(Number(els['#pfseek'].value).toFixed(0),'500','seek bar follows the finger');
assert.equal(paused,true,'first real movement pauses so the seek is instant');
assert.equal(audio.currentTime,5,'audio committed the seek during the drag');
// A second move inside the throttle window still paints immediately.
ctx.__now=120;
disc.fire('pointermove',ev('pointermove',Math.PI));
assert.equal(els['#pftime'].textContent,'T8.00','readout stays live inside the throttle window');
assert.equal(audio.currentTime,5,'coalesced seek waits for the window');
// Wrapping past the seam must keep counting forward, not jump backwards.
ctx.__now=300;
disc.fire('pointermove',ev('pointermove',Math.PI+0.4));
const afterWrap=Number(els['#pfseek'].value);
assert(afterWrap>800,'seam crossing keeps moving forward');
disc.fire('pointerup',ev('pointerup',Math.PI+0.4,{button:0}));
assert.equal(playCalls,1,'resumes after a drag that started while playing');
const committed=audio.currentTime;
assert(Math.abs(committed-((Math.PI+0.4)*12/(2*Math.PI)+2))<0.001,'release commits the exact dragged position');
// Keyboard scrub repaints the readout too.
audio.currentTime=1;
disc.fire('keydown',{key:'ArrowRight',shiftKey:false,preventDefault(){}});
assert.equal(els['#pftime'].textContent,'T2.00','arrow key repaints the clock');
// Paused start: a drag must not start playback.
paused=true; audio.currentTime=4; ctx.__now=1000;
disc.fire('pointerdown',ev('pointerdown',0));
ctx.__now=1100; disc.fire('pointermove',ev('pointermove',Math.PI/2));
disc.fire('pointerup',ev('pointerup',Math.PI/2));
assert.equal(playCalls,1,'a paused track stays paused after scrubbing');
console.log('disc scrub: instant clock + seek bar on every move, coalesced audio seek, seam wrap, pause/resume, keyboard repaint PASS');
