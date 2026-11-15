/* The playback queue and the owner playlist editor.

   Loaded as a classic script from index.html where the inline block used to sit, so the
   globals declared here (queueRestorePending, playHistory, paintPlaybackQueue, bindOrderDrag,
   openPlaylistEditor ...) land in the same order and are visible to the rest of the app
   exactly as before. Not a module, and not deferred: it must not run before or after its
   neighbours.

   Owns: the queue drawer (#playback-items, #playback-panel), its drag reordering, the
   clear-with-confirmation, the recently-played history, and the playlist editor dialog.
   No media or network work on initialization. */
let queueRestorePending = true;
// The queue is not a page overlay: it is a drawer that only exists while the player is loaded.
// Playback history: newest first, capped, and only tracks that actually started.
let playHistory = (() => {
  try { return JSON.parse(localStorage.getItem("hl.history") || "[]").filter(h => h && h.id); }
  catch (_) { return []; }
})();
function rememberPlayed(id){
  playHistory = [{id, at: Date.now()}, ...playHistory.filter(h => h.id !== id)].slice(0, 60);
  try { localStorage.setItem("hl.history", JSON.stringify(playHistory)); } catch (_) {}
  paintHistory();
}
function timeAgo(stamp){
  const minutes = Math.max(0, Math.round((Date.now() - stamp) / 60000));
  if (minutes < 1) return "just now";
  if (minutes < 60) return minutes + "m ago";
  const hours = Math.round(minutes / 60);
  if (hours < 24) return hours + "h ago";
  return Math.round(hours / 24) + "d ago";
}
function paintHistory(){
  const host = $("#history-items"), status = $("#history-status"), count = $("#history-count");
  if (!host) return;
  const rows = playHistory.map(entry => ({entry, track: (libAll || []).find(t => t.id === entry.id)}))
                          .filter(item => item.track);
  if (count) count.textContent = String(rows.length);
  if (status) status.hidden = rows.length > 0;
  host.innerHTML = rows.map(({entry, track}) => `
    <div class="hist-row" data-history-id="${track.id}" role="button" tabindex="0">
      <span class="pq-name">${esc(track.title || "Untitled")}<small>${esc(track.artist_name || "")}</small></span>
      <span class="hist-when">${timeAgo(entry.at)}</span>
      ${String(track.id) === String(playingId) ? '<span class="pq-live" aria-hidden="true"></span>' : ""}
    </div>`).join("");
}
$("#history-items")?.addEventListener("click", event => {
  const row = event.target.closest("[data-history-id]");
  if (row) playFromLibrary(Number(row.dataset.historyId));
});
// Drag the queue down to pull the recently-played history out from under it, drag up (or
// a tap on its heading) to push it back. The history is not part of the queue unless you
// ask for it, and it scrolls on its own.
(() => {
  const panel = document.getElementById("playback-panel");
  const details = document.getElementById("history-details");
  const items = document.getElementById("playback-items");
  if (!panel || !details) return;
  const setHistory = open => {
    panel.classList.toggle("history-open", !!open);
    if (open) details.open = true;
  };
  const summary = details.querySelector("summary");
  if (summary) summary.addEventListener("click", event => {
    event.preventDefault();
    setHistory(!panel.classList.contains("history-open"));
  });
  let start = null;
  const armable = target => {
    if (target.closest("button,input,select,a,summary")) return false;
    // Inside the list, only a pull from the very top counts: otherwise the gesture is a scroll.
    if (target.closest("#playback-items")) return (items ? items.scrollTop : 0) <= 0;
    return !!target.closest("#playback-panel");
  };
  panel.addEventListener("pointerdown", event => {
    if (event.button > 0 || !armable(event.target)) return;
    start = {y: event.clientY, id: event.pointerId, moved: false};
  });
  panel.addEventListener("pointermove", event => {
    if (!start || event.pointerId !== start.id) return;
    // A row being reordered owns the gesture. Pulling the history open mid-drag grew this
    // panel by 80px, so every row moved up and the finger ended rows below where it began.
    if (items && items.dataset.dragging === "1") { start = null; return; }
    const dy = event.clientY - start.y;
    if (dy > 44 && !panel.classList.contains("history-open")) { setHistory(true); start = null; }
    else if (dy < -44 && panel.classList.contains("history-open")) { setHistory(false); start = null; }
  });
  const end = () => { start = null; };
  panel.addEventListener("pointerup", end);
  panel.addEventListener("pointercancel", end);
})();
// Fullscreen playback is a fixed page: no document scroll, no pinch zoom, no rubber-band
// off the screen on iOS. Inner scrollers (queue, history) keep their own pan.
(() => {
  const locked = () => document.body.classList.contains("fullplayer");
  document.addEventListener("gesturestart", event => { if (locked()) event.preventDefault(); }, {passive: false});
  // iOS scrolls the document itself, and a fixed body does not always stop it. While the
  // player is up, any move that is not inside a real scroller is cancelled outright.
  document.addEventListener("touchmove", event => {
    if (!locked()) return;
    if (event.touches.length > 1) { event.preventDefault(); return; }
    const target = event.target.closest?.("#playback-items,#history-items,#playback-panel .pq-now");
    if (target && target.scrollHeight > target.clientHeight + 2) return;
    event.preventDefault();
  }, {passive: false});
})();

function togglePlaybackPanel(force, instant){
  const panel = document.querySelector('#playback-panel');
  if (!panel) return;
  const open = force === undefined ? !panel.classList.contains('open') : !!force;
  // Inside the fullscreen column the panel rides the slot down, so that slide is the whole
  // close. Running this function's own exit here instead started a 160 ms panel-out next to
  // the slot's 440 ms travel, dropped `queue-open` at once, and re-showed the panel at
  // opacity 1 the moment `.closing` was cleared — a pop about 170 ms into the slide, with the
  // panel still parked in the slot afterwards. The × and Escape now take the same path as
  // the footer's queue button.
  if (!open && panel.classList.contains('in-full') && typeof showFullQueue === 'function'){
    showFullQueue(false);
    return;
  }
  // The panel slides out before it hides: `.closing` keeps it on screen for the exit
  // animation, then the class is dropped. Opening is immediate. `instant` is for a close
  // that already happened on screen — the fullscreen queue's slide — where fading the panel
  // out again would paint it a second time, at its home spot, after it left.
  clearTimeout(panel._closeTimer);
  if (open){
    panel.classList.remove('closing');
    panel.classList.add('open');
  } else if (panel.classList.contains('open')){
    panel.classList.remove('open');
    if (instant){
      panel.classList.remove('closing');
    } else {
      panel.classList.add('closing');
      panel._closeTimer = setTimeout(() => panel.classList.remove('closing'), 170);
    }
  }
  const bar = document.getElementById('player');
  if (bar) bar.classList.toggle('queue-shown', open);
  // The fullscreen layout follows the panel: closing the queue brings the record and the
  // header back, whichever control closed it.
  const full = document.querySelector('#player-full');
  if (full){
    full.classList.toggle('queue-open', open && full.classList.contains('on'));
    if (!open){
      const text = document.querySelector('#player-full .pf-text');
      if (text) text.style.transform = '';
    }
  }
  const btn = document.querySelector('#plqueue');
  if (btn) btn.setAttribute('aria-expanded', String(open));
  const pfqueue = document.querySelector('#pfqueue');
  if (pfqueue) pfqueue.setAttribute('aria-expanded', String(open));
}

function syncPlaybackQueue(){
  if (queueRestorePending && libAll.length){
    queueRestorePending = false;
    try {
      const ids = JSON.parse(localStorage.getItem('hl.playbackQueue.v1') || 'null');
      if (Array.isArray(ids)) {
        libQueue = [...new Set(ids)].map(id => libAll.find(t => t.id === id && t.playable)).filter(Boolean);
        playbackQueueEdited = true; libIndex = -1;
      }
    } catch (_) {} // A damaged or denied browser store must not block the library.
  }
  paintPlaybackQueue();
}
function commitPlaybackQueue(){
  queueRestorePending = false;
  if (typeof saveNowState === 'function') setTimeout(() => saveNowState(true), 0);
  playbackQueueEdited = true;
  libIndex = libQueue.findIndex(t => t.id === playingId);
  try { localStorage.setItem('hl.playbackQueue.v1', JSON.stringify(libQueue.map(t => t.id))); } catch (_) {}
  paintPlaybackQueue();
}
function queueMove(id, action){
  const from = libQueue.findIndex(t => t.id === id);
  if (from < 0) return;
  if (action === 'remove') libQueue.splice(from,1);
  else {
    const to = action === 'top' ? 0 : action === 'bottom' ? libQueue.length-1 : Math.max(0,Math.min(libQueue.length-1,from+(action === 'up' ? -1 : 1)));
    libQueue.splice(to,0,libQueue.splice(from,1)[0]);
  }
  commitPlaybackQueue();
  (document.querySelector(`#playback-items [data-id="${id}"] [data-action="${action}"]`) || document.querySelector('#playback-details summary')).focus();
  document.querySelector('#playback-status').textContent = action === 'remove' ? 'Removed from queue. Current audio is unchanged.' : 'Queue order updated.';
}
// Clearing the queue is one tap away from the queue button, so it asks first.
function askClearPlaybackQueue(){
  const waiting = Math.max(0, libQueue.length - (playingId ? 1 : 0));
  if (!libQueue.length) { showToast("The queue is already empty."); return; }
  document.getElementById("queue-confirm-text").textContent =
    `Remove ${waiting} song${waiting === 1 ? "" : "s"} from the queue? The song playing now keeps playing.`;
  document.getElementById("queue-confirm").showModal();
}
// The dialog is part of the page, but wiring it at top level means a page that only carries
// the queue markup would throw here and lose every handler below it.
document.getElementById("queue-confirm")?.addEventListener("close", event => {
  if (event.target.returnValue === "go") clearPlaybackQueue();
});
function clearPlaybackQueue(){libQueue=[];commitPlaybackQueue();document.querySelector('#playback-status').textContent='Queue cleared. Current audio is unchanged.';}
function moveButtons(id, fn){
  return ['top','up','down','bottom','remove'].map(action => `<button type="button" data-action="${action}" aria-label="${action === 'remove' ? 'Remove song' : 'Move '+action}" onclick="${fn}(${id},'${action}')">${({top:'⇈',up:'↑',down:'↓',bottom:'⇊',remove:'Remove'})[action]}</button>`).join('');
}
// Buffer state of a row: buffered beats fetching, and neither is guessed.
function queueState(id){
  if (aheadAudio.has(id)) return {cls: "pq-ready", tag: "ready"};
  if (aheadJobs.has(id)) return {cls: "pq-loading", tag: "loading"};
  return {cls: "", tag: ""};
}
function paintPlaybackQueue(){
  const host = document.querySelector('#playback-items'); if (!host || host.dataset.dragging) return;
  const current = libIndex >= 0 ? libQueue[libIndex] : null;
  const upNext = libIndex >= 0 ? libQueue.slice(libIndex + 1) : libQueue.slice();
  const count = document.querySelector('#playback-count');
  // Nothing queued means nothing to sweep: the broom says so instead of reporting it.
  const broom = document.querySelector('#pqclear');
  if (broom) broom.disabled = libQueue.length === 0;
  if (count) count.textContent = upNext.length;
  const now = document.querySelector('#playback-now');
  if (now){
    if (current && playingId != null){
      now.hidden = false;
      now.innerHTML = `<span class="pq-live" aria-hidden="true"></span><span class="pq-name">${esc(current.title || 'Untitled')}<small>${esc(current.artist_name || '')}</small></span><span class="pq-tag now">now</span>`;
    } else { now.hidden = true; now.innerHTML = ""; }
  }
  const markup = upNext.map(t => {
    const state = queueState(t.id);
    // No × in the row. It sat one thumb-width from the row's own tap target, so clearing a
    // song by accident was easier than playing it. The row slides left to reveal Remove
    // instead, and the ⋯ menu keeps the same action for anyone without a pointer.
    return `<div class="pq-row ${state.cls}" data-id="${t.id}" tabindex="0" role="button" aria-label="Play ${esc(t.title)}, drag or hold to reorder, swipe left to remove"><div class="pq-body"><button class="pq-handle" type="button" aria-label="Drag to reorder ${esc(t.title)}" title="Drag to reorder">⠿</button><span class="pq-name">${esc(t.title || 'Untitled')}<small>${esc(t.artist_name || '')}</small></span><span class="pq-tag">${state.tag}</span><button class="pq-more" type="button" data-action="more" aria-label="More options for ${esc(t.title)}" title="More options">⋯</button></div><button class="pq-remove" type="button" data-action="remove" tabindex="-1" aria-label="Remove ${esc(t.title)} from the queue">Remove</button></div>`;
  }).join('') || '<p class="hint pq-empty">Nothing up next — the queue is empty.</p>';
  // Database IDs are numeric and all external metadata above passes through esc().
  if (host.dataset.rendered !== markup) {host.innerHTML=markup;host.dataset.rendered=markup;}
}
// One drag implementation serves mouse, pen and touch. A grip drags at once; a
// queue row has no grip, so touch/pen needs a short hold before it lifts, and a
// plain tap stays a tap. Cancel never commits.
function bindOrderDrag(host, commit){
  let drag = null, hold = null, armed = false;
  const release = () => { clearTimeout(hold); hold = null; };
  host.addEventListener('pointerdown', e => {
    if (e.button > 0) return;
    const grip = e.target.closest('.pq-grip,.pq-handle');
    if (!grip && e.target.closest('button,input')) return;
    const row = (grip || e.target).closest('.pq-row');
    if (!row || row.parentElement !== host) return;
    // The flag that suppresses the click trailing a drop belongs to one gesture. Clearing it
    // as the next gesture starts stops a drag that ended without a click from eating a tap.
    delete host.dataset.justDragged;
    drag = {row, pointer: e.pointerId, y: e.clientY, x: e.clientX, moved: false,
            ids: [...host.children].map(n => +n.dataset.id).filter(Boolean)};
    armed = !!grip || e.pointerType !== 'touch';
    // Only a real grip shows a lifted row up front; a plain press waits for movement
    // (mouse) or the hold (touch), so clicking a song still plays it.
    if (grip) { row.classList.add('pq-lift'); host.dataset.dragging = '1'; try { row.setPointerCapture?.(e.pointerId); } catch (_) {} }
    else if (!armed) hold = setTimeout(() => {
      if (!drag) return;
      armed = true; drag.row.classList.add('pq-lift'); host.dataset.dragging = '1';
      try { drag.row.setPointerCapture?.(drag.pointer); } catch (_) {}
      navigator.vibrate?.(8);
    }, 280);
    // No pointer capture until this is really a drag: capturing on the way down
    // swallowed the click, so tapping a queue row never played it. The gesture is also
    // tracked on the window: a drop below the last row lands outside the list, and a
    // pointerup the list never sees leaves the row stuck lifted.
    window.addEventListener('pointermove', move);
    window.addEventListener('pointerup', end);
    window.addEventListener('pointercancel', cancelDrag);
  });
  function move(e){
    if (!drag || drag.pointer !== e.pointerId) return;
    if (!armed){
      if (Math.abs(e.clientY - drag.y) > 8 || Math.abs(e.clientX - drag.x) > 8){
        release(); drag = null;                  // that was a scroll, not a hold
      }
      return;
    }
    e.preventDefault();
    // A mouse arms the drag on pointerdown, so movement — not arming — is what
    // makes it a drag. Otherwise every plain click was swallowed as a reorder.
    // Movement has to be vertical to count: a sideways drag is the row's swipe-to-remove,
    // and claiming the first few pixels meant the swipe could never begin.
    const dx = Math.abs(e.clientX - drag.x), dy = Math.abs(e.clientY - drag.y);
    if (!drag.moved && dy > 3 && dy >= dx){
      drag.moved = true;
      if (!drag.row.classList.contains('pq-lift')){ drag.row.classList.add('pq-lift'); host.dataset.dragging = '1'; }
      try { drag.row.setPointerCapture?.(drag.pointer); } catch (_) {}
    }
    // Place the lifted row before the first row whose middle is below the finger. That is
    // one idempotent answer for every position — hit-testing here returned the lifted row
    // itself (it sits under the finger) and the old geometry fallback could swap twice per
    // move, so a short drag slid the row several places.
    const others = [...host.children].filter(node => node !== drag.row && +node.dataset.id);
    const next = others.find(node => {
      const box = node.getBoundingClientRect();
      return e.clientY < box.top + box.height / 2;
    });
    if (host.children[host.children.length - 1] !== drag.row || next) {
      host.insertBefore(drag.row, next || null);
    }
    const box = host.getBoundingClientRect();
    if (e.clientY < box.top + 32) host.scrollTop -= 14;
    else if (e.clientY > box.bottom - 32) host.scrollTop += 14;
  }
  const cancelDrag = e => end(e, true);
  const end = (e, cancel) => {
    release();
    window.removeEventListener('pointermove', move);
    window.removeEventListener('pointerup', end);
    window.removeEventListener('pointercancel', cancelDrag);
    if (!drag || e.pointerId !== drag.pointer) return;
    const moved = !!drag.moved;
    const ids = [...host.children].map(n => +n.dataset.id).filter(Boolean);
    drag.row.classList.remove('pq-lift');
    drag = null; armed = false; delete host.dataset.dragging;
    // A plain tap changes nothing: repainting the list here replaced the row under
    // the pointer, which swallowed the click that plays it.
    if (!moved) return;
    delete host.dataset.rendered;
    if (!cancel) host.dataset.justDragged = '1';
    commit(cancel ? null : ids, !cancel);
  };
  // Once a row is lifted the touch drag has to own the gesture. `touch-action` is latched
  // when the finger lands, so the row's `pan-y` still lets the list scroll — and the
  // browser then cancels the pointer mid-drag, which silently ended every phone reorder.
  // Blocking the touch scroll only while a row is lifted leaves ordinary scrolling alone.
  host.addEventListener('touchmove', e => {
    if (host.dataset.dragging === '1' && e.cancelable) e.preventDefault();
  }, { passive: false });
  // A long press belongs to the drag, not to the browser's copy/share menu.
  host.addEventListener('contextmenu', e => {
    if (e.target.closest('.pq-row')) e.preventDefault();
  });
}
// Tap plays, × removes, Alt+arrow moves: full keyboard parity without a single
// visible arrow button in the queue.
const queueHost = document.querySelector('#playback-items');
bindOrderDrag(queueHost, (ids, save) => {
  if (ids){
    const visible = libIndex >= 0 ? libQueue.slice(libIndex + 1) : libQueue.slice();
    const byId = new Map(visible.map(t => [t.id, t]));
    const reordered = ids.map(id => byId.get(id)).filter(Boolean);
    libQueue = libIndex >= 0 ? [...libQueue.slice(0, libIndex + 1), ...reordered] : reordered;
  }
  if (save) commitPlaybackQueue();
  paintPlaybackQueue();
});
// A queue row removes its song by sliding left, the way a list on a phone does. The × that
// used to sit in every row was one thumb-width from the row's own tap target, so clearing a
// song by accident was easier than playing it. The ⋯ menu keeps the same action for anyone
// without a pointer, and the Remove panel is not in the tab order until it is showing.
(function queueSwipe(){
  const host = document.querySelector('#playback-items');
  if (!host) return;
  const REVEAL = 92;
  let press = null;
  const shiftOf = row => Math.abs(parseFloat(row.style.getPropertyValue('--pq-shift')) || 0);
  const close = row => { row.classList.remove('pq-swiped', 'pq-live'); row.style.removeProperty('--pq-shift'); };
  const setShift = (row, px) => row.style.setProperty('--pq-shift', Math.round(px) + 'px');
  // The gesture is tracked on the window, not on the row: a swipe that runs past the row's
  // edge (which is the normal way to swipe) would otherwise deliver its pointerup somewhere
  // else entirely, leaving the row stuck half-open. Pointer capture does not cover it here.
  const move = e => {
    if (!press || e.pointerId !== press.id) return;
    if (host.dataset.dragging === '1') return;         // the reorder drag owns a lifted row
    const dx = e.clientX - press.x, dy = e.clientY - press.y;
    if (!press.live){
      if (Math.abs(dx) < 12 || Math.abs(dx) <= Math.abs(dy)) return;   // a scroll, or a tap
      press.live = true;
      press.row.classList.add('pq-live');
    }
    setShift(press.row, Math.max(-REVEAL, Math.min(0, press.base + dx)));
  };
  const release = () => {
    window.removeEventListener('pointermove', move);
    window.removeEventListener('pointerup', settle);
    window.removeEventListener('pointercancel', settle);
  };
  function settle(){
    if (!press) return;
    const {row, live} = press;
    press = null;
    release();
    if (!live) return;
    row.classList.remove('pq-live');
    // Past halfway it stays open; short of that it springs back.
    row.classList.toggle('pq-swiped', shiftOf(row) > REVEAL / 2);
    row.style.removeProperty('--pq-shift');            // the class carries the resting position
    host.dataset.justSwiped = '1';
  }
  host.addEventListener('pointerdown', e => {
    if (e.button > 0) return;
    delete host.dataset.justSwiped;                    // this gesture's click is its own
    if (e.target.closest('button,input')) return;      // the grip, ⋯ and Remove own their presses
    const row = e.target.closest('.pq-row');
    if (!row) return;
    for (const other of host.querySelectorAll('.pq-row.pq-swiped')) if (other !== row) close(other);
    press = {row, id: e.pointerId, x: e.clientX, y: e.clientY, base: shiftOf(row), live: false};
    window.addEventListener('pointermove', move, { passive: true });
    window.addEventListener('pointerup', settle);
    window.addEventListener('pointercancel', settle);
  });
  // A press on a row that is already slid open puts it back instead of playing the song, and
  // a press anywhere outside the queue closes it too. Remove itself is never pre-empted.
  document.addEventListener('pointerdown', e => {
    const open = e.target.closest?.('.pq-row.pq-swiped');
    if (open){
      if (!e.target.closest('.pq-remove')){
        close(open);
        host.dataset.justSwiped = '1';
      }
      return;
    }
    if (!host.contains(e.target)) for (const row of host.querySelectorAll('.pq-row.pq-swiped')) close(row);
  }, true);
})();
queueHost.addEventListener('click', event => {
  if (event.target.closest('.pq-handle')) { event.preventDefault(); return; }
  if (queueHost.dataset.justDragged){ delete queueHost.dataset.justDragged; event.preventDefault(); return; }
  if (queueHost.dataset.justSwiped){ delete queueHost.dataset.justSwiped; event.preventDefault(); return; }
  const row = event.target.closest('.pq-row'); if (!row) return;
  const id = +row.dataset.id;
  if (event.target.closest('[data-action="more"]')){
    event.preventDefault(); event.stopPropagation();
    const button = event.target.closest('[data-action="more"]');
    const rect = button.getBoundingClientRect();
    const id = Number(button.closest('.pq-row').dataset.id);
    const menu = document.createElement('div');
    menu.className = 'songmenu qrowmenu';
    menu.setAttribute('role', 'menu');
    const add = (label, action) => {
      const b = document.createElement('button');
      b.type = 'button'; b.setAttribute('role', 'menuitem'); b.textContent = label;
      b.onclick = () => { menu.remove(); action(); };
      menu.append(b);
    };
    add('Move to top', () => queueMove(id, 'top'));
    add('Move to bottom', () => queueMove(id, 'bottom'));
    add('Remove from queue', () => queueMove(id, 'remove'));
    document.body.append(menu);
    const box = menu.getBoundingClientRect();
    menu.style.left = Math.max(8, Math.min(rect.left, innerWidth - box.width - 8)) + 'px';
    menu.style.top = Math.max(8, Math.min(rect.top - box.height - 6, innerHeight - box.height - 8)) + 'px';
    document.addEventListener('pointerdown', function close(e){
      if (!menu.contains(e.target)){ menu.remove(); document.removeEventListener('pointerdown', close); }
    }, true);
    return;
  }
  if (event.target.closest('[data-action="remove"]')) { queueMove(id, 'remove'); return; }
  playTrackNow(id);
});
queueHost.addEventListener('keydown', event => {
  const row = event.target.closest('.pq-row'); if (!row) return;
  const id = +row.dataset.id;
  if (event.key === 'Enter' || event.key === ' '){ event.preventDefault(); playTrackNow(id); return; }
  if (!event.altKey) return;
  const moves = {ArrowUp: 'up', ArrowDown: 'down', Home: 'top', End: 'bottom'};
  if (moves[event.key]){ event.preventDefault(); queueMove(id, moves[event.key]); }
});
document.querySelector('#plqueue')?.addEventListener('click', () => togglePlaybackPanel());
document.addEventListener('keydown', e => { if (e.key === 'Escape') togglePlaybackPanel(false); });
let userPlaylists=[], editingPlaylist=null, playlistBusy=false;
async function openPlaylistEditor(trackId, createOnly = false, editId = null){
  const dialog = document.querySelector('#playlist-editor');
  dialog.classList.toggle('create-only', !!createOnly);
  document.querySelector('#playlist-song-search').value = libAll.find(t=>t.id===trackId)?.title || '';
  dialog.showModal();
  try { const response=await fetch('/api/playlists',{cache:'no-store'});if(!response.ok)throw Error('Could not load playlists');userPlaylists=(await response.json()).playlists;
    if (editId != null) editingPlaylist = userPlaylists.find(p => p.id === +editId) || null;
    paintPlaylistChoices(); }
  catch(e){document.querySelector('#playlist-message').textContent=e.message;}
}
function openNewPlaylistEditor(){ editingPlaylist = null; openPlaylistEditor(undefined, true); }
function editPlaylist(id){ openPlaylistEditor(undefined, false, id); }
function paintPlaylistChoices(){
  const choice=document.querySelector('#playlist-choice');
  choice.innerHTML='<option value="">Choose a playlist</option>'+userPlaylists.map(p=>`<option value="${p.id}">${esc(p.title)}${p.can_edit?' · Yours':' · View only'}</option>`).join('');
  choice.value=editingPlaylist?.id || ''; selectUserPlaylist();
}
function selectUserPlaylist(){editingPlaylist=userPlaylists.find(p=>p.id===+document.querySelector('#playlist-choice').value)||null;paintPlaylistEditor();}
function playlistTrack(id){return libAll.find(t=>t.id===id)||{id,title:'Track '+id,artist_name:'Not loaded'};}
function paintPlaylistEditor(){
  const p=editingPlaylist,host=document.querySelector('#playlist-members');if(host.dataset.dragging)return;
  host.innerHTML=(p?.track_ids||[]).map(id=>{const t=playlistTrack(id);return `<div class="pq-row" data-id="${id}">${p.can_edit?'<button class="pq-grip" aria-label="Drag to reorder song">⠿</button>':''}<span class="pq-name">${esc(t.title)}<small>${esc(t.artist_name)}</small></span>${p.can_edit?`<div class="pq-actions">${moveButtons(id,'playlistMove')}</div>`:''}</div>`;}).join('');
  const q=document.querySelector('#playlist-song-search').value.toLowerCase();
  document.querySelector('#playlist-candidates').innerHTML=p?.can_edit?libAll.filter(t=>!p.track_ids.includes(t.id)&&`${t.title} ${t.artist_name}`.toLowerCase().includes(q)).slice(0,60).map(t=>`<div class="pq-row"><span class="pq-name">${esc(t.title)}<small>${esc(t.artist_name)}</small></span><button onclick="addPlaylistSong(${t.id})">Add song</button></div>`).join(''):'<p class="hint">Choose one of your playlists to add or reorder songs.</p>';
}
async function createUserPlaylist(){
  const input=document.querySelector('#playlist-name'),title=input.value.trim();if(!title || playlistBusy)return;
  playlistBusy=true;
  try{const r=await api('/api/playlists',{title,track_ids:[]});await verifyPlaylist(r.playlist.id);input.value='';}
  catch(e){document.querySelector('#playlist-message').textContent=e.message;}finally{playlistBusy=false;}
}
async function verifyPlaylist(id){
  const response=await fetch('/api/playlists/'+id,{cache:'no-store'});if(!response.ok)throw Error('Could not verify playlist; reload before editing');
  const p=(await response.json()).playlist;userPlaylists=userPlaylists.filter(x=>x.id!==p.id);userPlaylists.push(p);editingPlaylist=p;paintPlaylistChoices();document.querySelector('#playlist-message').textContent='Playlist saved.';
  if(typeof loadLibrarySnapshot==='function')loadLibrarySnapshot();
}
async function savePlaylistOrder(ids){
  const p=editingPlaylist;if(!p?.can_edit || playlistBusy){paintPlaylistEditor();return;}
  playlistBusy=true;
  try{await api('/api/playlists/'+p.id,{track_ids:ids,expected_track_ids:p.track_ids});await verifyPlaylist(p.id);}
  catch(e){document.querySelector('#playlist-message').textContent=e.message+' Close and reopen to reload before retrying.';paintPlaylistEditor();}finally{playlistBusy=false;}
}
function addPlaylistSong(id){if(editingPlaylist&&!editingPlaylist.track_ids.includes(id))savePlaylistOrder([...editingPlaylist.track_ids,id]);}
async function playlistMove(id,action){
  if(!editingPlaylist)return;const ids=[...editingPlaylist.track_ids],from=ids.indexOf(id);if(from<0)return;
  if(action==='remove')ids.splice(from,1);else{const to=action==='top'?0:action==='bottom'?ids.length-1:Math.max(0,Math.min(ids.length-1,from+(action==='up'?-1:1)));ids.splice(to,0,ids.splice(from,1)[0]);}await savePlaylistOrder(ids);
  (document.querySelector(`#playlist-members [data-id="${id}"] [data-action="${action}"]`) || document.querySelector('#playlist-choice')).focus();
}
bindOrderDrag(document.querySelector('#playlist-members'),(ids,save)=>{if(save)savePlaylistOrder(ids);else paintPlaylistEditor();});
