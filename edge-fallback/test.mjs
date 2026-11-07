import assert from 'node:assert/strict';
import worker from './worker.mjs';
const req = new Request('https://music.example.com/party#keep', {headers:{accept:'text/html'}});
for (const status of [200,206,303,400,401,403,404,429]) {
 const original=new Response('original',{status});globalThis.fetch=async()=>original;
 assert.equal(await worker.fetch(req),original);
}
for (const status of [502,504,520,522,530]) {
 globalThis.fetch=async()=>new Response('gateway',{status});const r=await worker.fetch(req);
 assert.equal(r.status,503);assert.match(await r.text(),/PC offline or unreachable/);assert.equal(r.headers.get('cache-control'),'no-store');
}
globalThis.fetch=async()=>{throw Error('disconnected')};
assert.equal((await worker.fetch(new Request('https://music.example.com/api/party'))).headers.get('content-type'),'application/json; charset=utf-8');
assert.equal(await (await worker.fetch(new Request('https://music.example.com/',{method:'HEAD'}))).text(),'');
const body=new ReadableStream({start(c){c.enqueue(new TextEncoder().encode('event: state\n\n'));c.close()}});
const stream=new Response(body,{headers:{'content-type':'text/event-stream'}});globalThis.fetch=async()=>stream;assert.equal(await worker.fetch(req),stream);
console.log('PASS: origin/auth/range/redirect passthrough, SSE stream passthrough, tunnel errors, offline HTML/JSON/HEAD');
