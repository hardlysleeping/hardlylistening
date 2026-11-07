const fs=require('fs'),vm=require('vm'),assert=require('assert');
const {JSDOM}=require('jsdom');
const html=fs.readFileSync('web/index.html','utf8');
const lifecycle=fs.readFileSync('web/lifecycle.js','utf8');
(async()=>{
// Single-flight guards cover both background polls and action-triggered refreshes.
for(const name of ['refresh','loadLibrary']){
 let finish,calls=0;
 const ctx=vm.createContext({document:{hidden:false},Promise});
 ctx[name+'Snapshot']=()=>{calls++;return new Promise(resolve=>finish=resolve)};
 vm.runInContext(html.slice(html.indexOf('function '+name+'(){'),html.indexOf('async function '+name+'Snapshot')),ctx);
 const first=ctx[name]();assert.strictEqual(first,ctx[name]());assert.equal(calls,1);finish();await first;
 ctx.document.hidden=true;await ctx[name]();assert.equal(calls,1);
}
const dom=new JSDOM('<div id="view-library"><div class="bubbles" data-bkey="a"><div class="bubble run"></div></div></div>',{runScripts:'outside-only',url:'https://example.test/'}),w=dom.window;
let hidden=false;Object.defineProperty(w.document,'hidden',{get:()=>hidden});
let ids=0;const timers=new Map();w.setTimeout=(fn,delay)=>{timers.set(++ids,{fn,delay});return ids};w.clearTimeout=id=>timers.delete(id);
const motion={matches:false,addEventListener(name,fn){this.changed=fn}};w.matchMedia=()=>motion;
let observer;w.IntersectionObserver=class{constructor(fn){observer=fn}observe(){}unobserve(){}};
w.refresh=async()=>{};w.loadLibrary=async()=>{};
w.bubbleState=new Map();w.stepBubble=()=>{w.steps=(w.steps||0)+1};
w.eval(lifecycle+'\nwindow.syncBubbleVisibility=syncBubbleVisibility;');
w.eval('bubbleState.set("a",{node:document.querySelector(".bubble"),timer:0,items:["first","second"],index:1})');
w.syncBubbleVisibility();assert(![...timers.values()].some(t=>t.delay===4400));
observer([{target:w.document.querySelector('.bubble'),isIntersecting:true}]);assert([...timers.values()].some(t=>t.delay===4400));
hidden=true;w.document.dispatchEvent(new w.Event('visibilitychange'));assert.equal(timers.size,0);
assert(w.document.querySelector('.bubble').hasAttribute('data-paused'));
w.eval('if(bubbleState.get("a").index!==1 || bubbleState.get("a").items.length!==2)throw Error("comments lost")');
hidden=false;w.document.dispatchEvent(new w.Event('visibilitychange'));assert([...timers.values()].some(t=>t.delay===4400));
observer([{target:w.document.querySelector('.bubble'),isIntersecting:false}]);assert(![...timers.values()].some(t=>t.delay===4400));
observer([{target:w.document.querySelector('.bubble'),isIntersecting:true}]);
motion.matches=true;motion.changed();assert(![...timers.values()].some(t=>t.delay===4400));
motion.matches=false;motion.changed();assert([...timers.values()].some(t=>t.delay===4400));
w.document.querySelector('.bubble').remove();w.syncBubbleVisibility();assert(![...timers.values()].some(t=>t.delay===4400));
w.dispatchEvent(new w.Event('pagehide'));dom.window.close();
// Worker cache allowlist and fetch interception, including credential-bearing paths.
const handlers={},requests=[];const ctx=vm.createContext({URL,self:{location:{origin:'https://example.test'},addEventListener:(name,fn)=>handlers[name]=fn},caches:{open:async()=>({addAll:async paths=>requests.push(...paths)})},fetch:async()=>({ok:true})});
vm.runInContext(fs.readFileSync('web/sw.js','utf8'),ctx);
let install;handlers.install({waitUntil:p=>install=p});await install;
assert(requests.includes('/offline.html'));assert(!requests.some(p=>p==='/'||p.includes('api')||p.includes('login')||p.includes('auth')));
for(const path of ['/api/state','/api/lyrics/1','/api/track/1/audio','/auth/link#invite=secret','/login','/rest/stream?u=a&p=b','/icon-192.png?token=secret']){
 let intercept=false;handlers.fetch({request:{url:'https://example.test'+path,method:'GET',mode:'navigate'},respondWith(){intercept=true}});assert(!intercept,path);
}
console.log('performance/PWA DOM PASS: single-flight, hidden/offscreen cleanup, retained comments, static-only caching');
})().catch(e=>{console.error(e);process.exitCode=1});
