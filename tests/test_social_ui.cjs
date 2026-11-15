// Isolated DOM smoke: no network, app boot, DB, or media.
const fs=require('fs'),vm=require('vm'),assert=require('assert');
const html=fs.readFileSync('web/index.html','utf8');
new vm.Script(html.match(/<script>([\s\S]*?)<\/script>/)[1]);
class Node {
  constructor(tag){this.tagName=tag;this.children=[];this.style={};this.attrs={};this.textContent='';}
  append(...nodes){this.children.push(...nodes);for(const n of nodes)n.parentElement=this;}
  replaceChildren(){this.children=[];}
  setAttribute(k,v){this.attrs[k]=v;}
  querySelector(selector){return this.children.find(n=>'.'+n.className===selector)||null;}
  remove(){this.parentElement.children=this.parentElement.children.filter(n=>n!==this);}
  set innerHTML(_){throw Error('Untrusted HTML insertion');}
}
const xss='<img src=x onerror="throw Error(1)">';
const summary=new Node('summary'),host=new Node('div');host.className='social-body';
const box=new Node('details');box.dataset={social:'/api/social/track/1'};box.append(summary,host);
box.querySelector=s=>s==='summary'?summary:host;
const calls=[];
const context=vm.createContext({console,document:{createElement:t=>new Node(t),documentElement:{classList:{toggle(){}}},activeElement:null,querySelectorAll:()=>[]},
  localStorage:{getItem:()=>null,setItem(){}},scrollTo(){},
  fetch:async()=>({ok:true,json:async()=>({comments:2,score:-1,up:0,down:1,vote:-1,
    items:[{id:1,member_id:xss,body:xss,created_at:'now',can_edit:true}],next_after:null})}),
  api:async(...args)=>calls.push(args),prompt:()=>xss+' edited',confirm:()=>true});
vm.runInContext('let playerAudio = {src: ""};',context);
vm.runInContext(html.slice(html.indexOf('// Personal listening state'),html.indexOf('const playerAudio =')),context);
(async()=>{
  await context.socialLoad(box);
  assert(summary.textContent.includes('your vote -1'));
  assert(summary.textContent.includes('Discuss (2)'));
  const flat=n=>[n,...n.children.flatMap(flat)];
  assert(flat(host).some(n=>n.textContent===xss));
  assert(flat(host).some(n=>n.textContent.includes('Member '+xss)));
  assert(flat(host).every(n=>n.tagName!=='img'));
  const buttons=flat(host).filter(n=>n.tagName==='button');
  assert(buttons.some(n=>n.textContent.startsWith('▼')&&n.attrs['aria-pressed']==='true'));
  let stopped=0;
  const event={preventDefault(){},stopPropagation(){stopped++;}};
  await buttons.find(n=>n.textContent==='Edit').onclick(event);
  assert.deepEqual(calls[0],['/api/social/track/1/comments/1',{body:xss+' edited'}]);
  await flat(host).find(n=>n.textContent.startsWith('Reset vote')).onclick(event);
  assert.deepEqual(calls[1],['/api/social/track/1/vote',{value:0}]);
  assert.equal(stopped,2);
  const markup=context.socialControl('track',1);
  assert(markup.includes('event.stopPropagation()'));
  assert(markup.includes('/api/social/track/1'));
  assert.equal(context.socialControl('album',null),'');
  // The library wires discussions into album and playlist detail, and a shared
  // drawer for cards and song rows, all through the same verified endpoint.
  assert(html.includes('socialControl("album", album.key)'));
  assert(html.includes('socialControl("playlist", playlist.id)'));
  assert(html.includes("openDiscussion('${kind}','${id}',event)"));
  assert(context.voteRow('track',1).includes('/api/social/track/1/vote') === false);
  assert(context.voteRow('track',1).includes("castVote('track','1',1,event)"));
  assert(context.voteRow('album','a'.repeat(64)).includes("castVote('album','"));
  assert.equal(context.socialControl('track','1\" onclick=alert(1)'), '');
  // A library poll must reattach the exact open discussion/draft node.
  box.open=true;
  const fields={'#libsearch':{value:''},'#libplaylist':{value:''},'#libtitle':{},'#libscopehelp':{},'#libscopes':{querySelectorAll:()=>[]}};
  let attached=[box];
  fields['#liblist']={querySelectorAll:()=>attached,set innerHTML(value){
    const replacement={dataset:{social:'/api/social/track/1'},replaceWith(previous){attached=[previous];}};
    attached=[replacement];
  }};
  context.$=id=>fields[id];context.esc=String;context.fmtClock=String;
  fields['#view-library']={classList:{toggle(){}}};fields['#libcount']={textContent:''};
  vm.runInContext("lib=[{id:1,title:'Song',artist_name:'Artist',album:'Album',album_id:'a'.repeat(64),playable:true,playlists:[]}];libAll=lib.slice();renderLibrary()",context);
  assert.strictEqual(attached[0],box);assert(box.open);

  // Votes move on the click, not on the round trip.
  const voteCtx=vm.createContext({document:{querySelectorAll:()=>[{classList:{add(){},remove(){}},dataset:{voteValue:'1'},offsetWidth:1}]},setTimeout:()=>0,console});
  vm.runInContext(html.slice(html.indexOf('function optimisticVote'),html.indexOf('function pulseVote')),voteCtx);
  const fresh=voteCtx.optimisticVote({score:0,up:3,down:1,vote:0},0,1);
  assert.strictEqual(fresh.up,4);assert.strictEqual(fresh.score,3);assert.strictEqual(fresh.vote,1);
  const switched=voteCtx.optimisticVote({score:2,up:3,down:1,vote:1},1,-1);   // up → down in one click
  assert.strictEqual(switched.up,2);assert.strictEqual(switched.down,2);assert.strictEqual(switched.score,0);
  const retract=voteCtx.optimisticVote({score:2,up:3,down:1,vote:1},1,0);      // pressing again retracts
  assert.strictEqual(retract.up,2);assert.strictEqual(retract.score,1);assert.strictEqual(retract.vote,0);
  assert.strictEqual(voteCtx.optimisticVote({up:0,down:0,score:0,vote:0},0,-1).down,1);
  console.log('Social DOM smoke PASS: text-only XSS, counts/own vote, edit/reset API, propagation, poll/draft retention, all entity controls and full syntax');
})().catch(e=>{console.error(e);process.exitCode=1;});
