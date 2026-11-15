/* Shared metadata labels; no inference or network inside render. */
window.Genres = (() => {
  let selected = [], taxonomy = [];
  let lastRows, byId = new Map();
  const matches = track => {
    if (!selected.length) return true;
    if (typeof libAll !== 'undefined' && lastRows !== libAll) {
      lastRows = libAll; byId = new Map(libAll.map(t => [t.id, t]));
    }
    return selected.some(tag => (track.genres || byId.get(track.id)?.genres || []).includes(tag));
  };
  async function post(path, body) {
    const response = await fetch(path, { method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify(body) });
    const data = await response.json();
    if (!response.ok) throw Error(data.error || 'Genre action failed');
    return data;
  }
  async function init() {
    const bar = document.querySelector('.libbar');
    if (!bar) return;
    // A chip on the filter row opens a popup; the taxonomy never takes a row of its own.
    const button = document.createElement('button');
    button.type = 'button'; button.id = 'genresbtn'; button.className = 'chip small';
    button.title = 'Filter by genre'; button.textContent = 'Genres';
    button.setAttribute('aria-haspopup', 'dialog');
    // The filter row is the row the Filter button opens: the chip appears with the other
    // filters, not beside the search box.
    (document.getElementById('libfilters') || bar).append(button);

    const pop = document.createElement('dialog');
    pop.id = 'genres-pop';
    const headRow = document.createElement('div'); headRow.className = 'ghead';
    const head = document.createElement('h2'); head.textContent = 'Genres';
    const clear = document.createElement('button');
    clear.type = 'button'; clear.id = 'genres-clear'; clear.className = 'gclear';
    clear.title = 'Clear genre filter'; clear.setAttribute('aria-label', 'Clear genre filter');
    clear.innerHTML = '<svg viewBox="0 0 24 24" width="15" height="15" fill="none" stroke="currentColor" ' +
      'stroke-width="2" stroke-linecap="round" aria-hidden="true"><path d="M6 6l12 12M18 6L6 18"/></svg>';
    // The X closes the sheet; clearing the filter is its own button in the footer, so the
    // close target does exactly what it looks like it does.
    clear.title = 'Close';
    clear.setAttribute('aria-label', 'Close genres');
    clear.onclick = () => pop.close();
    headRow.append(head, clear);
    const help = document.createElement('p'); help.className = 'gnote';
    help.textContent = 'Shared artist/title/album metadata estimates — not audio analysis. Any selected genre matches (OR); untagged songs only show when no genre is selected.';
    const labels = document.createElement('div'); labels.className = 'glabels';
    const status = document.createElement('p'); status.className = 'gnote'; status.setAttribute('role', 'status');
    const adminPanel = document.createElement('div'); adminPanel.className = 'gadmin';
    const foot = document.createElement('div'); foot.className = 'gfoot';
    const clearAll = document.createElement('button'); clearAll.type = 'button'; clearAll.className = 'ghost';
    clearAll.textContent = 'Clear'; clearAll.title = 'Clear genre filter';
    clearAll.onclick = () => {
      for (const input of labels.querySelectorAll('input:checked')) input.checked = false;
      selected = [];
      syncLabel();
      renderLibrary();
    };
    const done = document.createElement('button'); done.type = 'button'; done.className = 'primary'; done.textContent = 'Done';
    done.onclick = () => pop.close();
    foot.append(clearAll, done);
    pop.append(headRow, help, labels, status, adminPanel, foot);
    document.body.append(pop);

    const canTag = () => typeof adminUI === 'function' ? adminUI() : (typeof isAdmin === 'function' && isAdmin());
    button.onclick = () => { adminPanel.hidden = !canTag(); pop.showModal(); };
    const syncLabel = () => {
      button.textContent = selected.length ? `Genres (${selected.length})` : 'Genres';
      button.classList.toggle('on', selected.length > 0);
    };
    try {
      const response = await fetch('/api/genres'); const data = await response.json();
      if (!response.ok) throw Error(data.error || 'Genres unavailable');
      taxonomy = data.taxonomy;
      for (const tag of taxonomy) {
        const row = document.createElement('label'); const input = document.createElement('input');
        input.type = 'checkbox'; input.value = tag;
        input.onchange = () => {
          selected = [...labels.querySelectorAll('input:checked')].map(n => n.value);
          syncLabel();
          renderLibrary();
        };
        row.append(input, document.createTextNode(tag)); labels.append(row);
      }
      // Recheck on open: initial state may still be loading at window load.
      // Chrome gate, not a permission check: in "view as user" an administrator must not
      // see the tagging buttons either. The API stays admin-only regardless.
      const updateRole = () => { adminPanel.hidden = !canTag(); };
      document.addEventListener('hl:chrome', updateRole);
      updateRole();
      const ids = document.createElement('input');
      ids.placeholder = 'Explicit track IDs, e.g. 12,34'; ids.setAttribute('aria-label', 'Track IDs for genre actions');
      adminPanel.append(ids);
      const pick = document.createElement('select'); pick.multiple = true; pick.setAttribute('aria-label', 'Choose tracks for genre tagging');
      const fill = document.createElement('button'); fill.type = 'button'; fill.textContent = 'Load displayed tracks for selection';
      fill.onclick = () => { pick.replaceChildren(); for (const t of currentRows) { const option = document.createElement('option'); option.value = t.id; option.textContent = `${t.artist_name} — ${t.title} [${(t.genres || []).join(', ') || 'untagged'}]`; pick.append(option); } };
      pick.onchange = () => { ids.value = [...pick.selectedOptions].map(o => o.value).join(','); };
      adminPanel.append(fill, pick);
      const tags = document.createElement('input');
      tags.placeholder = 'Correction tags, comma-separated'; tags.setAttribute('aria-label', 'Replacement genre labels');
      adminPanel.append(tags);
      for (const [text, path] of [['Generate AI tags', 'generate'], ['Replace tags manually', 'correct']]) {
        const action = document.createElement('button'); action.type = 'button'; action.textContent = text;
        action.onclick = async () => {
          action.disabled = true;
          try {
            const chosen = ids.value.split(',').map(s => Number(s.trim()));
            const body = { ids: chosen }; if (path === 'correct') body.tags = tags.value.split(',').map(s => s.trim());
            await post('/api/genres/' + path, body);
            status.textContent = path === 'generate' ? 'Queued selected tracks. Use Check status, then refresh Library when complete.' : 'Shared correction saved. Refresh Library to see labels.';
          } catch (e) { status.textContent = e.message; } finally { action.disabled = false; }
        };
        adminPanel.append(action);
      }
      const check = document.createElement('button'); check.type = 'button'; check.textContent = 'Check tagging status';
      check.onclick = async () => { try { const r = await fetch('/api/genres'); const d = await r.json(); if (!r.ok) throw Error(d.error); status.textContent = JSON.stringify(d.status); } catch (e) { status.textContent = e.message; } };
      adminPanel.append(check);
    } catch (e) { status.textContent = e.message; }
  }
  // Initialize after the initial state request without adding polling.
  window.addEventListener('load', () => setTimeout(init, 1000), { once: true });
  return { matches };
})();
