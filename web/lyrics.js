/* Lyrics use the existing player only: no audio creation, autoplay or frame loop.

   Three readings of the same track: line-timed (`[mm:ss.xx]`), word-timed (an enhanced-LRC
   line that carries `<mm:ss.xx>` before each word), and plain text. A plain file still falls
   back exactly as it did; a word-timed file highlights the word being sung, and a line-timed
   file keeps the line highlight it always had. Editing is a member's correction stored with
   the track (POST /api/track/<id>/lyrics), so it survives a reload and a provider refresh. */
(() => {
  const audio = document.getElementById('player-audio');
  const button = document.getElementById('pllyrics');
  const fullButton = document.getElementById('pflyrics');
  const dialog = document.getElementById('lyrics-overlay');
  const status = document.getElementById('lyrics-status');
  const body = document.getElementById('lyrics-body');
  const karaoke = document.getElementById('lyrics-karaoke');
  const editButton = document.getElementById('lyrics-edit');
  const editDialog = document.getElementById('dlg-lyrics-edit');
  const editForm = document.getElementById('lyrics-edit-form');
  const editText = document.getElementById('lyrics-edit-text');
  const editStatus = document.getElementById('lyrics-edit-status');
  const resetButton = document.getElementById('lyrics-edit-reset');
  let data = null, nodes = [], current = -2, request = null, generation = 0;
  const events = ['timeupdate', 'seeking', 'seeked', 'ratechange', 'loadedmetadata'];
  const editable = ['synced', 'plain'];

  function trackId(){
    const match = audio.getAttribute('src')?.match(/^\/api\/track\/(\d+)\/audio$/);
    return match?.[1] || (typeof playingId !== 'undefined' ? playingId : null);
  }
  function setAvailable(available){
    for (const control of [button, fullButton, editButton]){
      if (!control) continue;
      control.disabled = !available;
      control.setAttribute('aria-disabled', String(!available));
      control.classList.toggle('lyrics-ready', available);
    }
  }
  // ---- painting ------------------------------------------------------------
  // One line, one node. A word-timed line is a run of spans, so the word being sung can lift
  // without rebuilding the line on every tick.
  function lineNode(line){
    const node = document.createElement('p');
    if (line.words?.length){
      node.classList.add('wordline');
      line.words.forEach((word, index) => {
        const span = document.createElement('span');
        span.className = 'w';
        span.textContent = word.text;
        node.append(span);
        if (index < line.words.length - 1) node.append(' ');
      });
    } else {
      node.textContent = line.text || '♪';
    }
    return node;
  }
  function wordProgress(index, at){
    const line = data?.lines?.[index], node = nodes[index];
    if (!line?.words?.length || !node) return;
    const spans = node.querySelectorAll('.w');
    let sung = -1;
    for (let i = 0; i < line.words.length && line.words[i].time <= at; i++) sung = i;
    spans.forEach((span, i) => {
      span.classList.toggle('on', i <= sung);
      span.classList.toggle('current', i === sung);
    });
  }
  function clearWords(index){
    const node = nodes[index];
    if (node) node.querySelectorAll('.w').forEach(span => span.classList.remove('on', 'current'));
  }
  function paint(){
    if (dialog.hidden || !data || data.status !== 'synced') return;
    const at = audio.currentTime;
    let index = -1;
    for (let i = 0; i < data.lines.length && data.lines[i].time <= at; i++) index = i;
    // The current line's words advance inside the line, so they are painted on every tick,
    // not only when the line changes. That is one line's spans, not the whole document.
    wordProgress(index, at);
    if (index === current) return;
    clearWords(current);
    current = index;
    nodes.forEach((node, i) => {
      node.classList.toggle('current', i === index);
      node.classList.toggle('next', i === index + 1);
      if (i === index) node.setAttribute('aria-current', 'true'); else node.removeAttribute('aria-current');
    });
    if (!dialog.classList.contains('karaoke') && nodes[index]) nodes[index].scrollIntoView({block:'nearest', behavior:'smooth'});
  }
  function message(){
    if (!data) return '';
    if (data.status === 'synced') return data.word_timed
      ? 'Synchronized lyrics · word timing'
      : 'Synchronized lyrics · line timing, not word timing';
    return ({plain:'Plain lyrics · synchronized timing unavailable',
             instrumental:'Provider marks this track instrumental · no lyrics',
             not_found:'No matching lyrics found',
             unavailable:'Lyrics provider unavailable · try again later'})[data.status] || 'Lyrics unavailable';
  }
  // Everything drawn from one payload: the load path and the edit path answer with the same
  // shape, so a saved correction repaints through exactly the code a fresh lookup uses.
  function render(){
    body.replaceChildren();
    nodes = []; current = -2;
    status.textContent = message() + (data?.source ? ` — ${data.source}` : '')
      + (data?.editor ? ` · edited by ${data.editor}` : '');
    karaoke.disabled = data?.status !== 'synced';
    if (karaoke.disabled){ dialog.classList.remove('karaoke'); karaoke.setAttribute('aria-pressed','false'); }
    setAvailable(editable.includes(data?.status));
    if (data?.status === 'synced'){
      nodes = data.lines.map(line => { const node = lineNode(line); body.append(node); return node; });
      paint();
    } else if (data?.status === 'plain'){
      const node = document.createElement('pre');
      node.textContent = data.text;
      body.append(node);
    }
  }
  async function load(){
    if (request) request.abort();
    const seq = ++generation;
    data = null; nodes = []; current = -2; body.replaceChildren();
    setAvailable(false);
    dialog.hidden = true;
    if (editDialog?.open) editDialog.close();
    karaoke.disabled = true; dialog.classList.remove('karaoke'); karaoke.setAttribute('aria-pressed', 'false');
    const id = trackId();
    if (!id) { status.textContent = 'Choose a track in the player first.'; return; }
    status.textContent = 'Looking up lyrics…';
    request = new AbortController();
    try {
      const response = await fetch(`/api/track/${id}/lyrics`, {signal:request.signal, cache:'no-store'});
      if (!response.ok) throw new Error('Lyrics lookup unavailable. Sign in and try again.');
      const result = await response.json();
      if (seq !== generation) return;
      data = result;
      render();
    } catch (error) {
      if (seq === generation && error.name !== 'AbortError') { status.textContent = error.message || 'Lyrics unavailable'; setAvailable(false); }
    }
  }
  // ---- editing -------------------------------------------------------------
  // The editor speaks the file's own format, which is why a saved correction keeps its line
  // and word timings: the member is looking at the timestamps, not at a flattened text.
  const stamp = value => {
    const centis = Math.round(Math.max(0, value) * 100);
    const minutes = Math.floor(centis / 6000), rest = centis - minutes * 6000;
    return `${String(minutes).padStart(2,'0')}:${String(Math.floor(rest / 100)).padStart(2,'0')}.${String(rest % 100).padStart(2,'0')}`;
  };
  function asLrc(){
    if (!data) return '';
    if (data.status !== 'synced') return data.text || '';
    return data.lines.map(line => line.words?.length
      ? `[${stamp(line.time)}]` + line.words.map(word => `<${stamp(word.time)}>${word.text}`).join(' ')
      : `[${stamp(line.time)}]${line.text}`).join('\n');
  }
  function openEditor(){
    if (!data || !editable.includes(data.status) || !editDialog) return;
    // The editor is a dialog, and a dialog opened while the player is fullscreen would be
    // behind it; leaving fullscreen first keeps one surface in front at a time.
    if (document.fullscreenElement) document.exitFullscreen?.().catch(() => {});
    editText.value = asLrc();
    // The dialog itself explains the format; this line only says who is being overridden.
    // Repeating the format twice on one small dialog is noise, not help.
    editStatus.textContent = data.editor
      ? `Edited by ${data.editor}. Saving replaces that correction; "Use the original" brings the provider copy back.`
      : '';
    editDialog.showModal();
    editText.focus();
  }
  async function save(body){
    const id = trackId();
    if (!id || !editStatus) return;
    editStatus.textContent = 'Saving…';
    try {
      const response = await fetch(`/api/track/${id}/lyrics`, {
        method: 'POST', headers: {'Content-Type': 'application/json'}, body: JSON.stringify(body)});
      const result = await response.json().catch(() => ({}));
      if (!response.ok) throw new Error(result.error || 'Could not save those lyrics.');
      data = result;
      editDialog.close();
      render();
    } catch (error) {
      editStatus.textContent = error.message || 'Could not save those lyrics.';
    }
  }
  function close() {
    dialog.hidden = true;
    if (document.fullscreenElement === dialog) document.exitFullscreen?.().catch(() => {});
    events.forEach(event => audio.removeEventListener(event, paint));
    dialog.classList.remove('on','karaoke');
  }
  audio.addEventListener('loadstart', load);
  setAvailable(false);
  button.addEventListener('click', () => {
    if (button.disabled || !data) return;
    if (!dialog.hidden) { close(); return; }
    dialog.hidden = false; dialog.classList.add('on');
    if (typeof window.openFullPlayer === 'function') window.openFullPlayer();
    events.forEach(event => audio.addEventListener(event, paint));
    current = -2; paint();
  });
  document.getElementById('lyrics-close').addEventListener('click', close);
  karaoke.addEventListener('click', () => {
    const on = !dialog.classList.contains('karaoke');
    dialog.classList.toggle('karaoke', on); karaoke.setAttribute('aria-pressed', String(on));
    current = -2; paint();
  });
  document.getElementById('lyrics-fullscreen').addEventListener('click', () => {
    if (document.fullscreenElement === dialog) document.exitFullscreen?.().catch(() => {});
    else dialog.requestFullscreen?.().catch(() => { status.textContent += ' · Fullscreen unavailable'; });
  });
  editButton?.addEventListener('click', openEditor);
  editForm?.addEventListener('submit', event => { event.preventDefault(); save({text: editText.value}); });
  resetButton?.addEventListener('click', () => save({reset: true}));
  if (audio.getAttribute('src')) load();
})();