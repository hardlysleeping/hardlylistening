// The shell must not keep running against newer server code after a deploy.
const fs=require('fs'),vm=require('vm'),assert=require('assert');
const source=fs.readFileSync('web/lifecycle.js','utf8');
assert(/sessionStorage\.setItem\('hl\.shellVersion', version\)/.test(source),'the shell records the server version it booted against');
function run(serverVersion, stored){
  const store={};
  if (stored!==undefined) store['hl.shellVersion']=stored;
  let reloads=0;
  const ctx=vm.createContext({
    console, setTimeout:()=>0, clearTimeout(){}, setInterval:()=>0,
    fetch:async()=>({json:async()=>({ok:true,version:serverVersion})}),
    sessionStorage:{getItem:k=>store[k]??null,setItem:(k,v)=>{store[k]=String(v);}},
    location:{reload(){reloads++;}},
    navigator:{}, matchMedia:()=>({matches:false,addEventListener(){}}),
    IntersectionObserver:null, MutationObserver:class{observe(){}},
    document:{hidden:false,querySelector:()=>({hidden:false}),addEventListener(){}},
    window:{addEventListener(){}}, bubbleState:new Map(), stepBubble(){},
  });
  ctx.window.isSecureContext=false;
  vm.runInContext(source,ctx);
  return {ctx, store, reloads:()=>reloads};
}
(async()=>{
  const first=run('0.12.0',undefined);
  await vm.runInContext('watchServerVersion()',first.ctx);
  assert.equal(first.store['hl.shellVersion'],'0.12.0','a fresh shell remembers the server version');
  assert.equal(first.reloads(),0,'a matching version does not reload');

  const stale=run('0.13.0','0.12.0');
  await vm.runInContext('watchServerVersion()',stale.ctx);
  assert.equal(stale.reloads(),1,'a deploy while the page is open reloads it once');
  assert.equal(stale.store['hl.shellVersion'],'0.13.0','and the new version is remembered, so it never loops');

  const again=run('0.13.0','0.13.0');
  await vm.runInContext('watchServerVersion()',again.ctx);
  assert.equal(again.reloads(),0,'the reloaded shell does not reload again');

  const offline=run('0.12.0','0.12.0');
  offline.ctx.fetch=async()=>{throw Error('offline')};
  await vm.runInContext('watchServerVersion()',offline.ctx);
  assert.equal(offline.reloads(),0,'an offline shell keeps running');
  console.log('Shell version PASS: remembers the boot version, reloads once after a deploy, never loops, survives offline');
})();
