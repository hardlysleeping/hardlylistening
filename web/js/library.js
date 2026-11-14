/* The library's drawing layer: grouping, the renderers (list, cards, grid, album and artist
   walls), and the detail-view editors behind them.

   `JS-032`. A plain classic script, loaded before the inline one: no build step, all names
   stay global. Nothing here paints at load — `renderLibrary()` is called by the poll and by
   every action — and nothing looks up markup or reads a `const` from the inline script,
   which does not exist yet. The library's state (`lib`, `libQueue`, the view/layout/sort
   choices) still lives in index.html; this file reads it at call time.
*/
// ------------------------------------------------------------------ grouping
// The cover to draw for a track: our own digest route when the server knows one (the same
// picture at every width, independent of the provider's CDN, and something the service worker
// can keep for offline play), otherwise the provider URL exactly as before.
function trackArt(track){
  if (!track) return "";
  return track.art_hash ? `/api/art/${track.art_hash}` : (track.artwork || "");
}
function albumsOf(rows){
  const map = new Map();
  for (const t of rows){
    // A song with no release still needs a page to open from its cover and its row; it just
    // is not an album, so the Album tab filters these out (renderAlbums). One group per
    // track: two nameless songs by one artist are not "an album" together.
    const named = (t.album || "").trim();
    const key = named
      ? (t.album_id || `${t.artist_name || "Unknown artist"}\u0000${t.album}`)
      : `single\u0000${t.id}`;
    let album = map.get(key);
    if (!album){
      album = {key, title: named || t.title || "Single", artist: t.artist_name || "Unknown artist",
               tracks: [], art: "", added: 0, duration: 0,
               albumId: t.album_id ? Number(t.album_id) : 0, single: !named};
      map.set(key, album);
    }
    album.tracks.push(t);
    if (!album.art && trackArt(t)) album.art = trackArt(t);
    album.duration += t.duration || 0;
    album.added = Math.max(album.added, t.added || 0);
  }
  for (const album of map.values())
    album.tracks.sort((a, b) => (a.track_number || 0) - (b.track_number || 0) || String(a.title).localeCompare(b.title));
  return [...map.values()];
}

// Every track names its artist, album or not — so the artist list is built from
// the tracks and only borrows albums when the artist actually has one.
function artistsOf(rows){
  const map = new Map();
  for (const t of rows){
    const name = t.artist_name || "Unknown artist";
    let artist = map.get(name);
    if (!artist){ artist = {name, albums: [], tracks: [], art: "", added: 0}; map.set(name, artist); }
    artist.tracks.push(t);
    if (!artist.art) artist.art = trackArt(t);
    artist.added = Math.max(artist.added, t.added || 0);
  }
  for (const album of albumsOf(rows)){
    const artist = map.get(album.artist);
    // Singles stay out of the artist's album count: they are songs, not releases.
    if (artist && !album.single){ artist.albums.push(album); artist.art = artist.art || album.art; }
  }
  for (const artist of map.values()){
    const key = String(artist.name || "").trim().toLowerCase().replace(/\s+/g, " ");
    artist.art = artistArt[key] || artist.art;
  }
  return [...map.values()];
}

function sortAlbums(albums){
  const list = albums.slice();
  if (libSort === "title") list.sort((a, b) => a.title.localeCompare(b.title));
  else if (libSort === "artist") list.sort((a, b) => a.artist.localeCompare(b.artist) || a.title.localeCompare(b.title));
  else if (libSort === "oldest") list.sort((a, b) => a.added - b.added || a.title.localeCompare(b.title));
  else if (libSort === "duration") list.sort((a, b) => (a.duration || 0) - (b.duration || 0) || a.title.localeCompare(b.title));
  else list.sort((a, b) => b.added - a.added || a.artist.localeCompare(b.artist));
  return list;
}

// ------------------------------------------------------------------ rendering
// The app header is sticky and its height changes with the viewport (the bar
// wraps on a phone), so the sticky column header reads its offset from here.
function syncHeaderOffset(){
  const hd = document.querySelector('header');
  if(!hd) return;
  document.documentElement.style.setProperty('--hdr', Math.round(hd.getBoundingClientRect().height) + 'px');
}
// Guarded: the DOM test suites evaluate this script without browser globals.
if (typeof addEventListener === 'function') {
  addEventListener('resize', syncHeaderOffset, {passive:true});
  addEventListener('load', syncHeaderOffset);
  if (window.ResizeObserver) {
    const hd = document.querySelector('header');
    if (hd) new ResizeObserver(syncHeaderOffset).observe(hd);
  }
  syncHeaderOffset();
}

function renderLibrary(){
  if (libraryScope !== 'mine' && libView === 'folders') { libView='albums'; localStorage.setItem('hl.libraryView',libView); }
  if (typeof syncPlaybackQueue === 'function') syncPlaybackQueue();
  // Polling must not erase an open discussion or an unfinished comment.
  const host = $("#liblist");
  const oldSocial = new Map([...host.querySelectorAll('.social')].map(n => [n.dataset.social, n]));
  const socialFocus = typeof document !== 'undefined' && document.activeElement?.closest?.('.social') ? document.activeElement : null;
  const restoreSocial = () => {
    for (const node of host.querySelectorAll('.social')){
      const previous = oldSocial.get(node.dataset.social);
      if (previous) node.replaceWith(previous);
    }
    if (socialFocus?.isConnected) socialFocus.focus();
  };

  const q = ($("#libsearch").value || "").trim().toLowerCase();
  const setsOf = t => (t.playlists || []).map(p => p.title).join(" ");
  const selected = $("#libplaylist").value;
  const playlists = scopedPlaylists();
  paintRepoTab();
  // Titles are HTML-escaped; option values are database integer IDs.
  $('#libplaylist').innerHTML = '<option value="">All playlists</option>' + playlists.map(p => `<option value="${p.id}">${esc(p.title)}</option>`).join('');
  $('#libplaylist').value = playlists.some(p => String(p.id) === selected) ? selected : '';
  for (const button of $('#libscopes').querySelectorAll('button')) button.setAttribute('aria-pressed', String(button.dataset.scope === libraryScope));
  for (const button of $('#libviews').querySelectorAll('button')) {
    button.classList.toggle('on', button.dataset.view === libView);
    if (button.dataset.view === 'folders') button.hidden = libraryScope !== 'mine';
  }
  $('#libsort').style.display = ['playlists','folders'].includes(libView) ? 'none' : '';
  $('#libplaylist').style.display = libView === 'folders' ? 'none' : '';
  // Editing playlists belongs beside the playlist selector, in the Playlists view only.
  $('#playlist-edit-btn').hidden = libView !== 'playlists';
  // Search says what it searches, and only searches that view's names.
  $('#libsearch').placeholder = ({albums: 'Search albums', artists: 'Search artists',
    songs: 'Search songs', playlists: 'Search playlists', folders: 'Search folders'})[libView] || 'Search';
  paintLibraryToolbar();
  // Repository keeps one line of explanation; Library needs none.
  $('#libscopehelp').hidden = libraryScope !== 'shared';
  if (libraryScope === 'shared') $('#libscopehelp').textContent = 'Repository is the shared community music pool.';
  // Importing a file copies it into the shared Repository, not into your Library: the
  // drop target only belongs on the Repository scope, where the file actually lands.
  // Browsing shows only what you can actually play. A queued row used to sit here
  // with a play button that could only fail; a track appears once it is on disk, and
  // what is still coming is said once above the list instead of as dead rows.
  const subject = libView !== 'folders' && $('#libplaylist').value
    ? playlistTracks(Number($('#libplaylist').value))
    : scopedTracks();
  const source = subject.filter(t => t.playable);
  const pendingCount = subject.length - source.length;
  const haystack = t => libView === 'albums' ? `${t.album} ${t.artist_name}`
    : libView === 'artists' ? `${t.artist_name}`
    : libView === 'playlists' ? `${t.title} ${t.artist_name} ${t.album} ${setsOf(t)}`
    : `${t.title} ${t.artist_name} ${t.album}`;
  const rows = source.filter(t => (typeof Genres === 'undefined' || Genres.matches(t)) &&
    (!q || haystack(t).toLowerCase().includes(q)));
  currentRows = rows;
  if (!playerAudio.src && !playbackQueueEdited) libQueue = rows.filter(t => t.playable);
  if (typeof paintPlaybackQueue === 'function') paintPlaybackQueue();

  const total = scopedTracks();
  // One number beside the title: how many songs this scope holds.
  $("#libcount").textContent = libraryScope === 'mine'
    ? `${total.filter(t => t.playable).length} songs`
    : `${lib.length} songs`;
  $("#libcount").hidden = activeRepoTab() !== 'browse';

  if (detail && detail.kind !== "playlist" && !rows.length && q) detail = null;

  let html = "";
  if (detail) html = renderDetail(rows);
  else if (libView === "albums") html = renderAlbums(rows, q);
  else if (libView === "artists") html = renderArtists(rows, q);
  else if (libView === "songs") html = renderSongs(rows, q);
  else if (libView === "folders") html = renderFolders(rows, q);
  else html = renderPlaylistsView(rows, q);

  // One quiet line instead of a wall of rows that cannot play yet.
  if (pendingCount && !detail){
    html = `<div class="libnote">${pendingCount} track${pendingCount === 1 ? "" : "s"} still downloading — they appear here the moment they are ready.</div>` + html;
  }

  if (html !== lastLibraryHtml){
    host.innerHTML = html;
    lastLibraryHtml = html;
  }
  $("#view-library").classList.toggle("detail-open", !!detail);
  restoreSocial();
  paintBubbles();
  // The cover wall was just rebuilt, so the honeycomb's row marks are new too.
  if (typeof paintHexRows === "function") paintHexRows();
  // Offline copies are marked per row, and the mark has to survive every repaint.
  if (typeof paintOfflineMarks === "function") paintOfflineMarks();
}

function emptyState(rows, q){
  if (!rows.length){
    if (libraryScope === 'mine') return `<div class="empty">${!listeningSaves
      ? 'Personal Library unavailable. Sign in as an approved member and try again.'
      : q ? 'Nothing matches that search in your Library.'
      : 'No saved songs here yet. Open Repository and choose Add to library on a song or playlist. Saved playlists also appear under Playlists.'}</div>`;
    return `<div class="empty">${lib.length
      ? (q ? "Nothing matches that search." : "This playlist has no downloaded tracks yet.")
      : "Your library is empty — add a playlist on the Queue tab."}</div>`;
  }
  return "";
}

function renderAlbums(rows, q){
  // A one-song "album" adds nothing to the album list — the song is still in Songs — and a
  // nameless single is not an album at all. Both keep their own page (openAlbumForTrack).
  const albums = albumsOf(rows).filter(a => !a.single && a.tracks.length > 1);
  if (!albums.length) return emptyState(rows, q);
  renderedAlbums = sortAlbums(albums);
  // Albums read as a grid or as rows, the same switch songs use.
  // Albums have two shapes: rows, or the cover grid. Cards and Grid are the same wall here.
  // Exactly the song renderers, with album payloads: same table head, same rows, same
  // cover tiles, same card. One layout switch, one set of styles.
  if (libSongLayout === 'list') return `<div class="songtable">${songHead()}${renderedAlbums.map((a, i) => albumRow(a, i)).join("")}</div>`;
  if (libSongLayout === 'grid') return albumCoverWall(renderedAlbums);
  return `<div class="songgrid">${renderedAlbums.map((a, i) => albumCard(a, i)).join("")}</div>`;
}

// The line under an album's name: its artist, or — for a SoundCloud set — the name of
// the set those songs came from, because a set card is already headed by the artist.
function albumArtistLine(album){
  return album.artist || "—";
}

// An album as a song row: the song row's own markup and classes, filled with album values.
function albumRow(album, index){
  const count = album.tracks.length;
  const pending = album.tracks.filter(t => !t.playable).length;
  const on = album.tracks.some(t => t.id === playingId);
  const playable = album.tracks.filter(t => t.playable).length;
  const zip = "/api/library/download?ids=" + album.tracks.map(t => t.id).join(",");
  const removed = libraryScope === 'mine' && albumRemoved(album);
  return `<div class="songrow${on ? " playing" : ""}${removed ? " removed" : ""}" data-album="${esc(album.key)}" tabindex="0" aria-label="${esc(album.title)} — album actions available with Shift+F10" onclick="openAlbumByKey('${esc(album.key)}')">
    <div class="idx${on ? " playing" : ""}">${on ? SVG.bars(11) : index + 1}</div>
    <div class="thumb">${album.art ? artImg(album.art, "", true, album.title) : ""}</div>
    <div class="st"><span class="sttext"><b>${esc(album.title)}</b>${removed ? `<span class="removed-badge">Removed</span>` : ""}</span><div class="rowactions">${albumSaveControl(album)}</div></div>
    <div class="sartist">${esc(albumArtistLine(album))}</div>
    <div class="salbum">${count} song${count === 1 ? "" : "s"}${pending ? ` · ${pending} pending` : ""}</div>
    ${voteRow("album", album.key)}
    <div class="sdur">${album.duration ? fmtLong(album.duration) : count + " songs"}</div>
    <span class="send">
      <a class="sdl" href="${zip}" download title="Download ZIP" onclick="event.stopPropagation()">↓</a>
      <button type="button" class="splay${playable ? "" : " off"}" ${playable ? "" : "disabled"} title="Play album" aria-label="Play album" onclick="event.stopPropagation();playAlbumAt(${index})">${SVG.play(13)}</button>
    </span>
  </div>`;
}

// An album as a cover tile: the song cover wall's own tile, opening the album.
function albumCoverWall(albums){
  const tiles = albums.map((a, i) => {
    const on = a.tracks.some(t => t.id === playingId);
    const removed = libraryScope === 'mine' && albumRemoved(a);
    return `<button type="button" class="covercell${on ? " playing" : ""}${removed ? " removed" : ""}" data-album="${esc(a.key)}"
      title="${esc(a.title)}" aria-label="${esc(a.title)} — open album" onclick="openAlbumByKey('${esc(a.key)}')">
      ${artBox(a.art, a.title, "coverart")}
      ${statusMarks("album", a.key)}
      <span class="covername"><b>${esc(a.title)}</b></span>
    </button>`;
  }).join("");
  return `<div class="covergrid">${tiles}</div>`;
}

function openAlbumByKey(key){
  const list = renderedAlbums || [];
  const index = list.findIndex(a => a.key === key);
  if (index >= 0) openAlbumAt(index);
}

function albumCard(album, index){
  const count = album.tracks.length;
  const pending = album.tracks.filter(t => !t.playable).length;
  const goneCount = album.tracks.filter(t => goneLabel(t)).length;
  const on = album.tracks.some(t => t.id === playingId);
  const playable = album.tracks.filter(t => t.playable).length;
  const zip = "/api/library/download?ids=" + album.tracks.map(t => t.id).join(",");
  const removed = libraryScope === 'mine' && albumRemoved(album);
  // Cards: the same shape as a song card — cover with queue-next, duration, download and
  // play on the artwork, a save button under the cover, artist/album lines, vote row.
  return `<div class="songcard acard ${on ? "playing" : ""} ${goneCount ? "gone-any" : ""} ${removed ? "removed" : ""}" data-album="${esc(album.key)}" onclick="openAlbumAt(${index})">
    <div class="thumb">${artBox(album.art, album.title, "acover")}
      ${removed ? `<span class="removed-chip">Removed</span>` : ""}
      <div class="album-tools"></div>
      <button type="button" class="snext" title="Queue album next" aria-label="Queue album next"
        onclick="event.stopPropagation();queueAlbumNext(${index})">${SVG.toNext(16)}</button>
      ${on ? `<span class="nowbars" aria-hidden="true">${SVG.bars(14)}</span>` : ""}
      <div class="bubbles" data-bkey="${album.key}"></div>
    </div>
    <span class="sdur">${album.duration ? fmtLong(album.duration) : count + " songs"}</span>
    <span class="send">
      <a class="sdl" href="${zip}" download title="Download ZIP" onclick="event.stopPropagation()">↓</a>
      <button type="button" class="splay${playable ? "" : " off"}" ${playable ? "" : "disabled"}
        title="${playable ? "Play album" : "Nothing downloaded yet"}" aria-label="Play album"
        onclick="event.stopPropagation();playAlbumAt(${index})">${SVG.play(13)}</button>
    </span>
    <div class="st"><span class="sttext"><b>${esc(album.title)}</b></span>
      <div class="rowactions">${albumSaveControl(album)}</div></div>
    <div class="sartist">${esc(albumArtistLine(album))}</div>
    <div class="salbum">${count} song${count === 1 ? "" : "s"}${pending ? ` · ${pending} pending` : ""}</div>
    ${voteRow("album", album.key)}
  </div>`;
}

// An album is in the library when any of its songs is: clicking minus then removes what is
// there instead of topping up the gaps. A removal also stays on the page (see libraryPeek),
// so the same button turns into an undo until the person leaves this view.
function albumRemoved(album){
  const ids = album.tracks.filter(t => t.playable).map(t => t.id);
  return ids.length > 0 && ids.every(id => libraryPeek.has(id));
}
const albumConfirmed = new Set();
function albumSaveControl(album){
  if (!listeningSaves) return '';
  const ids = album.tracks.filter(t => t.playable).map(t => t.id);
  const savedIds = new Set(listeningSaves.tracks || []);
  const savedCount = ids.filter(id => savedIds.has(id)).length;
  if (albumRemoved(album)) return `<button class="ghost lsave" title="Add album back to library" aria-label="Add album back to library" aria-pressed="false"
    onclick="event.stopPropagation();toggleAlbumSave('${esc(album.key)}')">${SVG.plus(14)}</button>`;
  const saved = savedCount > 0;
  const confirming = saved && albumConfirmed.has(album.key);
  const label = confirming ? 'Album added to your library'
    : !saved ? 'Add album to library'
    : savedCount < ids.length ? `Remove album from library (${savedCount} of ${ids.length} songs are saved)`
    : 'Remove album from library';
  return `<button class="ghost lsave${saved && !confirming ? ' on' : ''}${confirming ? ' confirm' : ''}" title="${label}" aria-label="${label}" aria-pressed="${saved}"
    onclick="event.stopPropagation();toggleAlbumSave('${esc(album.key)}')">${confirming ? SVG.check(14) : saved ? SVG.minus(14) : SVG.plus(14)}</button>`;
}
async function toggleAlbumSave(key){
  // The album as it is on screen: in the Library that is the saved songs, so undoing a
  // remove puts back exactly those instead of adding the rest of the release as well.
  const album = (renderedAlbums || []).find(a => a.key === key)
             || albumsOf(scopedTracks()).find(a => a.key === key)
             || albumsOf(libAll).find(a => a.key === key);
  if (!album || !listeningSaves) return;
  const ids = album.tracks.filter(t => t.playable).map(t => t.id);
  if (!ids.length){ showToast('Nothing downloaded from this album yet'); return; }
  const savedIds = new Set(listeningSaves.tracks || []);
  const savedCount = ids.filter(id => savedIds.has(id)).length;
  const save = savedCount === 0;                  // nothing saved: this is an add (or an undo)
  try {
    await Promise.all(ids.filter(id => save ? !savedIds.has(id) : savedIds.has(id))
      .map(id => api('/api/listening/saves', {kind: 'track', id, saved: save})));
    await loadListeningSaves();
    for (const id of ids) save ? libraryPeek.delete(id) : libraryPeek.add(id);
    // An add shows the check for a beat before the minus takes over; a remove leaves the
    // card marked and dashed so it is visibly still there and one click from returning.
    if (save) albumConfirmed.add(key);
    renderLibrary();
    if (save) setTimeout(() => { albumConfirmed.delete(key); renderLibrary(); }, SAVE_CONFIRM_MS);
    showToast(save ? 'Album added to your library'
                   : 'Album removed from your library — the + puts it back');
  } catch (e) { showToast(e.message || 'Could not change that album'); }
}
function queueAlbumNext(index){
  const album = renderedAlbums[index];
  if (!album) return;
  for (const track of album.tracks.filter(t => t.playable)) queueSong(track.id, true);
}

function renderArtists(rows, q){
  if (!rows.length) return emptyState(rows, q);
  renderedArtists = artistsOf(rows);
  if (libSort === "title") renderedArtists.sort((a, b) => a.name.localeCompare(b.name));
  else if (libSort === "recent") renderedArtists.sort((a, b) => b.added - a.added);
  return `<div class="artistgrid">${renderedArtists.map((artist, i) => `
    <div class="arcard">
      <button type="button" class="artistbtn" onclick="openArtistAt(${i})"
        data-artist-index="${i}"
        oncontextmenu="event.preventDefault();artistPictureMenu(${i},event.clientX,event.clientY)"
        aria-label="Open ${esc(artist.name)}">
        <span class="round">${artist.art ? `<img src="${esc(artist.art)}" alt="">` : esc(initials(artist.name))}</span>
      </button>
      <div class="aname" title="${esc(artist.name)}">${esc(artist.name)}</div>
      <div class="asub">${artist.albums.length ? `${artist.albums.length} album${artist.albums.length === 1 ? "" : "s"} · ` : ""}${artist.tracks.length} song${artist.tracks.length === 1 ? "" : "s"}</div>
    </div>`).join("")}</div>`;
}

// Admin-only: replace an artist's picture by hand. Manual art wins over the
// automatic SoundCloud/Apple picture and is never overwritten by it.
function artistPictureMenu(index, x, y){
  if (!document.documentElement.classList.contains('is-admin')) return;
  const artist = renderedArtists[index];
  if (!artist) return;
  document.querySelectorAll('.artistmenu').forEach(node => node.remove());
  const box = document.createElement('div');
  box.className = 'songmenu artistmenu';
  box.setAttribute('role', 'menu');
  box.setAttribute('aria-label', 'Artist actions');
  for (const [label, action] of [
    ['Use SoundCloud picture', () => useArtistArt(artist.name, 'soundcloud')],
    ['Use Apple Music picture', () => useArtistArt(artist.name, 'apple')],
    ['Upload picture', () => pickArtistPicture(artist.name)],
  ]){
    const button = document.createElement('button');
    button.type = 'button';
    button.setAttribute('role', 'menuitem');
    button.textContent = label;
    button.onclick = () => { box.remove(); action(); };
    box.append(button);
  }
  document.body.append(box);
  const rect = box.getBoundingClientRect();
  box.style.left = Math.max(8, Math.min(x, innerWidth - rect.width - 8)) + 'px';
  box.style.top = Math.max(8, Math.min(y, innerHeight - rect.height - 8)) + 'px';
  setTimeout(() => {
    const away = event => { if (!box.contains(event.target)) { box.remove(); document.removeEventListener('pointerdown', away, true); } };
    document.addEventListener('pointerdown', away, true);
  }, 0);
}
function pickArtistPicture(name){
  const input = document.createElement('input');
  input.type = 'file';
  input.accept = 'image/*';
  input.onchange = async () => {
    const file = input.files && input.files[0];
    if (!file) return;
    if (file.size > 8_000_000){ showToast('Image too large (8 MB)'); return; }
    const data = await new Promise((resolve, reject) => {
      const reader = new FileReader();
      reader.onload = () => resolve(reader.result);
      reader.onerror = reject;
      reader.readAsDataURL(file);
    });
    try {
      const result = await api('/api/artist/art', {name, data});
      const key = String(name || "").trim().toLowerCase().replace(/\s+/g, " ");
      artistArt[key] = result.artwork;
      renderLibrary();
      showToast('Artist picture updated');
    } catch (error) {
      showToast(error.message || 'Could not set that picture');
    }
  };
  input.click();
}
async function useArtistArt(name, provider){
  try {
    const response = await fetch('/api/artist/art/candidates?name=' + encodeURIComponent(name), {cache: 'no-store'});
    const data = await response.json().catch(() => ({}));
    if (!response.ok) throw new Error(data.error || 'Could not fetch artist pictures');
    const url = data[provider];
    if (!url){
      showToast(provider === 'soundcloud' ? 'No SoundCloud picture found' : 'No Apple Music picture found');
      return;
    }
    await api('/api/artist/art', {name, url});
    const key = String(name || "").trim().toLowerCase().replace(/\s+/g, " ");
    artistArt[key] = url;
    renderLibrary();
    showToast('Artist picture updated');
  } catch (error) {
    showToast(error.message || 'Could not update that picture');
  }
}
// Long-press the avatar on a phone opens the same menu.
(() => {
  let timer = null;
  const clear = () => { if (timer){ clearTimeout(timer); timer = null; } };
  document.addEventListener('pointerdown', event => {
    const button = event.target.closest && event.target.closest('.artistbtn');
    if (!button || event.pointerType !== 'touch') return;
    clear();
    timer = setTimeout(() => {
      timer = null;
      artistPictureMenu(Number(button.dataset.artistIndex), event.clientX, event.clientY);
    }, 550);
  }, true);
  document.addEventListener('pointerup', clear, true);
  document.addEventListener('pointercancel', clear, true);
  document.addEventListener('pointermove', event => {
    if (timer && (Math.abs(event.movementX) + Math.abs(event.movementY)) > 10) clear();
  }, true);
})();

// One definition of the column labels, so the list view and the detail pages
// cannot drift apart.
function headCell(key, label, cls){
  const active = key === "votes" ? libSort.startsWith("votes") : libSort === key;
  const dir = active ? (libSortDir === "asc" ? "↑" : "↓") : "";
  const text = key === "votes" ? sortLabel(libSort.startsWith("votes") ? libSort : "votes_all") : label;
  return `<button type="button" class="hcell ${cls || ""} ${active ? "on" : ""}"
    data-sort="${key}" aria-sort="${active ? (libSortDir === "asc" ? "ascending" : "descending") : "none"}"
    title="${key === "votes" ? "Sort by votes — everyone, then just yours" : `Sort by ${label.toLowerCase()}`}"
    onclick="sortBy('${key}')">${text}${dir ? `<i class="harrow">${dir}</i>` : ""}</button>`;
}

// Rebuilt on every render: the active column and its arrow live in this markup.
function songHead(){
  return `<div class="songhead" role="row">
  <span>#</span><span></span>${headCell("title", "Title", "h-title")}${headCell("artist", "Artist")}
  ${headCell("album", "Album")}${headCell("votes", "Votes", "h-votes")}${headCell("duration", "Time", "h-time")}<span></span>
</div>`;
}

function songTable(tracks, emptyHtml, opts){
  return `<div class="songtable ${(opts && opts.classes) || ""}">${songHead()}${
    tracks.length ? tracks.map((t, i) => songRow(t, i, false, opts)).join("")
                  : `<div class="empty">${emptyHtml || "Nothing here yet."}</div>`}</div>`;
}

function sortKeys(key, t){
  switch (key){
    case "title":      return String(t.title || "").toLowerCase();
    case "artist":     return String(t.artist_name || "").toLowerCase();
    case "album":      return String(t.album || "").toLowerCase();
    case "duration":   return Number(t.duration || 0);
    case "bpm":        return Number(t.bpm || 0);
    case "votes_all":  { const v = summary("track", t.id); return {a: v.up, b: v.down}; }
    default:           return Number(t.added || 0);
  }
}

// Votes sort by the pair (up, down) so "3 up" beats "3 up/2 down" at equal score,
// and a vote of your own sorts by what you said, not by the crowd.
function compareTracks(key, a, b){
  const ka = sortKeys(key, a), kb = sortKeys(key, b);
  if (typeof ka === "string" || typeof ka === "number"){
    if (ka < kb) return -1;
    if (ka > kb) return 1;
    return String(a.title || "").localeCompare(String(b.title || ""));
  }
  if ((ka.a || 0) !== (kb.a || 0)) return (ka.a || 0) - (kb.a || 0);
  if ((ka.b || 0) !== (kb.b || 0)) return (kb.b || 0) - (ka.b || 0);   // fewer downs first
  return String(a.title || "").localeCompare(String(b.title || ""));
}

// Each view gets the sort keys that make sense for it, plus one reverse toggle.
// Only keys that mean something in that section: a song sorts by its own title, an album
// by the album title or its artist, never the other way round.
const SORT_BY_VIEW = {
  songs:     ['title', 'duration', 'bpm', 'votes_all', 'recent', 'oldest'],
  albums:    ['title', 'duration', 'recent', 'oldest'],
  artists:   ['artist', 'recent'],
  playlists: [],
  folders:   [],
};
function sortKeysForView(view){
  const keys = SORT_BY_VIEW[view] || SORT_BY_VIEW.songs;
  return keys.length ? keys : ['recent'];
}
function paintSortControl(){
  const select = document.getElementById('libsort');
  const reverse = document.getElementById('libsortdir');
  const keys = sortKeysForView(libView);
  if (select){
    if (!keys.includes(libSort)) libSort = keys[0];
    select.innerHTML = keys.map(key => {
      let label = SORT_KEYS[key] ? SORT_KEYS[key].label : key;
      if (libView === 'artists' && key === 'artist') label = 'Name';
      if (key === 'title') label = libView === 'albums' ? 'Album title' : 'Song title';
      return `<option value="${key}">${esc(label)}</option>`;
    }).join('');
    select.value = libSort;
    select.hidden = !SORT_BY_VIEW[libView] || !SORT_BY_VIEW[libView].length;
  }
  if (reverse){
    const hidden = !SORT_BY_VIEW[libView] || !SORT_BY_VIEW[libView].length;
    reverse.hidden = hidden;
    // The arrow is drawn, not rotated: up means A→Z, down means Z→A.
    const glyph = libSortDir === 'asc'
      ? '<path d="M12 20V5"/><path d="m6.5 11 5.5-6 5.5 6"/>'
      : '<path d="M12 4v15"/><path d="m6.5 13 5.5 6 5.5-6"/>';
    const svg = reverse.querySelector('svg');
    if (svg && svg.dataset.dir !== libSortDir){
      svg.dataset.dir = libSortDir;
      svg.innerHTML = glyph;
    }
    reverse.setAttribute('aria-pressed', String(libSortDir === 'desc'));
    reverse.title = libSortDir === 'desc' ? 'Reversed — tap for ascending' : 'Ascending — tap to reverse';
    reverse.classList.toggle('on', libSortDir === 'desc');
  }
}
// The filter controls live behind one button and open in their own centred row.
// The repository tools live behind a gear in the header's top-right corner.
function toggleAdminTools(force){
  const root = document.documentElement;
  const open = force === undefined ? !root.classList.contains('admintools-open') : !!force;
  root.classList.toggle('admintools-open', open);
  const button = document.getElementById('admintoolsbtn');
  if (button){
    button.setAttribute('aria-expanded', String(open));
    button.classList.toggle('on', open);
  }
}
document.addEventListener('pointerdown', event => {
  if (!document.documentElement.classList.contains('admintools-open')) return;
  if (event.target.closest('#admintoolsbtn, .libtools')) return;
  toggleAdminTools(false);
}, true);

function toggleFilters(force){
  const root = document.documentElement;
  const open = force === undefined ? !root.classList.contains('filters-open') : !!force;
  root.classList.toggle('filters-open', open);
  const button = document.getElementById('filterbtn');
  if (button){
    button.setAttribute('aria-expanded', String(open));
    button.classList.toggle('on', open);
  }
  try { sessionStorage.setItem('hl.filtersOpen', open ? '1' : '0'); } catch (_) {}
}

function toggleSortDir(){
  libSortDir = libSortDir === 'asc' ? 'desc' : 'asc';
  localStorage.setItem('hl.librarySortDir', libSortDir);
  paintSortControl();
  renderLibrary();
}

function sortTracks(list){
  const dir = libSortDir === "asc" ? 1 : -1;
  const out = list.slice();
  out.sort((a, b) => dir * compareTracks(libSort, a, b));
  // Preference is a tie-breaker, never a competing order.
  out.sort((a, b) => globalThis.PreferenceUI?.rank('track', a.id, b.id) || 0);
  return out;
}

function sortLabel(key){
  if (key === "votes_all") return "Votes";
  return (SORT_KEYS[key] || SORT_KEYS.recent).label;
}

// Clicking a column header sorts by it; clicking again reverses it. Votes sorts by the
// whole library's votes — sorting by your own vote only was a pointless reading.
function sortBy(key){
  if (key === "votes"){
    if (libSort !== "votes_all") { libSort = "votes_all"; libSortDir = "desc"; }
    else libSortDir = libSortDir === "desc" ? "asc" : "desc";
  } else if (libSort === key){
    libSortDir = libSortDir === "asc" ? "desc" : "asc";
  } else {
    libSort = key;
    libSortDir = key === 'votes_all' ? 'desc' : 'asc';
  }
  localStorage.setItem("hl.librarySort", libSort);
  localStorage.setItem("hl.librarySortDir", libSortDir);
  localStorage.setItem("hl.librarySortDir", libSortDir);
  const select = $("#libsort");
  if (select) select.value = libSort;
  renderLibrary();
}

function applyCardStyle(){
  const root = typeof document !== 'undefined' ? document.documentElement : null;
  if (root) root.dataset.cardstyle = (libCardsCompact && libSongLayout === 'cards') ? 'compact' : 'full';
}
function setCardsCompact(on){
  libCardsCompact = !!on;
  localStorage.setItem('hl.cardsCompact', libCardsCompact ? '1' : '0');
  if (!libCardsCompact){
    compactOpen.clear();
    document.querySelectorAll('.songcard.open').forEach(node => {
      node.classList.remove('open'); node.style.marginBottom = '';
    });
  }
  applyCardStyle();
  paintLibraryToolbar();
}
// Grid can be laid out three ways; one button cycles them, and the choice is remembered.
const GRID_STYLES = ['std', 'hex', 'mosaic'];
let gridStyle = localStorage.getItem('hl.gridStyle') || 'std';
if (!GRID_STYLES.includes(gridStyle)) gridStyle = 'std';
function paintGridStyle(){
  const root = document.documentElement;
  if (root) root.dataset.gridstyle = gridStyle;
  const button = document.getElementById('gridstylebtn');
  if (button){
    const onGrid = libSongLayout === 'grid';
    // A detail page draws its own list, so the wall shape has nothing to act on there — the
    // same rule the card-density toggle follows. The artist page is the exception: it draws
    // a wall of covers itself, so the shape still applies.
    const layoutView = detail ? detail.kind === 'artist' : ['albums', 'songs'].includes(libView);
    button.hidden = !onGrid || !layoutView;
    button.classList.toggle('on', onGrid && gridStyle !== 'std');
    const label = gridStyle === 'std' ? 'squares' : gridStyle === 'hex' ? 'honeycomb' : 'mosaic';
    button.title = 'Grid style: ' + label;
    button.setAttribute('aria-label', 'Grid style: ' + label);
    button.setAttribute('aria-pressed', String(gridStyle !== 'std'));
  }
}
function cycleGridStyle(){
  gridStyle = GRID_STYLES[(GRID_STYLES.indexOf(gridStyle) + 1) % GRID_STYLES.length];
  localStorage.setItem('hl.gridStyle', gridStyle);
  paintGridStyle();
  applyGridStyle();
  if (gridStyle === 'std') showToast('Grid: squares');
  else if (gridStyle === 'hex') showToast('Grid: honeycomb');
  else showToast('Grid: mosaic');
}
function applyGridStyle(){
  const host = document.getElementById('liblist');
  if (!host) return;
  const grid = host.querySelector('.covergrid');
  if (!grid) return;
  // The layouts are pure CSS; this only marks the element so the rules can key off it.
  grid.dataset.style = gridStyle;
  // Honeycomb needs its alternate rows marked; the others do not care.
  paintHexRows();
}
function setSongLayout(layout){
  if (!SONG_LAYOUTS.includes(layout)) return;
  libSongLayout = layout;
  localStorage.setItem('hl.songLayout', layout);
  applySongCols();
  paintLibraryToolbar();
  renderLibrary();
  applyGridStyle();
}
// Compact cards open one at a time and never touch the queue: the tap is for reading the
// card, not for playing it (that is the play button, or a double tap anywhere).
const compactOpen = new Set();
function toggleCompactCard(id){
  toggleCompactNode(document.querySelector(`.songcard[data-id="${id}"]`), id);
}
// An opened compact card is lifted out of the grid flow and drawn over its neighbours: the
// row below keeps its height, so nothing is pushed down.
function placeCompactNode(node){
  if (!node) return;
  const left = node.offsetLeft, top = node.offsetTop, width = node.offsetWidth;
  node.style.position = 'absolute';
  node.style.left = left + 'px';
  node.style.top = top + 'px';
  node.style.width = width + 'px';
  node.style.zIndex = '6';
  node.classList.add('open');
}
function toggleCompactNode(node, key){
  const open = !compactOpen.has(key);
  if (open) compactOpen.add(key); else compactOpen.delete(key);
  if (!node) return;
  const clear = () => {
    node.classList.remove('open');
    node.style.removeProperty('position');
    node.style.removeProperty('left');
    node.style.removeProperty('top');
    node.style.removeProperty('width');
    node.style.removeProperty('z-index');
    node.style.removeProperty('margin-bottom');
  };
  if (!open){ clear(); return; }
  placeCompactNode(node);
}

// One switch for both views: Albums and Songs read as a grid or as rows, and the
// density slider only means something while a grid is showing.
function paintLibraryToolbar(){
  const seg = typeof $ === 'function' ? $('#layoutseg') : null;
  if (seg){
    seg.hidden = !['albums', 'songs'].includes(libView);
    for (const button of seg.querySelectorAll('button[data-layout]')){
      const on = button.dataset.layout === libSongLayout;
      button.classList.toggle('on', on);
      button.setAttribute('aria-pressed', String(on));
    }
  }
  const slider = typeof $ === 'function' ? $('#songcols') : null;
  if (slider){
    const max = songColsMax(), now = effectiveSongCols();
    slider.max = String(max);
    slider.value = String(now);
    // One detent per column count: the ticks are drawn by CSS from the gap count, so the
    // slider reads as 2…max steps rather than a continuous fader that happens to snap.
    slider.style.setProperty('--dcols-gaps', String(Math.max(1, max - 2)));
    const readout = typeof $ === 'function' ? $('#dcolsn') : null;
    if (readout){
      readout.textContent = String(now);
      readout.title = `${now} per row`;
    }
    paintDensity();
  }
  // Playlists are always a grid of covers, so the layout switch has nothing to do there.
  // Repository-only tools: importing, moving and re-checking the shared library has no
  // meaning inside a member's own Library.
  const libTools = document.querySelector('#view-library .libtools');
  if (libTools) libTools.hidden = libraryScope !== 'shared';
  // Pins live in the member's Library, so the toggle has no business on Repository.
  const pinToggle = typeof $ === 'function' ? $('#pintogglebtn') : null;
  if (pinToggle){
    pinToggle.hidden = libraryScope !== 'mine' || !!detail;
    if (!pinToggle.hidden && window.PreferenceUI && typeof PreferenceUI.syncPinnedState === 'function') PreferenceUI.syncPinnedState();
  }
  if (typeof paintSortControl === 'function') paintSortControl();
  if (typeof paintGridStyle === 'function') paintGridStyle();
  const layoutSeg = typeof $ === 'function' ? $('#layoutseg') : null;
  const artistDetail = !!detail && detail.kind === 'artist';
  if (layoutSeg) layoutSeg.hidden = libView === 'playlists' || (libView === 'artists' && !artistDetail);
  const compact = typeof $ === 'function' ? $('#compactbtn') : null;
  if (compact){
    // A detail page (album, artist, playlist) draws its own list, so the card density
    // toggle has nothing to act on there: keep it off the toolbar entirely.
    compact.hidden = !!detail || libSongLayout !== 'cards' || !['albums', 'songs'].includes(libView);
    compact.classList.toggle('on', libCardsCompact);
    compact.setAttribute('aria-pressed', String(libCardsCompact));
  }
  applyCardStyle();
}

function renderSongs(rows, q){
  // One picker for both views (#layoutseg): List is rows, Cards are the full cards, Grid
  // is a wall of covers that opens the album when a cover is tapped.
  if (!rows.length) return emptyState(rows, q);
  const list = sortTracks(rows);
  if (libSongLayout === 'grid') return coverWall(list);
  if (libSongLayout === 'cards'){
    return `<div class="songgrid">${
      list.map((t, i) => songRow(t, i, true)).join('')}</div>`;
  }
  return songTable(list);
}

// Covers only: no buttons, no meta, just the album art and the song's name. Tapping one
// opens its album with that song marked, which is the only thing a cover wall is for.
function coverWall(list){
  const tiles = list.map(t => {
    const on = playingId === t.id;
    return `<button type="button" class="covercell${on ? ' playing' : ''}" data-id="${t.id}"
      title="${esc(t.title)}${t.album ? ' — ' + esc(t.album) : ''}" aria-label="${esc(t.title)} — open its album"
      onclick="openAlbumForTrack(${t.id})">
      ${artBox(t.artwork, t.album_id || t.album || t.title, 'coverart')}
      ${statusMarks('track', t.id)}
      <span class="covername"><b>${esc(t.title)}</b></span>
    </button>`;
  }).join('');
  return `<div class="covergrid">${tiles}</div>`;
}
// The album page the cover wall opens, with the song that was tapped marked on arrival.
function openAlbumForTrack(id){
  const rows = typeof libAll !== 'undefined' && libAll.length ? libAll : lib;
  const track = rows.find(t => t.id === id) || lib.find(t => t.id === id);
  if (!track) return;
  // Every song has a page: its release when it has one, otherwise the single group
  // albumsOf() gives it. The old "no album page" toast was the albumless case only.
  const album = albumsOf(lib.length ? lib : rows).find(a => a.tracks.some(t => t.id === id))
             || albumsOf(rows).find(a => a.tracks.some(t => t.id === id))
             || albumsOf(currentRows || []).find(a => a.tracks.some(t => t.id === id));
  if (!album){ showToast('That song is not in this scope — switch Repository / Library'); return; }
  detail = {kind: 'album', key: album.key};
  renderLibrary();
  scrollTo({top: 0, behavior: 'instant'});
  setTimeout(() => focusSongRow(id), 80);
}

function goneLabel(track){
  if (track.source_status === "gone") return track.provider === "apple" ? "No longer in this playlist" : "Removed from the source";
  if (track.source_status === "missing") return track.provider === "apple" ? "Not on Apple Music now" : "Unavailable at the source";
  return "";
}

function songRow(track, index, card = false, opts = {}){
  const on = playingId === track.id;
  const gone = goneLabel(track);
  // Removed on this visit: the row stays put, dimmed, with its + ready to undo.
  const removed = typeof libraryPeek !== "undefined" && libraryPeek.has(Number(track.id));
  // A SoundCloud set's name is stored as the album, so the right-hand cell on a card
  // would just repeat the playlist ("Shuffle") the songs came from. Real album names stay.
  const setAlbum = (track.playlists || []).some(p => p.title === track.album);
  // Our own copy of the cover (by digest) when the server knows one: it is the same picture
  // at every width, it does not depend on a provider CDN, and the service worker can keep it
  // for offline play. A row with no digest keeps the provider URL, as before.
  const art = trackArt(track);
  return `<div class="songrow ${card ? 'songcard' : ''} ${on ? "playing" : ""} ${gone ? "gone" : ""} ${removed ? "removed" : ""} ${track.playable ? "" : "absent"}" data-id="${track.id}" tabindex="0" aria-label="${esc(track.title)}${on ? " — playing" : ""} — song actions available with Shift+F10">
    <div class="idx${on ? " playing" : ""}">${on ? SVG.bars(11) : index + 1}</div>
    ${card ? "" : statusMarks("track", track.id)}
    <div class="thumb">${art ? artImg(art, "", true, track.album || track.title) : ""}${card && Number(track.bpm) > 0 ? `<span class="bpm" title="Detected tempo">${Math.round(Number(track.bpm))}</span>` : ""}${card ? `<button type="button" class="snext" title="Play next" aria-label="Play next" onclick="event.stopPropagation();queueSong(${track.id}, true)">${SVG.toNext(16)}</button>` : ""}${on && card ? `<span class="nowbars" aria-hidden="true">${SVG.bars(14)}</span>` : ""}</div>
    <div class="st"><span class="sttext"><b>${esc(track.title)}</b>${gone ? `<span class="gone-badge" title="Checked ${track.source_checked ? new Date(track.source_checked * 1000).toLocaleString() : "recently"} — the downloaded file still plays">${esc(gone)}</span>` : ""}${removed ? `<span class="removed-badge">Removed</span>` : ""}<span class="kept-badge" title="Kept on this device — plays without a connection" hidden>Kept</span></span><div class="rowactions">${listeningSaveControl("track", track.id)}</div></div>
    <div class="sartist">${esc(track.artist_name || "—")}</div>
    <!-- A card carries its tempo on the cover, so the tag column would repeat it. -->
    <div class="listtags">${card ? "" : sourceTag(track) + (Number(track.bpm) > 0 ? `<span class="bpm" title="Detected tempo">${Math.round(Number(track.bpm))}</span>` : "")}</div>
    <div class="salbum">${card && setAlbum ? "" : esc(track.album || "")}</div>
    ${opts.quietVotes ? '<div class="votes"></div>' : voteRow("track", track.id)}
    <div class="sdur">${track.playable ? fmtClock(track.duration) : esc(track.status || "missing")}</div>
    <span class="send">
      ${track.playable ? `<a class="sdl" href="/api/track/${track.id}/download" download title="Download" onclick="event.stopPropagation()">↓</a>` : ""}
      ${opts.grab && !track.playable ? `<button type="button" class="sdl-grab" title="Download this song"
        aria-label="Download ${esc(track.title)}" onclick="event.stopPropagation();grabMissing([${track.id}])">${DOWNLOAD_ICON}</button>` : ""}
      <button type="button" class="splay${track.playable ? "" : " off"}" ${track.playable ? "" : "disabled"} title="${track.playable ? (on && !playerAudio.paused ? "Pause" : "Play") : "Not downloaded yet"}" aria-label="${track.playable ? (on && !playerAudio.paused ? "Pause" : "Play") : "Not downloaded yet"}" onclick="event.stopPropagation();${track.playable ? `toggleSongPlayback(${track.id})` : ""}">${on && !playerAudio.paused && track.playable ? SVG.pause(13) : SVG.play(13)}</button>
    </span>
    ${card ? "" : `<button type="button" class="rowmore" title="More options" aria-label="More options" onclick="event.stopPropagation();openRowMenu(this, ${track.id})">⋯</button>`}
  </div>`;
}

// A playlist's collage is a mosaic of the records inside it, as many distinct covers as
// fill the rectangle whole: 12, 9, 8, 6, 4, 3, 2 or 1. A set with no artwork still gets
// its initials, so the card reads as a playlist rather than a black hole.
const COLLAGE_STEPS = [[12, "c12"], [9, "c9"], [8, "c8"], [6, "c6"], [4, "c4"], [3, "c3"], [2, "c2"], [1, "c1"]];
function playlistCollage(arts, title){
  // The same album twice would spend a tile on a cover already shown.
  const unique = Array.from(new Set(arts));
  const step = COLLAGE_STEPS.find(([n]) => unique.length >= n);
  const shown = step ? unique.slice(0, step[0]) : [];
  const cells = shown.map(a => `<i style="background-image:url('${esc(a)}')"></i>`).join("")
    || `<i class="ph">${esc(initials(title))}</i>`;
  return `<div class="collage ${step ? step[1] : "c1"}">${cells}</div>`;
}

function renderPlaylistsView(rows, q){
  const playable = new Set(lib.map(t => t.id));
  const cards = scopedPlaylists().filter(p => !q || `${p.title} ${playlistTracks(p.id).map(t => `${t.title} ${t.artist_name} ${t.album}`).join(' ')}`.toLowerCase().includes(q));
  cards.sort((a,b) => globalThis.PreferenceUI?.rank('playlist',a.id,b.id) || 0);
  if (!cards.length) return emptyState(rows, q) || `<div class="empty">No playlists yet — queue a SoundCloud set or an Apple Music playlist.</div>`;
  return `<div class="plgrid">${cards.map(p => {
    const tracks = playlistTracks(p.id);
    const arts = tracks.map(t => t.artwork).filter(Boolean);
    const done = tracks.filter(t => playable.has(t.id)).length;
    return `<div class="plcard" onclick="openPlaylist(${p.id})">
      ${playlistCollage(arts, p.title)}
      <div class="pbody">
        <div class="pt" title="${esc(p.title)}">${esc(p.title)}</div>
        <div class="ps"><span class="plcount">${done}/${tracks.length}<span class="plword"> downloaded</span></span><span class="plby" title="${esc(p.added_by || "")}">${esc(p.added_by || "")}</span></div>
        <div class="pacts">${downloadControl(libraryScope === 'mine' ? '/api/library/download?ids=' + tracks.map(t => t.id).join(',') : `/api/playlist/${p.id}/download`, done > 0, "ZIP")}${listeningSaveControl("playlist", p.id)}</div>
      </div>
    </div>`;
  }).join("")}</div>`;
}

function renderFolders(rows, q){
  if (!listeningSaves) return '<div class="empty">Sign in as an approved member to view private folders.</div>';
  if (activeFolderId && !personalFolder(activeFolderId)) activeFolderId = null;
  const current = personalFolder(activeFolderId);
  const folders = folderChildren(activeFolderId).filter(f => !q || folderPath(f.id).toLowerCase().includes(q));
  const folderCards = folders.map(f => {
    const count = (listeningSaves.folder_tracks || []).filter(t => t.folder_id === f.id).length;
    return `<button class="foldercard" onclick="openPersonalFolder(${f.id})"><span class="foldericon">▰</span><span class="foldername">${esc(f.name)}</span><span class="foldermeta">${count} song${count === 1 ? '' : 's'}</span></button>`;
  }).join('');
  const header = current
    ? `<div class="folderhead"><button class="backbtn" onclick="leavePersonalFolder()">‹ ${current.parent_id ? 'Parent folder' : 'All folders'}</button><div class="grow"><b>${esc(current.name)}</b><small>${esc(folderPath(current.id))}</small></div><button class="ghost" onclick="renamePersonalFolder(${current.id})">Rename</button><button class="ghost danger" onclick="deletePersonalFolder(${current.id})">Delete</button></div>`
    : `<div class="folderhead"><div class="grow"><b>Your folders</b><small>Private to your profile. Songs reference the shared Repository.</small></div></div>`;
  if (!current && !folders.length) return header + `<div class="empty">${q ? 'No folders match that search.' : 'No folders yet. Create one, then right-click a Repository song to add it here.'}</div>`;
  const direct = new Set((listeningSaves.folder_tracks || []).filter(t => t.folder_id === activeFolderId).map(t => t.track_id));
  const songs = current ? rows.filter(t => direct.has(t.id)) : [];
  const songContent = !current ? '' : songs.length ? renderSongs(songs,q)
    : `<div class="empty">${q ? 'No songs match that search in this folder.' : 'This folder is empty. Right-click a Repository song and choose this folder.'}</div>`;
  return header + (folders.length ? `<div class="folderlist">${folderCards}</div>` : current ? '' : '') + songContent;
}

// ------------------------------------------------------------------ detail views
function renderDetail(rows){
  if (detail.kind === "album"){
    // The release, not just the part we have: pending songs stay in the tracklist so the
    // album reads whole and each missing one can be grabbed on its own.
    const album = albumsOf(scopedTracks()).find(a => a.key === detail.key)
               || albumsOf(rows).find(a => a.key === detail.key);
    if (!album){ detail = null; return renderAlbums(rows, ""); }
    return albumDetail(album);
  }
  if (detail.kind === "artist"){
    const artist = artistsOf(rows).find(a => a.name === detail.key);
    if (!artist){ detail = null; return renderArtists(rows, ""); }
    renderedAlbums = sortAlbums(artist.albums).filter(a => !a.single && a.tracks.length > 1);
    const artistTracks = artist.tracks;
    renderedArtistSongs = artistTracks;
    const songs = artistTracks.length;
    const profile = artistProfiles.get(artist.name);
    return `<button class="backbtn" onclick="closeDetail()">‹ Back</button>
      <div class="hero">
        ${artBox(artist.art, artist.name, "hart round")}
        <div class="hmeta"><div class="kicker">Artist</div><h3>${esc(artist.name)}</h3>
          <div class="hsub">${artist.albums.length ? `${artist.albums.length} album${artist.albums.length === 1 ? "" : "s"} · ` : ""}${songs} song${songs === 1 ? "" : "s"}${profile ? ` · ${profile.downloaded} downloaded` : ""}</div>
          <div class="hacts"><button class="runplay" onclick="playArtist()">${SVG.play(14)} Play</button>
            ${profile ? subscriptionControl(profile, artist.name) : ""}
            <button class="ghost" onclick="createArtistPlaylist()">Create playlist</button>
          </div>
        </div>
      </div>
      ${profile ? artistProfileBlock(profile) : ""}
      ${renderedAlbums.length
        ? (libSongLayout === 'list'
            ? `<div class="songtable">${songHead()}${renderedAlbums.map((a, i) => albumRow(a, i)).join("")}</div>`
            : libSongLayout === 'grid'
              ? albumCoverWall(renderedAlbums)
              : `<div class="songgrid">${renderedAlbums.map((a, i) => albumCard(a, i)).join("")}</div>`)
        : renderSongs(artistTracks, "")}`;
  }
  // playlist
  const playlist = scopedPlaylists().find(p => String(p.id) === String(detail.key));
  if (!playlist){ detail = null; return renderPlaylistsView(rows, ""); }
  const allTracks = playlistTracks(playlist.id);
  const tracks = allTracks.filter(t => t.playable);      // only what plays
  const art = tracks.map(t => t.artwork).filter(Boolean)[0] || "";
  const duration = tracks.reduce((n, t) => n + (t.duration || 0), 0);
  const playable = tracks.length;
  const pendingTracks = allTracks.length - tracks.length;
  return `<button class="backbtn" onclick="closeDetail()">‹ Back</button>
    <div class="hero">
      ${artBox(art, playlist.title, "hart")}
      <div class="hmeta"><div class="kicker">Playlist</div><h3>${esc(playlist.title)}</h3>
        <div class="hsub">${tracks.length} song${tracks.length === 1 ? "" : "s"} ready${pendingTracks ? ` · ${pendingTracks} still downloading` : ""}${duration ? ` · ${fmtLong(duration)}` : ""}</div>
        <div class="hacts">
          <button class="runplay" onclick="playPlaylist()">${SVG.play(14)} Play</button>
          ${pendingTracks ? `<button class="ghost" data-admin-only onclick="grabDetailPlaylist()">Download missing (${pendingTracks})</button>` : ""}
          ${downloadControl(singleDownloadUrl(tracks, libraryScope === 'mine' ? '/api/library/download?ids=' + tracks.map(t => t.id).join(',') : `/api/playlist/${playlist.id}/download`), playable > 0, singleDownloadLabel(tracks, "Download ZIP"))}
          ${playlist.provider === 'apple' && allTracks.length ? appleBackupControls(playlist) : ""}
          ${listeningSaveControl("playlist", playlist.id)}
          ${playlist.can_edit ? `<button class="ghost" onclick="editPlaylist(${playlist.id})">Edit</button>` : ""}
          ${socialControl("playlist", playlist.id)}
        </div>
      </div>
    </div>
    <div class="detaillist tracklist">${songTable(tracks, "Nothing downloaded from this playlist yet.")}</div>`;
}

// A single has no album discussion target — the server validates a normalized artist+album
// digest and a nameless release has none — so its talk and votes belong to the song itself,
// the same thread its row opens. Nothing is removed from the page.
function socialTarget(album){
  return album.single && album.tracks[0] ? socialControl("track", album.tracks[0].id)
                                         : socialControl("album", album.key);
}
function albumDetail(album){
  const duration = album.duration;
  const playable = album.tracks.filter(t => t.playable).length;
  // The title holds the container's name (album or set); the artist sits under it and
  // stays a link into the Repo, whichever kind of container this is.
  const who = `<a role="link" tabindex="0" data-artist="${esc(album.artist)}"
          title="Open ${esc(album.artist)} in the Repo"
          onclick="openArtistByName(this.dataset.artist)"
          onkeydown="if(event.key==='Enter'||event.key===' '){event.preventDefault();openArtistByName(this.dataset.artist)}">${esc(album.artist)}</a>`;
  return `<button class="backbtn" onclick="closeDetail()">‹ Back</button>
    <div class="hero">
      <div style="position:relative;flex:0 0 auto">${artBox(album.art, album.title, "hart")}
        <div class="bubbles" data-bkey="${album.key}"></div></div>
      <div class="hmeta">
        <div class="kicker">${album.single ? "Single" : "Album"}</div>
        <h3>${esc(album.title)}</h3>
        <div class="hsub">${who} · ${album.tracks.length} song${album.tracks.length === 1 ? "" : "s"}${duration ? ` · ${fmtLong(duration)}` : ""}${album.tracks.length - playable ? ` · <span style="color:#ffd9a1">${album.tracks.length - playable} not downloaded</span>` : ""}</div>
        <div class="hacts">
          <button class="runplay" onclick="playDetailAlbum()">${SVG.play(14)} Play</button>
          <button class="ghost" onclick="shuffleDetailAlbum()">Shuffle</button>
          ${album.tracks.length - playable ? `<button class="ghost grabmissing" data-admin-only
             title="Queue every song this album has and your library does not"
             onclick="askGrabAlbum()">${SVG.sync(14)} Grab ${album.tracks.length - playable} missing</button>` : ""}
          ${downloadControl(singleDownloadUrl(album.tracks, "/api/library/download?ids=" + album.tracks.map(t => t.id).join(",")), playable > 0, singleDownloadLabel(album.tracks, "Download ZIP"))}
          ${album.albumId && adminUI() ? `<button class="ghost danger" id="delalbum-${album.albumId}"
             onclick="deleteAlbum(${album.albumId}, ${album.tracks.length}, this)">Delete album</button>` : ""}
          ${socialTarget(album)}
        </div>
      </div>
    </div>
    <div class="detaillist tracklist">${songTable(album.tracks, undefined, {grab: true, classes: "grabable", quietVotes: true})}</div>`;
}

// Grabbing a whole release is a queue operation the person should agree to; grabbing one
// song from a row is not, so only the bulk button asks.
function askGrabAlbum(){
  const album = albumsOf(scopedTracks()).find(a => a.key === detail?.key);
  const ids = album ? album.tracks.filter(t => !t.playable).map(t => t.id) : [];
  if (!ids.length){ showToast("Nothing is missing from this album."); return; }
  const dialog = document.getElementById("dlg-grab");
  if (!dialog){ grabMissing(ids); return; }
  grabPending = ids;
  document.getElementById("dlg-grab-text").textContent =
    `Queue ${ids.length} song${ids.length === 1 ? "" : "s"} from “${album.title}”? They download in the background, in album order.`;
  dialog.showModal();
}
let grabPending = [];
document.getElementById("dlg-grab")?.addEventListener("close", event => {
  if (event.target.returnValue === "go" && grabPending.length) grabMissing(grabPending);
  grabPending = [];
});

// Deleting audio is destructive and irreversible, so it takes two clicks: the
// first arms the button, the second does it. Anything else is one stray tap.
function armButton(node, label, action){
  if (!node) return;
  if (node.dataset.arm === "1"){
    node.dataset.arm = "";
    node.textContent = label;
    clearTimeout(node._armTimer);
    action();
    return;
  }
  node.dataset.arm = "1";
  node.textContent = "Click again to confirm";
  clearTimeout(node._armTimer);
  node._armTimer = setTimeout(() => { node.dataset.arm = ""; node.textContent = label; }, 4000);
}

async function deleteAlbum(albumId, count, button){
  armButton(button || document.getElementById(`delalbum-${albumId}`), "Delete album", async () => {
    try {
      const res = await api(`/api/release/${albumId}/remove`, {delete_files: true});
      const n = res.deleting != null ? res.deleting : count;
      showToast(`Deleted ${n} track${n === 1 ? "" : "s"}, ${fmtBytes(res.bytes || 0)} freed`);
      detail = null;
      renderLibrary();
      refresh();
    } catch (e){ showToast(e.message); }
  });
}

async function deletePlaylistFiles(pid, count, button){
  armButton(button, "Delete files", async () => {
    try {
      const res = await api(`/api/playlist/${pid}/remove`, {delete_files: true});
      const n = res.deleting || 0;
      showToast(`Deleted ${n} track${n === 1 ? "" : "s"}, ${fmtBytes(res.bytes || 0)} freed`);
    } catch (e){ showToast(e.message); }
    refresh();
  });
}

// One song off the shelf: the file goes, the row stays as a tombstone so the next
// playlist refresh cannot quietly download it back. Settings › Deleted songs is
// where the decision is reversible.
async function deleteTrack(id, title){
  if (!confirm(`Delete “${title || "this song"}” from the library?\n\nIts file is removed. Settings › Deleted songs can put it back.`)) return;
  try {
    const res = await api(`/api/track/${Number(id)}/delete`, {delete_file: true});
    if (res.error) throw new Error(res.error);
    showToast(res.removed_file ? `Deleted “${res.title || title || "song"}”`
                               : `Removed “${res.title || title || "song"}” from the library`);
    await loadLibrarySnapshot();
    refresh();
  } catch (e){ showToast(e.message); }
}

