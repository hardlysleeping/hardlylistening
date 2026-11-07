/* Lyrics use the existing player only: no audio creation, autoplay or frame loop. */
(() => {
  const audio = document.getElementById('player-audio');
  const button = document.getElementById('pllyrics');
  const dialog = document.getElementById('dlg-lyrics');
  const status = document.getElementById('lyrics-status');
  const body = document.getElementById('lyrics-body');
  const karaoke = document.getElementById('lyrics-karaoke');
  let data = null, nodes = [], current = -2, request = null, generation = 0;
  const events = ['timeupdate', 'seeking', 'seeked', 'ratechange', 'loadedmetadata'];
  function paint() {
    if (!dialog.open || !data || data.status !== 'synced') return;
    let index = -1;
    for (let i = 0; i < data.lines.length && data.lines[i].time <= audio.currentTime; i++) index = i;
    if (index === current) return;
    current = index;
    nodes.forEach((node, i) => {
      node.classList.toggle('current', i === index);
      node.classList.toggle('next', i === index + 1);
      if (i === index) node.setAttribute('aria-current', 'true'); else node.removeAttribute('aria-current');
    });
    if (!dialog.classList.contains('karaoke') && nodes[index]) nodes[index].scrollIntoView({block:'nearest', behavior:'instant'});
  }
  async function load() {
    if (request) request.abort();
    const seq = ++generation;
    data = null; nodes = []; current = -2; body.replaceChildren();
    karaoke.disabled = true; dialog.classList.remove('karaoke'); karaoke.setAttribute('aria-pressed', 'false');
    const match = audio.getAttribute('src')?.match(/^\/api\/track\/(\d+)\/audio$/);
    if (!match) { status.textContent = 'Choose a track in the player first.'; return; }
    status.textContent = 'Looking up lyrics…';
    request = new AbortController();
    try {
      const response = await fetch(`/api/track/${match[1]}/lyrics`, {signal:request.signal, cache:'no-store'});
      if (!response.ok) throw new Error('Lyrics lookup unavailable. Sign in and try again.');
      const result = await response.json();
      if (seq !== generation || !dialog.open) return;
      data = result;
      const messages = {synced:'Synchronized lyrics · line timing, not word timing', plain:'Plain lyrics · synchronized timing unavailable', instrumental:'Provider marks this track instrumental · no lyrics', not_found:'No matching lyrics found', unavailable:'Lyrics provider unavailable · try again later'};
      status.textContent = (messages[data.status] || messages.unavailable) + (data.source ? ` — ${data.source}` : '');
      if (data.status === 'synced') {
        nodes = data.lines.map(line => { const node = document.createElement('p'); node.textContent = line.text || '♪'; body.append(node); return node; });
        karaoke.disabled = false; paint();
      } else if (data.status === 'plain') { const node = document.createElement('pre'); node.textContent = data.text; body.append(node); }
    } catch (error) {
      if (seq === generation && dialog.open && error.name !== 'AbortError') status.textContent = error.message || 'Lyrics unavailable';
    }
  }
  function close() {
    generation++; if (request) request.abort(); request = null;
    events.forEach(event => audio.removeEventListener(event, paint));
    audio.removeEventListener('loadstart', load);
    data = null; nodes = []; body.replaceChildren();
    dialog.classList.remove('karaoke');
    if (document.fullscreenElement === dialog && document.exitFullscreen) document.exitFullscreen().catch(() => {});
  }
  button.addEventListener('click', () => {
    if (dialog.open) return;
    dialog.showModal(); events.forEach(event => audio.addEventListener(event, paint));
    audio.addEventListener('loadstart', load); load();
  });
  dialog.addEventListener('close', close);
  document.getElementById('lyrics-close').addEventListener('click', () => dialog.close());
  karaoke.addEventListener('click', () => {
    const on = !dialog.classList.contains('karaoke');
    dialog.classList.toggle('karaoke', on); karaoke.setAttribute('aria-pressed', String(on));
    current = -2; paint();
  });
  document.getElementById('lyrics-fullscreen').addEventListener('click', () => {
    if (document.fullscreenElement === dialog) document.exitFullscreen?.().catch(() => {});
    else dialog.requestFullscreen?.().catch(() => { status.textContent += ' · Fullscreen unavailable; use karaoke view.'; });
  });
})();
