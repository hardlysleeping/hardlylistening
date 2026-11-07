'use strict';
// No media lifecycle changes: background audio and the server queue remain independent.
const reducedMotion = matchMedia('(prefers-reduced-motion: reduce)');
const observedBubbles = new Map();
const bubbleObserver = typeof IntersectionObserver === 'function' ? new IntersectionObserver(records => {
  for (const record of records) {
    const entry = observedBubbles.get(record.target);
    if (entry) { entry.visible = record.isIntersecting; reconcileBubble(entry); }
  }
}) : null;
function reconcileBubble(entry){
  const active = !document.hidden && entry.visible && entry.node.isConnected && !entry.node.closest('[hidden]');
  entry.node.toggleAttribute('data-paused', !active);
  if ((!active || reducedMotion.matches) && entry.timer){ clearTimeout(entry.timer); entry.timer = 0; }
  if (active && !reducedMotion.matches && !entry.timer){
    entry.timer = setTimeout(() => {
      entry.timer = 0;
      if (document.hidden || !entry.visible || !entry.node.isConnected) return;
      const key = entry.node.parentElement.dataset.bkey;
      stepBubble(key);
      reconcileBubble(entry);
    }, 4400);
  }
}
function syncBubbleVisibility(){
  for (const [node, entry] of observedBubbles){
    if (!node.isConnected){clearTimeout(entry.timer);entry.timer=0;bubbleObserver?.unobserve(node);observedBubbles.delete(node);}
  }
  for (const entry of bubbleState.values()){
    if (!observedBubbles.has(entry.node) && entry.node.isConnected){
      entry.visible = !bubbleObserver;
      observedBubbles.set(entry.node, entry);
      bubbleObserver?.observe(entry.node);
    }
    reconcileBubble(entry);
  }
}
// A completion-driven timeout means slow requests never accumulate a backlog.
let statePoll = 0, libraryPoll = 0, lifecycleGeneration = 0;
async function pollState(generation){
  try { await refresh(); } catch (_) { /* next foreground poll retries */ }
  if (!document.hidden && generation === lifecycleGeneration) statePoll = setTimeout(() => pollState(generation),1500);
}
async function pollLibrary(generation, initial = false){
  try { if (initial || !document.querySelector('#view-library').hidden) await loadLibrary(); } catch (_) {}
  if (!document.hidden && generation === lifecycleGeneration) libraryPoll = setTimeout(() => pollLibrary(generation),3000);
}
function foregroundLifecycle(){
  clearTimeout(statePoll);clearTimeout(libraryPoll);
  const generation = ++lifecycleGeneration;
  syncBubbleVisibility();
  if (!document.hidden){pollState(generation);pollLibrary(generation, true);}
}
document.addEventListener('visibilitychange',foregroundLifecycle);
window.addEventListener('pagehide',() => {
  ++lifecycleGeneration;clearTimeout(statePoll);clearTimeout(libraryPoll);
  for (const entry of observedBubbles.values()){clearTimeout(entry.timer);entry.timer=0;entry.node.setAttribute('data-paused','');}
});
window.addEventListener('pageshow',foregroundLifecycle);
reducedMotion.addEventListener('change',syncBubbleVisibility);
// Tab hiding immediately pauses existing covers even before another library paint.
new MutationObserver(syncBubbleVisibility).observe(document.querySelector('#view-library'),{attributes:true,attributeFilter:['hidden']});
foregroundLifecycle();
if ('serviceWorker' in navigator && window.isSecureContext){
  // Do not skipWaiting/force reload: updates apply on the next normal launch.
  navigator.serviceWorker.register('/sw.js',{scope:'/',updateViaCache:'none'}).catch(() => {});
}
