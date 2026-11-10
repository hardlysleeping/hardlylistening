// Swipe: one Repository card at a time. Right keeps (adds to the library), left drops,
// up superlikes (adds and likes). Tap plays the card; keyboard ← → ↑ do the same.
(() => {
  const deck = document.getElementById('deck');
  const counts = document.getElementById('counts');
  const empty = document.getElementById('empty');
  const error = document.getElementById('error');
  const audio = document.getElementById('audio');
  let queue = [];        // [{...track}]
  let busy = false;
  let playing = null;

  const say = text => { error.textContent = text || ''; };
  const esc = s => String(s == null ? '' : s).replace(/[&<>"']/g, c =>
    ({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[c]));
  const clock = s => {
    const n = Math.round(Number(s) || 0);
    return n ? `${Math.floor(n / 60)}:${String(n % 60).padStart(2, '0')}` : '';
  };
  const paintCounts = st => {
    if (!st) return;
    counts.textContent = `${st.keep} kept · ${st.like} liked · ${st.drop} dropped · ${st.left} left`;
  };

  async function load(more) {
    try {
      const r = await fetch('/api/swipe/next?limit=' + (more ? 6 : 10), {cache: 'no-store'});
      if (r.status === 401) { location.replace('/login'); return; }
      const data = await r.json();
      if (!r.ok) throw new Error(data.error || 'Could not load songs');
      const seen = new Set(queue.map(t => t.id));
      queue.push(...(data.tracks || []).filter(t => !seen.has(t.id)));
      paintCounts(data.stats);
      render();
    } catch (e) { say(e.message || 'Could not load songs'); }
  }

  function card(track, back) {
    const node = document.createElement('article');
    node.className = 'card' + (back ? ' back' : '');
    node.dataset.id = track.id;
    node.innerHTML = `
      <span class="stamp keep">keep</span><span class="stamp drop">drop</span><span class="stamp like">like</span>
      <div class="art">${track.artwork ? `<img src="${esc(track.artwork)}" alt="" draggable="false">` : esc((track.title || '?').slice(0, 2).toUpperCase())}</div>
      <div class="meta"><b>${esc(track.title || 'Untitled')}</b>
        <span>${esc(track.artist_name || '—')}${track.album ? ' · ' + esc(track.album) : ''}${track.duration ? ' · ' + clock(track.duration) : ''}</span>
        <div class="tags">${(track.genres || []).slice(0, 4).map(g => `<i>${esc(g)}</i>`).join('')}${track.bpm ? `<i>${Math.round(track.bpm)} bpm</i>` : ''}</div>
      </div>`;
    if (!back) bind(node, track);
    return node;
  }

  function render() {
    deck.replaceChildren();
    const top = queue[0];
    if (!top) { empty.hidden = false; return; }
    empty.hidden = true;
    if (queue[1]) deck.append(card(queue[1], true));
    const front = card(top, false);
    deck.append(front);
    front.style.transform = 'translateY(0) rotate(0deg)';
  }

  function stamp(node, kind, amount) {
    const el = node.querySelector('.stamp.' + kind);
    if (el) el.style.opacity = String(Math.min(1, amount));
  }

  function bind(node, track) {
    let drag = null;
    const reset = () => {
      node.style.transition = 'transform .2s ease';
      node.style.transform = 'translateY(0) rotate(0deg)';
      node.querySelectorAll('.stamp').forEach(s => (s.style.opacity = '0'));
    };
    node.addEventListener('pointerdown', event => {
      if (event.button > 0) return;
      drag = {x: event.clientX, y: event.clientY, id: event.pointerId, moved: false, dx: 0, dy: 0};
      node.setPointerCapture?.(event.pointerId);
      node.style.transition = 'none';
    });
    node.addEventListener('pointermove', event => {
      if (!drag || event.pointerId !== drag.id) return;
      drag.dx = event.clientX - drag.x;
      drag.dy = event.clientY - drag.y;
      if (Math.hypot(drag.dx, drag.dy) > 6) drag.moved = true;
      if (!drag.moved) return;
      node.style.transform = `translate(${drag.dx}px, ${drag.dy}px) rotate(${drag.dx / 22}deg)`;
      const up = drag.dy < 0 && Math.abs(drag.dy) > Math.abs(drag.dx);
      stamp(node, 'like', up ? Math.abs(drag.dy) / 110 : 0);
      stamp(node, 'keep', !up && drag.dx > 0 ? drag.dx / 110 : 0);
      stamp(node, 'drop', !up && drag.dx < 0 ? -drag.dx / 110 : 0);
    });
    const end = event => {
      if (!drag || (event && event.pointerId !== drag.id)) return;
      const {dx, dy, moved} = drag;
      drag = null;
      if (!moved) { togglePlay(track); reset(); return; }
      const up = dy < 0 && Math.abs(dy) > Math.abs(dx);
      if (up && Math.abs(dy) > 80) return flip(node, track, 'like');
      if (Math.abs(dx) > 90) return flip(node, track, dx > 0 ? 'keep' : 'drop');
      reset();
    };
    node.addEventListener('pointerup', end);
    node.addEventListener('pointercancel', end);
  }

  function flip(node, track, decision) {
    const away = decision === 'like' ? 'translateY(-120%) rotate(0deg)'
      : decision === 'keep' ? 'translateX(120%) rotate(18deg)' : 'translateX(-120%) rotate(-18deg)';
    node.style.transition = 'transform .24s ease, opacity .24s ease';
    node.style.transform = away;
    node.style.opacity = '0';
    decide(track, decision);
  }

  async function decide(track, decision) {
    if (busy) return;
    busy = true;
    queue.shift();
    if (playing === track.id) stopPlay();
    render();
    if (queue.length < 3) load(true);
    try {
      const r = await fetch('/api/swipe/decide', {
        method: 'POST', headers: {'Content-Type': 'application/json'},
        body: JSON.stringify({id: track.id, decision}),
      });
      const data = await r.json().catch(() => ({}));
      if (!r.ok) throw new Error(data.error || 'Could not save that');
      paintCounts(data.stats);
    } catch (e) {
      say(e.message || 'Could not save that');
    } finally {
      busy = false;
    }
  }

  function stopPlay() {
    audio.pause();
    audio.removeAttribute('src');
    playing = null;
    deck.querySelectorAll('.card.playing').forEach(n => n.classList.remove('playing'));
  }
  function togglePlay(track) {
    if (playing === track.id) { stopPlay(); return; }
    stopPlay();
    audio.src = `/api/track/${track.id}/audio`;
    audio.play().then(() => {
      playing = track.id;
      deck.querySelector(`.card[data-id="${track.id}"]`)?.classList.add('playing');
    }).catch(e => say(e.message || 'Could not play that'));
  }

  document.getElementById('keep').onclick = () => queue[0] && flip(deck.lastElementChild, queue[0], 'keep');
  document.getElementById('drop').onclick = () => queue[0] && flip(deck.lastElementChild, queue[0], 'drop');
  document.getElementById('like').onclick = () => queue[0] && flip(deck.lastElementChild, queue[0], 'like');
  window.addEventListener('keydown', event => {
    if (!queue[0] || event.metaKey || event.ctrlKey) return;
    if (event.key === 'ArrowRight') { event.preventDefault(); flip(deck.lastElementChild, queue[0], 'keep'); }
    else if (event.key === 'ArrowLeft') { event.preventDefault(); flip(deck.lastElementChild, queue[0], 'drop'); }
    else if (event.key === 'ArrowUp') { event.preventDefault(); flip(deck.lastElementChild, queue[0], 'like'); }
    else if (event.key === ' ') { event.preventDefault(); togglePlay(queue[0]); }
  });

  load(false);
})();
