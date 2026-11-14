/* Self-only preferences; no audio object or playback side effects. */
(() => {
  let state = null;
  const busy = new Set();
  const pinned = (kind, id) => !!state?.pins.some(p => p.kind === kind && p.target === String(id));
  // What the tiles and rows read to draw the corner marks: liked, pinned, or neither.
  const marked = (kind, id) => ({
    liked: !!state && (kind === 'album' ? (state.album_likes || []).includes(String(id))
                                      : (state.favorites || []).includes(Number(id))),
    pinned: pinned(kind, id),
  });
  const rank = (kind, a, b) => libraryScope === 'mine' ? Number(pinned(kind,b)) - Number(pinned(kind,a)) : 0;
  // A pin is an icon, not the word: the button keeps the same box in both states.
  const pinIcon = active => `<svg class="pinicon" width="14" height="14" viewBox="0 0 24 24" aria-hidden="true"
    fill="${active ? 'currentColor' : 'none'}" stroke="currentColor" stroke-width="1.9" stroke-linecap="round" stroke-linejoin="round"
    ><path d="M12 17v5"/><path d="M9 10.8a2 2 0 0 1-1.1 1.8l-1.8.9A2 2 0 0 0 5 15.2V16a1 1 0 0 0 1 1h12a1 1 0 0 0 1-1v-.8a2 2 0 0 0-1.1-1.8l-1.8-.9A2 2 0 0 1 15 10.8V7a1 1 0 0 1 1-1 2 2 0 0 0 0-4H8a2 2 0 0 0 0 4 1 1 0 0 1 1 1z"/></svg>`;
  const PIN_LIMIT = 18;
  const control = (action, kind, id) => {
    if (!state || (kind === 'album' && !/^[a-f0-9]{64}$/.test(id))) return '';
    const active = action === 'favorite' ? state.favorites.includes(Number(id))
      : action === 'like' ? (state.album_likes || []).includes(String(id))
      : pinned(kind,id);
    const key = action+kind+id;
    const full = action === 'pin' && !active && state.pins.length >= PIN_LIMIT;
    const label = action === 'favorite' ? (active ? 'Unlike song' : 'Like song')
      : action === 'like' ? (active ? 'Unlike album' : 'Like album')
      : active ? 'Unpin '+kind : full ? `Up to ${PIN_LIMIT} pins — unpin one first` : 'Pin '+kind;
    const glyph = (action === 'favorite' || action === 'like') ? (active ? '♥' : '♡') : pinIcon(active);
    return `<button type="button" class="preference ${active ? 'active' : ''}" data-pref-action="${action}" data-pref-kind="${kind}" data-pref-id="${esc(String(id))}" aria-pressed="${active}" aria-label="${label}" title="${label}" ${busy.has(key) || full ? 'disabled' : ''}>${glyph}</button>`;
  };
  window.PreferenceUI = {rank, togglePinned, syncPinnedState, applyServerPins, marked, control}
  const originalLoad = loadListeningSaves;
  loadListeningSaves = async function(){
    await originalLoad();
    try { state = await listeningGet('preferences'); } catch (_) { state = null; }
    // The equalizer belongs to the player, not this module; hand it the saved bands.
    if (state && typeof window.eqLoad === 'function') window.eqLoad(state.eq);
    // AutoDJ follows the member between devices the same way the equalizer does.
    if (state && typeof window.autoDjLoad === 'function') window.autoDjLoad(state.playback);
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
    const pin = control('pin','album',album.key);
    if (!pin) return html;
    // The pin belongs on the card's control line. Appending it before the card's
    // closing div gave it a row of its own, under the votes, for no reason.
    // The meta line ("12 songs · 41:20") has the room; the chip row does not, and a
    // wrapped pin reads as its own row again.
    // Cards carry a .rowactions slot exactly like a song card; list rows fall back to the
    // meta line. The pin is wrapped so it gets the same floating pill as a song's like/pin.
    const pair = `<span class="pref-pair">${pin}</span>`;
    const tools = html.indexOf('<div class="album-tools">');
    if (tools !== -1) {
      const at = tools + '<div class="album-tools">'.length;
      return html.slice(0, at) + pair + html.slice(at);
    }
    const slot = html.lastIndexOf('<div class="rowactions">');
    if (slot !== -1) {
      const at = html.indexOf('</div>', slot);
      return html.slice(0, at) + pair + html.slice(at);
    }
    const cardEnd = html.lastIndexOf('</div>');
    const meta = html.lastIndexOf('<div class="ameta"');
    const metaEnd = meta === -1 ? -1 : html.indexOf('</div>', meta);
    if (metaEnd === -1) return html.slice(0,cardEnd) + pin + html.slice(cardEnd);
    return html.slice(0,metaEnd) + pin + html.slice(metaEnd);
  };
  const originalSortAlbums = sortAlbums;
  sortAlbums = albums => originalSortAlbums(albums).sort((a,b)=>rank('album',a.key,b.key));

  // Favorites are a playlist, not a panel under Pinned: the liked songs of the signed-in
  // member, and the same view across every member. Both are computed from
  // listening_favorites on each read — nothing is ever copied into a playlist row.
  const favTracks = () => state ? libAll.filter(t => state.favorites.includes(t.id)) : [];
  const publicEntries = () => {
    const map = state?.public_favorites || {};
    return Object.entries(map)
      .map(([id, likers]) => ({track: libAll.find(t => String(t.id) === id), likers}))
      .filter(e => e.track && e.likers.length)
      .sort((a, b) => b.likers.length - a.likers.length || String(a.track.title).localeCompare(String(b.track.title)));
  };
  const LIKER_COLORS = ['#58d3a8','#7aa2f7','#e0af68','#bb9af7','#f7768e','#7dcfff'];
  const likerColor = id => LIKER_COLORS[[...String(id)].reduce((a,c)=>((a*31+c.charCodeAt(0))>>>0), 7) % LIKER_COLORS.length];
  const likerChip = m => `<button type="button" class="liker" style="--c:${likerColor(m.id)}" data-person-id="${esc(m.id)}"
    title="${esc(m.name)} liked this" aria-label="${esc(m.name)} liked this">${profileSymbol(m.icon)}</button>`;
  // Four covers make the classic 2x2; one would look like a broken tile.
  const VIRTUAL = { favorites: 'Favorites', 'public-favorites': "Everyone's Favorites" };
  // A yearly top 100 is virtual too, but its name carries the year: `top-100:2026`.
  const YEAR_ID = id => /^top-100:(\d{4})$/.exec(String(id == null ? '' : id));
  const virtualName = id => VIRTUAL[id] || (YEAR_ID(id) ? YEAR_ID(id)[1] + ' Top 100' : null);
  const dynSaved = list => !!list.length && list.every(t => (listeningSaves?.tracks || []).includes(t.id));
  // Adding a computed playlist means saving its songs, one by one, through the same
  // endpoint a song's plus uses. Every song in the library is the finished state.
  function dynSaveControl(id, list){
    if (!listeningSaves || !list.length) return '';
    const saved = dynSaved(list);
    const label = saved ? 'Every song is in your library' : 'Add every song to your library';
    return `<button type="button" class="ghost lsave${saved ? ' on' : ''}" data-dyn-save="${id}" aria-pressed="${saved}"
      aria-label="${label}" title="${label}" ${saved || busy.has('save' + id) ? 'disabled' : ''}>${saved ? SVG.check(14) : SVG.plus(14)}</button>`;
  }
  function dynCard({open, title, list, by, extra}){
    const id = open === 'public' ? 'public-favorites' : 'favorites';
    return `<div class="plcard dyn ${extra || ''}" data-open-${open} tabindex="0" role="button" aria-label="${esc(title)}">
      ${playlistCollage(list.map(t => t.artwork).filter(Boolean), title)}
      <div class="pbody">
        <div class="pt"><span class="dynheart" aria-hidden="true">♥</span>${esc(title)}</div>
        <div class="ps"><span class="plcount">${list.length} song${list.length === 1 ? '' : 's'}</span><span class="plby">${esc(by)}</span></div>
        <div class="pacts">${downloadControl('/api/library/download?ids=' + list.map(t => t.id).join(','), list.length > 0, 'ZIP')}${dynSaveControl(id, list)}${control('pin', 'playlist', id)}</div>
      </div>
    </div>`;
  }
  function dynCards(){
    if (!state) return '';
    const mine = favTracks(), pub = publicEntries();
    const people = new Set(pub.flatMap(e => e.likers.map(m => m.id)));
    const me = (typeof last !== 'undefined' && last?.viewer?.display_name) || 'You';
    const cards = [];
    // Favorites is yours. Everyone's Favorites is the Repository's own list — it is not
    // yours to add or remove, so it belongs beside the shared playlists, not in My Library.
    if (libraryScope === 'mine'){
      cards.push(dynCard({open:'favorites', title:'Favorites', list:mine, by:me}));
      // One card per year with plays. The songs are computed on read (a 24-hour cache),
      // so the card can never hold a list that has drifted from the plays underneath it.
      for (const card of (state.years || [])) cards.push(yearCard(card));
    }
    else cards.push(dynCard({open:'public', title:"Everyone's Favorites", list:pub.map(e => e.track),
      by: `${people.size} member${people.size === 1 ? '' : 's'}`, extra:'public'}));
    return cards.join('');
  }
  // "Your 2026 Top 100": the year's most played songs, playable, pinnable and saveable
  // like any playlist. It has no row in `playlists`; the list is derived, never copied.
  function yearCard(card){
    const id = 'top-100:' + card.year;
    const title = card.year + ' Top 100';
    const songs = Number(card.tracks) || 0;
    return `<div class="plcard dyn" data-open-year="${card.year}" tabindex="0" role="button" aria-label="${esc(title)}">
      ${playlistCollage(card.artwork || [], title)}
      <div class="pbody">
        <div class="pt">${esc(title)}</div>
        <div class="ps"><span class="plcount">${songs} song${songs === 1 ? '' : 's'}</span><span class="plby">${Number(card.plays) || 0} plays</span></div>
        <div class="pacts">${control('pin', 'playlist', id)}</div>
      </div>
    </div>`;
  }
  function publicRow({track, likers}){
    const on = playingId === track.id;
    const names = likers.map(m => m.name).join(', ');
    return `<div class="favrow ${on ? 'playing' : ''}" data-fav-id="${track.id}" tabindex="0" role="button"
      aria-label="${esc(track.title)} — liked by ${esc(names)}">
      <div class="favart">${track.artwork ? `<img src="${esc(track.artwork)}" alt="" loading="lazy">` : ''}</div>
      <div class="favtext"><b>${esc(track.title)}</b><span>${esc(track.artist_name || '—')}</span></div>
      <div class="favlikers" title="${esc(names)}">${likers.slice(0, 4).map(likerChip).join('')}${likers.length > 4 ? `<span class="liker more">+${likers.length - 4}</span>` : ''}</div>
      <button type="button" class="favplay" title="${on && !playerAudio.paused ? 'Pause' : 'Play'}" aria-label="${on && !playerAudio.paused ? 'Pause' : 'Play'}"
        onclick="event.stopPropagation();toggleSongPlayback(${track.id})">${on && !playerAudio.paused ? SVG.pause(14) : SVG.play(14)}</button>
    </div>`;
  }
  // Pinned reads like the cover wall — square art, kind label, name — six at a time in a
  // 3 × 2 carousel that scrolls sideways for the rest (18 pins is three pages).
  function pinnedPanel(){
    if (!state || libraryScope !== 'mine' || detail) return '';
    const albums = albumsOf(libAll);
    // A computed playlist has no row in `playlists`; its pin resolves to the same list
    // the card shows, so the strip can name it and open it.
    const virtualPin = target => {
      const name = virtualName(target);
      if (!name) return null;
      const list = target === 'favorites' ? favTracks()
        : target === 'public-favorites' ? publicEntries().map(e => e.track) : [];
      const card = (state.years || []).find(c => 'top-100:' + c.year === target);
      return {title: name, artwork: list.map(t => t.artwork).find(Boolean) || (card?.artwork || [])[0] || ''};
    };
    const tiles = state.pins.slice(0, PIN_LIMIT).map(p => {
      const item = p.kind === 'track' ? libAll.find(t=>String(t.id)===p.target)
        : p.kind === 'playlist' ? (libPlaylists.find(t=>String(t.id)===p.target) || virtualPin(p.target))
        : albums.find(a=>a.key===p.target);
      if (!item) return '';
      const art = p.kind === 'album' ? item.art : item.artwork;
      const kind = p.kind === 'track' ? 'Song' : p.kind === 'album' ? 'Album' : 'Playlist';
      return `<div class="pinned-tile" data-pin-kind="${p.kind}" data-pin-target="${esc(p.target)}">
        <button type="button" class="pintile" data-open-pin-kind="${p.kind}" data-open-pin-id="${esc(p.target)}" title="${esc(item.title)}">
          <span class="pinart">${art ? `<img src="${esc(art)}" alt="" loading="lazy">` : esc(initials(item.title))}</span>
          <span class="pinmeta"><small>${kind}</small><b>${esc(item.title)}</b></span>
        </button>
        <button type="button" class="preference pin-holder" data-pref-action="pin" data-pref-kind="${p.kind}" data-pref-id="${esc(String(p.target))}" aria-pressed="true" hidden></button>
      </div>`;
    }).join('');
    const collapsed = pinsCollapsed();
    // A centred tab sits above the strip and is the only thing left when it is collapsed.
    return `<section class="mixed-pinned${collapsed ? ' collapsed' : ''}">
      <div class="pinbody">
        <h3>Pinned <small>${state.pins.length}/${PIN_LIMIT}</small><span class="pinpageinfo" id="pinpageinfo"></span></h3>
        <div class="pinned-carousel" id="pinnedcarousel">${tiles || '<p>Pin songs, albums and playlists here — up to 18. Pins save their current songs; unpinning keeps your saves.</p>'}</div>
      </div></section>`;
  }
  // The pin button in the library header row is the only control: lit when the strip is
  // open, dim when it is hidden. It is outside the panel, so it survives every render.
  function syncPinnedState(){ syncPinToggle(pinsCollapsed()); }
  // Hidden or shown is a member setting, not a device one: it is stored with the rest of
  // the view state and remembered on any device they sign in from.
  let pinHidden = null;
  function pinsCollapsed(){
    if (pinHidden === null){
      try { pinHidden = localStorage.getItem('hl.pinsCollapsed') === '1'; } catch (_) { pinHidden = false; }
    }
    return pinHidden;
  }
  function storePins(collapsed){
    pinHidden = collapsed;
    try { localStorage.setItem('hl.pinsCollapsed', collapsed ? '1' : '0'); } catch (_) {}
    try { api('/api/listening/ui', {pins: collapsed ? 1 : 0}).catch(() => {}); } catch (_) {}
  }
  function applyServerPins(state){
    if (!state || !('pins' in state)) return false;
    const collapsed = !!state.pins;
    const changed = collapsed !== pinsCollapsed();
    pinHidden = collapsed;
    try { localStorage.setItem('hl.pinsCollapsed', collapsed ? '1' : '0'); } catch (_) {}
    const panel = document.querySelector('.mixed-pinned');
    if (panel) panel.classList.toggle('collapsed', collapsed);
    if (typeof syncPinToggle === 'function') syncPinToggle(collapsed);
    return changed;
  }
  function syncPinToggle(collapsed){
    const button = document.getElementById('pintogglebtn');
    if (!button) return;
    button.setAttribute('aria-pressed', collapsed ? 'false' : 'true');
    button.classList.toggle('on', !collapsed);
    button.title = collapsed ? 'Show pinned' : 'Hide pinned';
    button.setAttribute('aria-label', button.title);
  }

  // Collapsing keeps the strip out of the way and remembers that choice across visits.
  function togglePinned(){
    const panel = document.querySelector('.mixed-pinned');
    const collapsed = !panel.classList.contains('collapsed');
    panel.classList.toggle('collapsed', collapsed);
    storePins(collapsed);
    syncPinToggle(collapsed);
    if (!collapsed && typeof paintPinPage === 'function') paintPinPage();
  }
  const originalRender = renderLibrary;
  renderLibrary = function(){
    originalRender();
    const host = document.querySelector('#liblist');
    if (!host) return;
    // A render must not pull the strip out from under a drag in progress.
    if (typeof pinDrag !== 'undefined' && pinDrag && pinDrag.active) return;
    document.querySelectorAll('.mixed-pinned').forEach(n=>n.remove());
    if (detail) return;
    // Pinned belongs at the top of the view, above the Albums/Songs tabs, not inside
    // the list where it pushed the actual content down.
    const bar = document.querySelector('#view-library .libbar');
    const panel = libraryScope === 'mine' ? pinnedPanel() : '';
    if (bar && panel) bar.insertAdjacentHTML('beforebegin', panel);
    if (typeof paintPinPage === "function") paintPinPage();
    syncPinToggle(localStorage.getItem('hl.pinsCollapsed') === '1');
    // The two dynamic playlists live in the playlists grid, not in a panel of their own.
    // These cards are rebuilt on every render: without clearing the old ones first they
    // stacked up ("Everyone's Favorites" five times, then six).
    host.querySelectorAll('.plcard.dyn').forEach(node => node.remove());
    if (state && libView === 'playlists'){
      const cards = dynCards();
      let grid = host.querySelector('.plgrid');
      if (!grid){ host.insertAdjacentHTML('afterbegin', '<div class="plgrid"></div>'); grid = host.querySelector('.plgrid'); }
      grid.insertAdjacentHTML('afterbegin', cards);
      // A grid that only ever held dynamic cards must not stay behind empty.
      if (!grid.children.length) grid.remove();
    }
  };
  const originalDetail = renderDetail;
  renderDetail = function(rows){
    if (detail?.kind === 'favorites') {
      const mine = favTracks();
      return `<button type="button" data-close-favorites>← Playlists</button>
        <h2>Favorites</h2>
        <p class="dynsub">${mine.length} song${mine.length === 1 ? '' : 's'} · liked by you · list updates as you like songs</p>` + renderSongs(mine, '');
    }
    if (detail?.kind === 'public-favorites') {
      const pub = publicEntries();
      const people = new Set(pub.flatMap(e => e.likers.map(m => m.id)));
      return `<button type="button" data-close-favorites>← Playlists</button>
        <h2>Everyone's Favorites</h2>
        <p class="dynsub">${pub.length} song${pub.length === 1 ? '' : 's'} liked by ${people.size} member${people.size === 1 ? '' : 's'} · most liked first</p>
        <div class="favlist">${pub.map(publicRow).join('') || '<div class="empty">Nobody has liked a song yet.</div>'}</div>`;
    }
    if (detail?.kind === 'year') {
      const year = detail.year;
      if (detail.rows === undefined) { loadYear(year); return `<p class="dynsub">Loading ${year}…</p>`; }
      const rows = detail.rows;
      return `<button type="button" data-close-favorites>← Playlists</button>
        <h2>${year} Top 100</h2>
        <p class="dynsub">${rows.length} song${rows.length === 1 ? '' : 's'} · most played first · recomputed daily</p>`
        + (rows.length ? renderSongs(rows, '')
           : '<div class="empty">No plays recorded in this year yet.</div>');
    }
    return originalDetail(rows);
  };
  // The list is computed on the server from the dated play log, capped at 100 and cached
  // for a day; the client only needs the ids it already has rows for.
  async function loadYear(year){
    try {
      const response = await fetch('/api/stats?year=' + encodeURIComponent(year), {cache: 'no-store'});
      const data = await response.json();
      if (!response.ok) throw new Error(data.error || ('HTTP ' + response.status));
      const rows = (data.top_tracks || []).map(row => libAll.find(t => t.id === row.id)).filter(Boolean);
      if (!detail || detail.kind !== 'year' || detail.year !== year) return;
      detail.rows = rows;
      renderLibrary();
    } catch (error) { showToast(error.message); }
  }
  // A liked-by row is a play target; keep the keyboard equivalent of its tabindex.
  document.addEventListener('keydown', event => {
    if (event.key !== 'Enter' && event.key !== ' ') return;
    const row = event.target.closest?.('[data-fav-id]');
    if (!row) return;
    event.preventDefault(); toggleSongPlayback(Number(row.dataset.favId));
  });
  // ---- pinned: drag to reorder, hold near an edge to page over --------------
  const PIN_PAGE = 6;
  let pinDrag = null, pinClickMuted = false;
  const pinCarousel = () => document.getElementById('pinnedcarousel');
  // A pin is already pinned, so it needs no button: long press (or right click) a tile for
  // the menu, and Unpin reuses the same hidden preference button as everything else.
  let pinMenu = null, pinHold = 0;
  const closePinMenu = () => { pinMenu?.remove(); pinMenu = null; };
  const openPinMenu = (tile, x, y) => {
    closePinMenu();
    const kind = tile.dataset.pinKind, target = tile.dataset.pinTarget;
    const id = kind === "album" ? target : Number(target);
    const rect = tile.getBoundingClientRect();
    pinMenu = window.openItemMenu(kind, id, x ?? rect.left, y ?? rect.bottom + 6, {
      unpin: () => tile.querySelector(".pin-holder")?.click()
    });
  };
  document.addEventListener('pointerdown', event => {
    const tile = event.target.closest?.('#pinnedcarousel .pinned-tile');
    if (!tile || event.button > 0) return;
    clearTimeout(pinHold);
    pinHold = setTimeout(() => openPinMenu(tile), 600);
  }, true);
  document.addEventListener('pointerup', () => clearTimeout(pinHold), true);
  document.addEventListener('pointermove', event => {
    if (Math.abs(event.movementX) + Math.abs(event.movementY) > 4) clearTimeout(pinHold);
  }, true);
  document.addEventListener('contextmenu', event => {
    const tile = event.target.closest?.('#pinnedcarousel .pinned-tile');
    if (!tile) return;
    event.preventDefault();
    openPinMenu(tile);
  });
  document.addEventListener('pointerdown', event => {
    if (pinMenu && !event.target.closest('.pinmenu')) closePinMenu();
  }, true);

  const pinTiles = car => [...car.querySelectorAll('.pinned-tile')];
  const paintPinPage = () => {
    const car = pinCarousel(), chip = document.getElementById('pinpageinfo');
    if (!car || !chip) return;
    const count = car.querySelectorAll('.pinned-tile').length;
    const pages = Math.max(1, Math.ceil(count / PIN_PAGE));
    const page = Math.min(pages, Math.round(car.scrollLeft / Math.max(1, car.clientWidth)) + 1);
    chip.textContent = pages > 1 ? `page ${page}/${pages}` : '';
  };
  document.addEventListener('scroll', paintPinPage, true);
  setTimeout(paintPinPage, 0);

  const savePinOrder = async car => {
    const order = pinTiles(car).map(tile => ({kind: tile.dataset.pinKind, target: tile.dataset.pinTarget}));
    if (!order.length) return;                       // the panel was rebuilt mid-drag
    try {
      state = await api('/api/listening/pin-order', {order});
    } catch (e) {
      showToast(e.message || 'Could not save that order');
    }
    renderLibrary();
  };

  document.addEventListener('pointerdown', event => {
    const tile = event.target.closest?.('#pinnedcarousel .pinned-tile');
    if (!tile || event.target.closest('.preference') || event.button > 0) return;
    pinClickMuted = false;
    pinDrag = {tile, id: event.pointerId, x: event.clientX, y: event.clientY, active: false, edge: 0, edgeAt: 0};
  }, true);

  document.addEventListener('pointermove', event => {
    if (!pinDrag || event.pointerId !== pinDrag.id) return;
    if (!pinDrag.active && Math.hypot(event.clientX - pinDrag.x, event.clientY - pinDrag.y) < 8) return;
    const car = pinCarousel();
    if (!car) return;
    if (!pinDrag.active){ pinDrag.active = true; pinDrag.tile.classList.add('pin-dragging'); }
    event.preventDefault();
    const under = document.elementFromPoint(event.clientX, event.clientY)?.closest('.pinned-tile');
    if (under && under !== pinDrag.tile && under.parentElement === car){
      if (pinTiles(car).indexOf(pinDrag.tile) < pinTiles(car).indexOf(under)) under.after(pinDrag.tile);
      else under.before(pinDrag.tile);
    }
    // Hold near an edge and the strip pages over, so a pin can be dropped there. The
    // pointer stops moving while it waits, so this is a timer, not a per-move check.
    const box = car.getBoundingClientRect();
    const edge = event.clientX < box.left + 64 ? -1 : event.clientX > box.right - 64 ? 1 : 0;
    if (edge !== pinDrag.edge){
      pinDrag.edge = edge;
      clearTimeout(pinDrag.timer);
      pinDrag.timer = 0;
      if (edge) pinDrag.timer = setTimeout(() => {
        if (!pinDrag || pinDrag.edge !== edge) return;
        car.scrollBy({left: edge * car.clientWidth, behavior: "smooth"});
        pinDrag.edge = 0;
        setTimeout(paintPinPage, 400);
      }, 420);
    }
  }, true);

  const endPinDrag = event => {
    if (!pinDrag || (event && event.pointerId !== pinDrag.id)) return;
    const drag = pinDrag; pinDrag = null;
    clearTimeout(drag.timer);
    if (!drag.active) return;
    drag.tile.classList.remove('pin-dragging');
    pinClickMuted = true;
    setTimeout(() => { pinClickMuted = false; }, 80);
    const car = pinCarousel();
    if (car) savePinOrder(car);
  };
  document.addEventListener('pointerup', endPinDrag, true);
  document.addEventListener('pointercancel', endPinDrag, true);

  // Capture before row/card inline handlers: personal actions never start playback.
  document.addEventListener('click', async event => {
    const button = event.target.closest('[data-pref-action],[data-open-favorites],[data-open-public],[data-open-year],[data-close-favorites],[data-open-pin-kind],[data-dyn-save],[data-fav-id]');
    if (!button) return;
    event.preventDefault(); event.stopPropagation(); event.stopImmediatePropagation();
    if (button.hasAttribute('data-open-favorites')) { detail={kind:'favorites'}; renderLibrary(); return; }
    if (button.hasAttribute('data-open-year')) {
      detail={kind:'year', year:Number(button.dataset.openYear)}; renderLibrary(); return;
    }
    if (button.hasAttribute('data-open-public')) { detail={kind:'public-favorites'}; renderLibrary(); return; }
    if (button.hasAttribute('data-close-favorites')) { detail=null; renderLibrary(); return; }
    if (button.dataset.favId) { toggleSongPlayback(Number(button.dataset.favId)); return; }
    if (button.dataset.dynSave){
      const target = button.dataset.dynSave, key = 'save' + target;
      if (busy.has(key)) return;
      busy.add(key); button.disabled = true;
      try { await api('/api/listening/saves', {kind:'playlist', id:target, saved:true}); await loadListeningSaves(); }
      catch (e) { showToast(e.message || 'Could not add those songs'); }
      finally { busy.delete(key); renderLibrary(); }
      return;
    }
    if (pinClickMuted) return;
    if (button.dataset.openPinKind){
      const kind=button.dataset.openPinKind, id=button.dataset.openPinId;
      if (kind==='playlist' && YEAR_ID(id)) { detail={kind:'year', year:Number(YEAR_ID(id)[1])}; renderLibrary(); return; }
      if (kind==='playlist' && VIRTUAL[id]) { detail={kind: id==='favorites' ? 'favorites' : 'public-favorites'}; renderLibrary(); return; }
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
