/* Self-only preferences; no audio object or playback side effects. */
(() => {
  let state = null;
  const busy = new Set();
  const pinned = (kind, id) => !!state?.pins.some(p => p.kind === kind && p.target === String(id));
  const rank = (kind, a, b) => libraryScope === 'mine' ? Number(pinned(kind,b)) - Number(pinned(kind,a)) : 0;
  // A pin is an icon, not the word: the button keeps the same box in both states.
  const pinIcon = active => `<svg class="pinicon" width="14" height="14" viewBox="0 0 24 24" aria-hidden="true"
    fill="${active ? 'currentColor' : 'none'}" stroke="currentColor" stroke-width="1.9" stroke-linecap="round" stroke-linejoin="round"
    ><path d="M12 17v5"/><path d="M9 10.8a2 2 0 0 1-1.1 1.8l-1.8.9A2 2 0 0 0 5 15.2V16a1 1 0 0 0 1 1h12a1 1 0 0 0 1-1v-.8a2 2 0 0 0-1.1-1.8l-1.8-.9A2 2 0 0 1 15 10.8V7a1 1 0 0 1 1-1 2 2 0 0 0 0-4H8a2 2 0 0 0 0 4 1 1 0 0 1 1 1z"/></svg>`;
  const control = (action, kind, id) => {
    if (!state || (kind === 'album' && !/^[a-f0-9]{64}$/.test(id))) return '';
    const active = action === 'favorite' ? state.favorites.includes(Number(id)) : pinned(kind,id);
    const key = action+kind+id;
    const label = action === 'favorite' ? (active ? 'Unlike song' : 'Like song') : (active ? 'Unpin '+kind : 'Pin '+kind);
    const glyph = action === 'favorite' ? (active ? '♥' : '♡') : pinIcon(active);
    return `<button type="button" class="preference ${active ? 'active' : ''}" data-pref-action="${action}" data-pref-kind="${kind}" data-pref-id="${esc(String(id))}" aria-pressed="${active}" aria-label="${label}" title="${label}" ${busy.has(key) ? 'disabled' : ''}>${glyph}</button>`;
  };
  window.PreferenceUI = {rank};
  const originalLoad = loadListeningSaves;
  loadListeningSaves = async function(){
    await originalLoad();
    try { state = await listeningGet('preferences'); } catch (_) { state = null; }
  };
  const originalTracks = scopedTracks;
  scopedTracks = function(){
    const rows = originalTracks();
    if (libraryScope !== 'mine' || !state) return rows;
    const ids = new Set([...rows.map(t=>t.id), ...state.favorites]);
    return libAll.filter(t=>ids.has(t.id));
  };
  const originalSaveControl = listeningSaveControl;
  listeningSaveControl = function(kind,id){
    // One nowrap group: the heart and Pin must never be split onto separate lines.
    return originalSaveControl(kind,id) + `<span class="pref-pair">${kind === 'track' ? control('favorite',kind,id) : ''}${control('pin',kind,id)}</span>`;
  };
  const originalAlbumCard = albumCard;
  albumCard = function(album,index){
    const html = originalAlbumCard(album,index);
    const end = html.lastIndexOf('</div>');
    return html.slice(0,end) + control('pin','album',album.key) + html.slice(end);
  };
  const originalSortAlbums = sortAlbums;
  sortAlbums = albums => originalSortAlbums(albums).sort((a,b)=>rank('album',a.key,b.key));

  function favoritesPanel(){
    if (!state || libraryScope !== 'mine' || detail) return '';
    const rows = libAll.filter(t=>state.favorites.includes(t.id));
    return `<section class="favorites-playlist"><h3>Favorites <span>${rows.length} songs</span></h3><p>All your liked songs, automatically. Likes save songs to your Library; unliking keeps the save.</p><button type="button" data-open-favorites>Open Favorites</button></section>`;
  }
  function pinnedPanel(){
    if (!state || libraryScope !== 'mine' || detail) return '';
    const albums = albumsOf(libAll);
    const cards = state.pins.map(p=>{
      const item = p.kind === 'track' ? libAll.find(t=>String(t.id)===p.target) : p.kind === 'playlist' ? libPlaylists.find(t=>String(t.id)===p.target) : albums.find(a=>a.key===p.target);
      if (!item) return '';
      return `<div class="pinned-item"><button type="button" data-open-pin-kind="${p.kind}" data-open-pin-id="${esc(p.target)}"><small>${p.kind === 'track' ? 'Song' : p.kind === 'album' ? 'Album' : 'Playlist'}</small><b>${esc(item.title)}</b>${p.kind === 'track' ? `<span>${esc(item.artist_name || '')}</span>` : ''}</button>${control('pin',p.kind,p.target)}</div>`;
    }).join('');
    return `<section class="mixed-pinned"><h3>Pinned</h3><div class="pinned-grid">${cards || '<p>Pin songs, albums and playlists here. Pins save their current songs; unpinning keeps your saves.</p>'}</div></section>`;
  }
  const originalRender = renderLibrary;
  renderLibrary = function(){
    originalRender();
    const host = document.querySelector('#liblist');
    if (!host) return;
    host.querySelectorAll('.mixed-pinned,.favorites-playlist').forEach(n=>n.remove());
    if (libraryScope === 'mine' && !detail){
      // Markup is fixed templates; every catalog title/id is escaped above.
      host.insertAdjacentHTML('afterbegin', pinnedPanel()+favoritesPanel());
    }
  };
  let favoritesOpen = false;
  const originalDetail = renderDetail;
  renderDetail = function(rows){
    if (detail?.kind === 'favorites') {
      favoritesOpen = true;
      const favorites = libAll.filter(t=>state?.favorites.includes(t.id));
      return `<button type="button" data-close-favorites>← Library</button><h2>Favorites</h2><p>${favorites.length} liked songs</p>` + renderSongs(favorites,'');
    }
    favoritesOpen = false;
    return originalDetail(rows);
  };
  // Capture before row/card inline handlers: personal actions never start playback.
  document.addEventListener('click', async event => {
    const button = event.target.closest('[data-pref-action],[data-open-favorites],[data-close-favorites],[data-open-pin-kind]');
    if (!button) return;
    event.preventDefault(); event.stopPropagation(); event.stopImmediatePropagation();
    if (button.hasAttribute('data-open-favorites')) { detail={kind:'favorites'}; renderLibrary(); return; }
    if (button.hasAttribute('data-close-favorites')) { detail=null; renderLibrary(); return; }
    if (button.dataset.openPinKind){
      const kind=button.dataset.openPinKind, id=button.dataset.openPinId;
      if (kind==='track') { detail=null; libView='songs'; $('#libsearch').value=libAll.find(t=>String(t.id)===id)?.title || ''; }
      else if (kind==='album') detail={kind:'album',key:id};
      else detail={kind:'playlist',id:Number(id)};
      // Pins remain accessible repository references after an explicit save removal.
      if (kind !== 'track' && (kind === 'album' ? !albumsOf(scopedTracks()).some(a=>a.key===id) : !scopedPlaylists().some(p=>String(p.id)===id))) setLibraryScope('shared');
      if (kind==='album') detail={kind:'album',key:id};
      if (kind==='playlist') { openPlaylist(Number(id)); return; }
      renderLibrary(); return;
    }
    const {prefAction:action,prefKind:kind,prefId:raw}=button.dataset;
    const key=action+kind+raw;
    if (!state || busy.has(key)) return;
    const id=kind==='album' ? raw : Number(raw);
    const enabled=action==='favorite' ? !state.favorites.includes(id) : !pinned(kind,raw);
    busy.add(key); button.disabled=true;
    try {
      state=await api('/api/listening/preferences',{action,kind,id,enabled});
      await loadListeningSaves();
    } catch(e){ showToast(e.message); }
    finally { busy.delete(key); renderLibrary(); }
  }, true);
})();
