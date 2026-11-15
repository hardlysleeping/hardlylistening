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
  let current = null;    // the track the bar is showing
  const auditions = [];  // what has been played, newest last, for prev

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
    BAR.next.disabled = !queue[1];
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

  // ---------------------------------------------------------------- the compact player
  // The library's own bar (`styles/components/player.css`), driven here by the one <audio>
  // this page owns. The deck decides what plays; the bar shows it, so swiping the playing
  // card away takes the bar with it instead of leaving a song playing with no card.
  const ICON = {
    play: s => `<svg width="${s}" height="${s}" viewBox="0 0 24 24" fill="currentColor"><path d="M8 5.14v13.72a1 1 0 0 0 1.53.85l10.5-6.86a1 1 0 0 0 0-1.7L9.53 4.29A1 1 0 0 0 8 5.14Z"/></svg>`,
    pause: s => `<svg width="${s}" height="${s}" viewBox="0 0 24 24" fill="currentColor"><rect x="6" y="5" width="4" height="14" rx="1.2"/><rect x="14" y="5" width="4" height="14" rx="1.2"/></svg>`,
    prev: s => `<svg width="${s}" height="${s}" viewBox="0 0 24 24" fill="currentColor"><rect x="5" y="5" width="2.6" height="14" rx="1"/><path d="M19 5.9v12.2a1 1 0 0 1-1.55.83l-8.4-6.1a1 1 0 0 1 0-1.66l8.4-6.1A1 1 0 0 1 19 5.9Z"/></svg>`,
    next: s => `<svg width="${s}" height="${s}" viewBox="0 0 24 24" fill="currentColor"><rect x="16.4" y="5" width="2.6" height="14" rx="1"/><path d="M5 5.9v12.2a1 1 0 0 0 1.55.83l8.4-6.1a1 1 0 0 0 0-1.66l-8.4-6.1A1 1 0 0 0 5 5.9Z"/></svg>`,
    volume: s => `<svg width="${s}" height="${s}" viewBox="0 0 24 24" fill="currentColor"><path d="M4 9v6h3.5L13 19V5L7.5 9H4Z"/><path d="M16 8.5a5 5 0 0 1 0 7" fill="none" stroke="currentColor" stroke-width="1.8" stroke-linecap="round"/></svg>`,
    // The fader's speaker, with a second wave: the mute flag's one-wave speaker and its
    // crossed twin sit right beside it, so three identical glyphs would say nothing.
    volumeUp: s => `<svg width="${s}" height="${s}" viewBox="0 0 24 24" fill="currentColor"><path d="M4 9v6h3.5L13 19V5L7.5 9H4Z"/><path d="M15.6 9.4a4 4 0 0 1 0 5.2M18.2 7a7 7 0 0 1 0 10" fill="none" stroke="currentColor" stroke-width="1.8" stroke-linecap="round"/></svg>`,
    mute: s => `<svg width="${s}" height="${s}" viewBox="0 0 24 24" fill="currentColor"><path d="M4 9v6h3.5L13 19V5L7.5 9H4Z"/><path d="m16 9 5 6M21 9l-5 6" fill="none" stroke="currentColor" stroke-width="1.8" stroke-linecap="round"/></svg>`,
  };
  const BAR = {
    root: document.getElementById('player'),
    art: document.getElementById('plart'),
    title: document.getElementById('pltitle'),
    sub: document.getElementById('plsub'),
    prev: document.getElementById('plprev'),
    play: document.getElementById('plplay'),
    next: document.getElementById('plnext'),
    mute: document.getElementById('plmute'),
    more: document.getElementById('plvolmore'),
    menu: document.getElementById('plvolmenu'),
    fader: document.getElementById('plvolv'),
    seek: document.getElementById('plseek'),
    time: document.getElementById('pltime'),
    dur: document.getElementById('pldur'),
  };
  const VOL_KEY = 'hl.volume';
  // `stamp` is the swipe-card's badge, so the clock reads its own name here.
  const mmss = s => {
    const n = Math.max(0, Math.floor(Number(s) || 0));
    return `${Math.floor(n / 60)}:${String(n % 60).padStart(2, '0')}`;
  };
  let scrubbing = false;

  function paintPlay() {
    const paused = !audio.src || audio.paused;
    BAR.play.innerHTML = paused ? ICON.play(14) : ICON.pause(14);
    BAR.play.title = paused ? 'Play' : 'Pause';
    BAR.play.setAttribute('aria-label', paused ? 'Play' : 'Pause');
  }
  function paintTime() {
    const total = Number.isFinite(audio.duration) ? audio.duration : 0;
    BAR.dur.textContent = mmss(total);
    BAR.time.textContent = mmss(audio.currentTime);
    const pct = total ? Math.min(100, (audio.currentTime / total) * 100) : 0;
    BAR.seek.style.setProperty('--bar-played', pct.toFixed(2) + '%');
    if (!scrubbing) BAR.seek.value = String(Math.round(pct * 10));
  }
  function paintVolume() {
    const pct = Math.round((audio.muted ? 0 : audio.volume) * 100);
    BAR.fader.value = String(pct);
    BAR.fader.style.setProperty('--vol', pct + '%');
    BAR.mute.innerHTML = audio.muted ? ICON.mute(16) : ICON.volume(16);
    BAR.mute.classList.toggle('on', audio.muted);
    BAR.mute.setAttribute('aria-pressed', String(audio.muted));
  }
  function paintBar(track) {
    current = track;
    BAR.title.textContent = track.title || 'Untitled';
    BAR.title.title = BAR.title.textContent;
    BAR.sub.textContent = [track.artist_name, track.album].filter(Boolean).join(' · ');
    if (track.artwork) BAR.art.src = track.artwork;
    else BAR.art.removeAttribute('src');
    // The same `has-art` flag the library sets: it is what carries the toned surface that
    // matches fullscreen, and without it this bar would be the older, lighter glass.
    BAR.root.classList.toggle('has-art', !!track.artwork);
    BAR.root.classList.add('on');
    paintPlay();
  }

  // The volume panel is placed from the button's box because the bar clips its own overflow
  // and the panel has to sit outside it, exactly as it does in the library.
  function closeVolumeMenu() {
    BAR.menu.classList.remove('open');
    BAR.more.classList.remove('on');
    BAR.more.setAttribute('aria-expanded', 'false');
  }
  function placeVolumeMenu() {
    const b = BAR.more.getBoundingClientRect(), size = BAR.menu.getBoundingClientRect(), pad = 8;
    const left = Math.min(Math.max(b.left + b.width / 2 - size.width / 2, pad),
      Math.max(pad, innerWidth - pad - size.width));
    BAR.menu.style.left = Math.round(left) + 'px';
    BAR.menu.style.bottom = Math.round(innerHeight - b.top + 10) + 'px';
  }
  BAR.more.onclick = event => {
    event.stopPropagation();
    const open = !BAR.menu.classList.contains('open');
    if (open) placeVolumeMenu();
    BAR.menu.classList.toggle('open', open);
    BAR.more.classList.toggle('on', open);
    BAR.more.setAttribute('aria-expanded', String(open));
  };
  document.addEventListener('click', event => {
    if (BAR.menu.classList.contains('open') && !BAR.menu.contains(event.target)) closeVolumeMenu();
  });
  document.addEventListener('keydown', event => {
    if (event.key === 'Escape') closeVolumeMenu();
  });
  window.addEventListener('resize', closeVolumeMenu);

  // One level for every song, the same key the library uses, so a level chosen on either
  // page is the level this page plays at.
  audio.volume = Math.min(1, Math.max(0,
    Number(localStorage.getItem(VOL_KEY) ?? 0.85) || 0));
  audio.addEventListener('play', paintPlay);
  audio.addEventListener('pause', paintPlay);
  audio.addEventListener('ended', paintPlay);
  audio.addEventListener('loadedmetadata', paintTime);
  audio.addEventListener('timeupdate', paintTime);
  audio.addEventListener('durationchange', paintTime);
  audio.addEventListener('volumechange', paintVolume);

  function stopPlay() {
    audio.pause();
    audio.removeAttribute('src');
    playing = null;
    current = null;
    BAR.root.classList.remove('on', 'has-art');
    BAR.seek.value = '0';
    BAR.seek.style.setProperty('--bar-played', '0%');
    BAR.time.textContent = mmss(0);
    BAR.dur.textContent = mmss(0);
    deck.querySelectorAll('.card.playing').forEach(n => n.classList.remove('playing'));
  }
  // The one way a song starts: the deck and the bar both come through here, so the card's
  // `playing` state and the bar's title can never disagree.
  function play(track, remember) {
    if (!track) return;
    stopPlay();
    if (remember) auditions.push(track);
    audio.src = `/api/track/${track.id}/audio`;
    paintBar(track);
    audio.play().then(() => {
      playing = track.id;
      deck.querySelector(`.card[data-id="${track.id}"]`)?.classList.add('playing');
      paintPlay();
    }).catch(e => say(e.message || 'Could not play that'));
  }
  function togglePlay(track) {
    if (playing === track.id) { stopPlay(); return; }
    play(track, true);
  }

  BAR.play.onclick = () => {
    if (!audio.src) { if (queue[0]) togglePlay(queue[0]); return; }
    if (audio.paused) audio.play().catch(e => say(e.message || 'Could not play that'));
    else audio.pause();
  };
  // A player's previous: back to the top of this song, or to the one auditioned before it.
  BAR.prev.onclick = () => {
    if (!current) return;
    if (audio.currentTime > 3 || auditions.length < 2) {
      audio.currentTime = 0;
      paintTime();
      return;
    }
    auditions.pop();
    play(auditions[auditions.length - 1], false);
  };
  // Next auditions the card under this one without swiping it: the deck is a feed to listen
  // through, and a keep/drop is still the member's call.
  BAR.next.onclick = () => { if (queue[1]) play(queue[1], true); };
  BAR.mute.onclick = () => { audio.muted = !audio.muted; };
  BAR.fader.addEventListener('input', () => {
    const value = Number(BAR.fader.value) / 100;
    audio.muted = false;
    audio.volume = value;
    localStorage.setItem(VOL_KEY, String(value));
  });
  BAR.seek.addEventListener('pointerdown', () => { scrubbing = true; });
  const endScrub = () => { scrubbing = false; paintTime(); };
  BAR.seek.addEventListener('pointerup', endScrub);
  BAR.seek.addEventListener('pointercancel', endScrub);
  BAR.seek.addEventListener('input', event => {
    if (audio.duration) audio.currentTime = (Number(event.target.value) / 1000) * audio.duration;
    paintTime();
  });

  document.getElementById('keep').onclick = () => queue[0] && flip(deck.lastElementChild, queue[0], 'keep');
  document.getElementById('drop').onclick = () => queue[0] && flip(deck.lastElementChild, queue[0], 'drop');
  document.getElementById('like').onclick = () => queue[0] && flip(deck.lastElementChild, queue[0], 'like');
  window.addEventListener('keydown', event => {
    if (!queue[0] || event.metaKey || event.ctrlKey) return;
    // The bar brings form controls with it. Arrow keys on the scrub seek and Space presses
    // the focused button; without this the same key would also drop or keep the card.
    if (event.target.closest('input, select, textarea, button')) return;
    if (event.key === 'ArrowRight') { event.preventDefault(); flip(deck.lastElementChild, queue[0], 'keep'); }
    else if (event.key === 'ArrowLeft') { event.preventDefault(); flip(deck.lastElementChild, queue[0], 'drop'); }
    else if (event.key === 'ArrowUp') { event.preventDefault(); flip(deck.lastElementChild, queue[0], 'like'); }
    else if (event.key === ' ') { event.preventDefault(); togglePlay(queue[0]); }
  });

  // Boot paint: the bar's glyphs, then the level it will play at.
  BAR.prev.innerHTML = ICON.prev(20);
  BAR.next.innerHTML = ICON.next(20);
  BAR.play.innerHTML = ICON.play(14);
  BAR.more.innerHTML = ICON.volumeUp(16);
  paintVolume();
  paintTime();

  load(false);
})();
