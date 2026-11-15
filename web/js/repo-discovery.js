/* Repo discovery: what the followed artists have released, the follows themselves, and
   the artist/release pages behind them.

   `JS-030`. A plain classic script, loaded before the inline one: no build step, and every
   name below stays global exactly as it was in index.html, so the rest of the page does not
   know this moved. Definitions only — nothing here runs at load. The few load-time DOM
   bindings that used `SVG` or the markup stay in index.html, after `const SVG`, because a
   `const` in a later script is not visible while an earlier one is still running.
*/
let feedItems = [], subscriptions = [];

function feedCard(item, preview){
  const badge = item.decision === "new" ? "new" : item.decision;
  const when = ago(item.first_seen);
  const songs = Number(item.track_count || 0);
  const openable = !preview && albumLike(item) && !!item.source_url;
  const chip = songs > 1 ? `${songs} songs` : "Songs";
  const outs = item.decision === "new"
    ? `<button class="primary feeddl" data-admin-only data-queue="${item.id}"
         aria-label="Download ${escapeHtml(item.title)}"
         title="Download ${escapeHtml(item.title)}">${SVG.download(16)}</button>
       ${openable ? `<button type="button" class="feedtrks" data-tracks="${item.id}"
         data-url="${escapeHtml(item.source_url)}"
         title="Show the songs on this release">${chip}</button>` : ""}
       <button data-open="${item.id}">Open</button>`
    : `<span class="pill ${badge}">${badge}</span>
       <button data-open="${item.id}">Open</button>`;
  return `<div class="feedrel"><div class="feedcard">
    <div class="feedart">${(item.artist || item.title || "?").slice(0, 1).toUpperCase()}${
      item.artwork ? `<img src="${escapeHtml(item.artwork)}" alt="" loading="lazy" onerror="this.remove()">` : ""}</div>
    <div class="feedmain">
      <div class="feedtitle">${escapeHtml(item.title)}</div>
      <div class="feedmeta">${escapeHtml(item.artist || "")}
        · <span class="pill ${badge}">${badge}</span>${item.release_type ? ` · ${item.release_type}` : ""}
        ${item.release_date ? `· ${item.release_date}` : ""}
        · ${when}</div>
    </div>
    ${preview ? "" : `<div class="feedacts">${outs}</div>`}
  </div>${openable ? `<div class="feedtracks" data-for="${item.id}" hidden></div>` : ""}</div>`;
}

// Latest first, not "the last artist I followed first": Apple carries a real
// release date, undated providers (SoundCloud) keep the order they were listed.
function feedSort(a, b){
  const da = a.release_date || "", db = b.release_date || "";
  if (da && db) return db.localeCompare(da) || (b.id - a.id);
  if (da) return -1;
  if (db) return 1;
  return b.id - a.id;
}

const openFeedArtists = new Set();   // collapsed artist rows stay as you left them
// The Artists feed shows one provider at a time: a follow lives in the provider it
// was made on, so Apple Music and SoundCloud are separate lists.
let feedProvider = (() => {
  try { return localStorage.getItem("hl.feedProvider") || "apple"; } catch (_) { return "apple"; }
})();

function paintFeedSwitch(){
  document.querySelectorAll(".feedsw").forEach(b => {
    const on = b.dataset.feedprov === feedProvider;
    b.classList.toggle("on", on);
    b.setAttribute("aria-pressed", String(on));
  });
}

// "My" narrows the Artists feed to the subscriptions this member made; off shows
// every subscription on the install, which is what the Feed has always done.
let feedMine = (() => {
  try { return localStorage.getItem("hl.feedMine") === "1"; } catch (_) { return false; }
})();

function paintFeedMine(){
  const button = document.getElementById("feedmine");
  if (!button) return;
  button.classList.toggle("on", feedMine);
  button.setAttribute("aria-pressed", String(feedMine));
  button.title = feedMine ? "Showing only artists you subscribe to — tap for every artist"
    : "Only artists you subscribe to";
}

function setFeedMine(on){
  feedMine = !!on;
  try { localStorage.setItem("hl.feedMine", feedMine ? "1" : "0"); } catch (_) {}
  paintFeedMine();
  renderFeed();
}


function setFeedProvider(provider){
  if (!provider || provider === feedProvider) return;
  feedProvider = provider;
  try { localStorage.setItem("hl.feedProvider", provider); } catch (_) {}
  paintFeedSwitch();
  renderFeed();
}


function artistKey(name){
  return String(name || "").trim().toLowerCase().replace(/\s+/g, " ");
}

// One row per artist, ordered by whoever released last — not by who was
// followed last, which used to stack every new follow on top.
// Ten newest releases per artist, plus their five newest albums: without the
// album slots a run of singles pushes every album off the list.
const FEED_RELEASES = 10, FEED_ALBUMS = 5;
function albumLike(item){
  // SoundCloud has no albums: a set is the thing that holds several songs, and
  // the only way to know is its permalink. Its track count is unknown until it
  // is opened, so the row says "Songs" rather than a made-up number.
  return Number(item.track_count || 0) > 1
    || ["album", "ep", "playlist", "mix"].includes(String(item.release_type || ""))
    || /\/sets\//.test(String(item.source_url || ""));
}

function capReleases(items){
  const keep = new Set(items.slice(0, FEED_RELEASES));
  for (const item of items.filter(albumLike).slice(0, FEED_ALBUMS)) keep.add(item);
  return items.filter(i => keep.has(i));
}

function feedGroups(items){
  const map = new Map();
  for (const item of items){
    const name = item.artist || item.follow_name || "Unknown artist";
    let group = map.get(name);
    if (!group){
      group = {name, items: [], latest: "", newest: 0, watchId: item.watch_id || 0};
      map.set(name, group);
    }
    group.items.push(item);
    const date = item.release_date || "";
    if (date > group.latest) group.latest = date;
    group.newest = Math.max(group.newest, Number(item.id) || 0);
  }
  for (const group of map.values()){
    group.items.sort(feedSort);
    group.items = capReleases(group.items);
    group.new = group.items.filter(i => i.decision === "new").length;
    const pictured = group.items.find(i => i.artwork);
    group.art = artistArt[artistKey(group.name)] || (pictured ? pictured.artwork : "");
    group.watch = subscriptions.find(f => Number(f.id) === Number(group.watchId)) || null;
  }
  return [...map.values()].sort((a, b) => {
    if (a.latest !== b.latest) return a.latest < b.latest ? 1 : -1;
    return b.newest - a.newest;
  });
}

function subscriptionSeat(group){
  // Everyone view only: the seat where the collapse triangle used to sit is where
  // you subscribe yourself. A red minus means you already do, and pressing it
  // takes the artist out of YOUR list — the subscription itself stays.
  if (feedMine || !group.watch) return "";
  const name = escapeHtml(group.name);
  if (group.watch.mine){
    return `<button type="button" class="feedgfollow out" data-unsub="${group.watch.id}"
      aria-label="Unsubscribe from ${name}"
      title="Unsubscribe from ${name} — the artist stays on the feed for everyone">${SVG.minus(15)}</button>`;
  }
  // Somebody else's subscription cannot be claimed from here, so the seat stays
  // empty rather than offering a button that would do nothing.
  if (group.watch.member_id) return "";
  if (!group.watch.artist_url) return "";
  return `<button type="button" class="feedgfollow" data-follow-url="${escapeHtml(group.watch.artist_url)}"
    data-follow-name="${name}" aria-label="Subscribe to ${name}"
    title="Subscribe to ${name} — nothing downloads until you pick something">${SVG.personPlus(16)}</button>`;
}

function feedGroupRow(group){
  const open = openFeedArtists.has(group.name);
  const letter = (group.name || "?").slice(0, 1).toUpperCase();
  const name = escapeHtml(group.name);
  const releases = group.items.length;
  // The fold lives at the row's edge and is always on screen: the portrait and the name
  // are the door to the artist's page, so without it a collapsed group had no visible way
  // back open (the only cue was a faint chevron under a faded preview). A collapsed group
  // still keeps its newest release cut off at the waist, now with a strip that names what
  // opening it would show.
  return `<div class="feedgroup">
    <div class="feedghead${open ? " open" : ""}" data-artist="${escapeHtml(group.name)}"
      role="button" tabindex="0" aria-expanded="${open}">
      <span class="feedgart">${letter}${
        group.art ? `<img src="${escapeHtml(group.art)}" alt="" loading="lazy" onerror="this.remove()">` : ""}</span>
      <span class="feedgname">${name}</span>
      <span class="feedgmeta">${group.new ? `<span class="pill new">${group.new} new</span>` : ""}${
        group.latest ? `<span>${group.latest}</span>` : ""}</span>
      ${subscriptionSeat(group)}
      <button type="button" class="feedgfold" data-fold="${name}"
        aria-expanded="${open}" aria-label="${open ? "Hide" : "Show"} ${releases} release${
          releases === 1 ? "" : "s"} from ${name}"
        title="${open ? "Hide" : "Show"} releases">${SVG.chevronDown(18)}</button>
    </div>
    <div class="feedgbody"${open ? "" : " hidden"}>${group.items.map(i => feedCard(i)).join("")}</div>
    <div class="feedgpeek" data-peek="${escapeHtml(group.name)}" aria-hidden="true"${open ? " hidden" : ""}>
      <div class="feedgpeekrows">${group.items.slice(0, 3).map(i => feedCard(i, true)).join("")}</div>
      <div class="feedgmore" aria-hidden="true">${SVG.chevronDown(16)}<span>Show ${
        releases} release${releases === 1 ? "" : "s"}</span></div>
    </div>
  </div>`;
}

// Expanding must not repaint the list: a full renderFeed() re-creates every
// <img>, which makes every cover flash and reload. Both halves are already in the
// DOM, so opening is just a hidden flip.
function toggleGroup(head){
  const group = head.closest(".feedgroup");
  if (!group) return;
  const name = head.dataset.artist;
  const open = !openFeedArtists.has(name);
  if (open) openFeedArtists.add(name);
  else openFeedArtists.delete(name);
  head.classList.toggle("open", open);
  head.setAttribute("aria-expanded", String(open));
  const body = group.querySelector(".feedgbody");
  const peek = group.querySelector(".feedgpeek");
  if (body) body.hidden = !open;
  if (peek) peek.hidden = open;
  // The fold button carries the same state, because it is the control most people
  // will press and a screen reader reads its label, not the head's attribute.
  const fold = head.querySelector(".feedgfold");
  if (fold){
    const count = body ? body.querySelectorAll(".feedrel").length : 0;
    fold.setAttribute("aria-expanded", String(open));
    fold.setAttribute("aria-label",
      `${open ? "Hide" : "Show"} ${count} release${count === 1 ? "" : "s"} from ${name}`);
    fold.title = `${open ? "Hide" : "Show"} releases`;
  }
}

// ---- release rows: an album opens to its songs, each downloadable on its own ----
// Nothing here repaints the feed: a tracklist is filled into its own container the
// first time it is opened, then only shown and hidden.
async function loadReleaseTracks(url){
  const response = await fetch(`/api/tracks?url=${encodeURIComponent(url)}`, {cache: "no-store"});
  const data = await response.json().catch(() => ({}));
  if (!response.ok) throw new Error(data.error || "Could not read that tracklist");
  const tracks = data.tracks || [];
  if (!tracks.length) throw new Error("That release has no separate songs");
  return tracks;
}

async function fillTracks(box, url){
  if (box.dataset.loaded) return;
  box.innerHTML = `<div class="feedtrkmsg">Reading the tracklist…</div>`;
  const tracks = await loadReleaseTracks(url);
  box.innerHTML = tracks.map(t => `<div class="feedtrk">
      <span class="feedtrkno">${t.n}</span>
      <span class="feedtrkname" title="${escapeHtml(t.title)}">${escapeHtml(t.title)}</span>
      <button type="button" class="feeddl" data-admin-only data-track-url="${escapeHtml(t.url)}"
        data-track-title="${escapeHtml(t.title)}"
        aria-label="Download ${escapeHtml(t.title)}"
        title="Download ${escapeHtml(t.title)}">${SVG.download(14)}</button>
    </div>`).join("");
  box.dataset.loaded = "1";
  box.querySelectorAll("[data-track-url]").forEach(b => b.onclick = () => queueOne(b));
}

async function queueOne(button){
  button.disabled = true;
  try {
    await api("/api/track/queue", {url: button.dataset.trackUrl});
    button.classList.add("sent");
    showToast(`Queued — ${button.dataset.trackTitle} · joins your library when it finishes`);
  } catch (e) {
    button.disabled = false;
    showToast(e.message || "Could not queue that song");
  }
}

function bindReleaseRows(root){
  root.querySelectorAll("[data-tracks]").forEach(b => b.onclick = async () => {
    const box = b.closest(".feedrel").querySelector(".feedtracks");
    if (!box) return;
    if (!box.hidden){ box.hidden = true; b.classList.remove("on"); return; }
    box.hidden = false; b.classList.add("on");
    if (box.dataset.loaded) return;
    b.disabled = true;
    try { await fillTracks(box, b.dataset.url); }
    catch (e){ box.innerHTML = `<div class="feedtrkmsg">${escapeHtml(e.message)}</div>`; }
    b.disabled = false;
  });
  root.querySelectorAll("[data-cat-url]").forEach(b => b.onclick = async () => {
    b.disabled = true;
    try {
      await api("/api/track/queue", {url: b.dataset.catUrl});
      showToast(`Queued — ${b.dataset.catTitle} · joins your library when it finishes`);
    } catch (e) {
      b.disabled = false;
      showToast(e.message || "Could not queue that release");
    }
  });
}

// ---- an artist's own page, opened from the group header --------------------
let artistPageWatch = 0;
const catalogueCache = new Map();

// The artist page: four newest albums as a 2x2 cover grid, then the artist's
// songs in whichever layout was picked last. The layout is its own choice — the
// library's default is cards, this section starts as a list.
const ARTIST_LAYOUTS = ["list", "cards", "grid"];
let artistLayout = (() => {
  try { const saved = localStorage.getItem("hl.artistLayout");
    return ARTIST_LAYOUTS.includes(saved) ? saved : "list"; } catch (_) { return "list"; }
})();
let artistPageAlbums = [];

function artistSongMeta(release){
  return `${escapeHtml(release.release_type || "song")}${
    release.release_date ? ` · ${escapeHtml(release.release_date)}` : ""}${
    Number(release.track_count || 0) > 1 ? ` · ${release.track_count} songs` : ""}`;
}

function artistDownload(release, size){
  return `<button type="button" class="primary feeddl" data-admin-only
    data-cat-url="${escapeHtml(release.source_url)}" data-cat-title="${escapeHtml(release.title)}"
    aria-label="Download ${escapeHtml(release.title)}"
    title="Download ${escapeHtml(release.title)}">${SVG.download(size || 16)}</button>`;
}

function artistSongRow(release){
  const letter = (release.title || "?").slice(0, 1).toUpperCase();
  return `<div class="feedrel"><div class="feedcard">
    <div class="feedart">${letter}${release.artwork
      ? `<img src="${escapeHtml(release.artwork)}" alt="" loading="lazy" onerror="this.remove()">` : ""}</div>
    <div class="feedmain">
      <div class="feedtitle">${escapeHtml(release.title)}</div>
      <div class="feedmeta">${artistSongMeta(release)}</div>
    </div>
    <div class="feedacts">${artistDownload(release)}</div>
  </div></div>`;
}

function artistSongCard(release){
  return `<div class="facard">
    ${artBox(release.artwork, release.title, "facover")}
    <div class="fatitle">${escapeHtml(release.title)}</div>
    <div class="fametas">${artistSongMeta(release)}</div>
    <div class="factl">${artistDownload(release, 15)}</div>
  </div>`;
}

function artistSongTile(release){
  return `<button type="button" class="covercell" data-cat-open="${escapeHtml(release.source_url)}"
    title="${escapeHtml(release.title)} — open on the source">
    ${artBox(release.artwork, release.title, "coverart")}
    <span class="covername"><b>${escapeHtml(release.title)}</b></span>
  </button>`;
}

function artistSongsHtml(releases){
  if (!releases.length)
    return `<div class="empty">${artistFilterQuery()
      ? "Nothing here matches that filter."
      : "No singles yet — everything this artist published is in the albums above."}</div>`;
  // Same containers the Library uses, so the same remembered column count applies
  // instead of a second grid that only looks similar.
  if (artistLayout === "cards")
    return `<div class="songgrid">${releases.map(artistSongCard).join("")}</div>`;
  if (artistLayout === "grid")
    return `<div class="covergrid">${releases.map(artistSongTile).join("")}</div>`;
  return `<div class="falist">${releases.map(artistSongRow).join("")}</div>`;
}

function artistLayoutSeg(){
  const icon = (name, title, inner) => `<button type="button" data-falayout="${name}"
    title="${title}" aria-label="${title}" aria-pressed="${artistLayout === name}">${inner}</button>`;
  return `<nav class="seg icons" id="fapLayout" aria-label="Songs layout">${
    icon("cards", "Cards", `<svg viewBox="0 0 24 24" width="16" height="16" fill="none" stroke="currentColor" stroke-width="1.7" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true"><rect x="3" y="4" width="8" height="7" rx="1.6"/><rect x="13" y="4" width="8" height="7" rx="1.6"/><rect x="3" y="13" width="8" height="7" rx="1.6"/><rect x="13" y="13" width="8" height="7" rx="1.6"/></svg>`)
  }${icon("grid", "Covers only", `<svg viewBox="0 0 24 24" width="16" height="16" fill="none" stroke="currentColor" stroke-width="1.7" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true"><rect x="3.5" y="3.5" width="7" height="7" rx="1.4"/><rect x="13.5" y="3.5" width="7" height="7" rx="1.4"/><rect x="3.5" y="13.5" width="7" height="7" rx="1.4"/><rect x="13.5" y="13.5" width="7" height="7" rx="1.4"/></svg>`)
  }${icon("list", "List", `<svg viewBox="0 0 24 24" width="16" height="16" fill="none" stroke="currentColor" stroke-width="1.8" stroke-linecap="round" aria-hidden="true"><path d="M4 6.5h16"/><path d="M4 12h16"/><path d="M4 17.5h16"/></svg>`)
  }</nav>`;
}

function paintArtistLayout(){
  const seg = document.getElementById("fapLayout");
  if (!seg) return;
  seg.querySelectorAll("[data-falayout]").forEach(b =>
    b.setAttribute("aria-pressed", String(b.dataset.falayout === artistLayout)));
  seg.querySelectorAll("[data-falayout]").forEach(b => b.onclick = () => setArtistLayout(b.dataset.falayout));
}

function setArtistLayout(layout){
  if (!ARTIST_LAYOUTS.includes(layout)) return;
  artistLayout = layout;
  try { localStorage.setItem("hl.artistLayout", layout); } catch (_) {}
  paintArtistLayout();
  paintArtistPanes();
}

function paintArtistSongs(){
  const host = document.getElementById("fapSongs");
  if (!host || !artistPageData) return;
  const all = artistPageData.tracks || [];
  const q = artistFilterQuery();
  const songs = q ? all.filter(t => artistMatches(t.title) || artistMatches(t.album)
    || artistMatches(t.artist)) : all;
  host.innerHTML = artistSongsHtml(songs);
  host.querySelectorAll("[data-cat-open]").forEach(b => b.onclick = () =>
    window.open(b.dataset.catOpen, "_blank", "noopener"));
  bindReleaseRows(host);
  const meter = document.getElementById("fap-song-meter");
  if (meter) meter.textContent = `${songs.length} of ${artistPageData.track_total} songs`;
  paintArtistFooter();
}

// The rest of the discography is one tap away, not a wall of cards on first paint.
function paintArtistFooter(){
  const foot = document.getElementById("fap-foot");
  if (!foot || !artistPageData) return;
  const shown = (artistPageData.tracks || []).length;
  const total = Number(artistPageData.track_total || 0);
  foot.innerHTML = `${shown < total
    ? `<button type="button" class="ghost" id="fap-more">Load more songs (${shown} of ${total})</button>`
    : ""}<span id="fap-more-status" role="status"></span>`;
  const more = document.getElementById("fap-more");
  if (more) more.onclick = loadMoreArtistSongs;
}

async function loadMoreArtistSongs(){
  if (!artistPageData || artistPageLoading || artistPageData.tracks.length >= artistPageData.track_total) return;
  const watchId = artistPageWatch;
  const offset = artistPageData.tracks.length;
  artistPageLoading = true;
  const button = document.getElementById("fap-more");
  if (button) { button.disabled = true; button.textContent = "Loading…"; }
  try {
    const page = await loadCatalogue(watchId, offset);
    if (watchId !== artistPageWatch || !artistPageData) return;
    artistPageData.tracks.push(...(page.tracks || []));
    artistPageData.track_total = page.track_total;
    paintArtistSongs();
  } catch (error) {
    const status = document.getElementById("fap-more-status");
    if (status) status.textContent = error.message;
  } finally {
    artistPageLoading = false;
    const current = document.getElementById("fap-more");
    if (current) current.disabled = false;
  }
}

// ---- the artist page's two sections ----------------------------------------
// Albums and Songs are tabs — the same square icon switch the Library uses — and the
// layout switch (cards / covers / rows) applies to both. The row above the pane carries
// the tabs on the left and the layout at its right, so nothing is stacked twice.
const ARTIST_TABS = ["albums", "songs"];
let artistTab = (() => {
  try { const saved = localStorage.getItem("hl.artistTab");
    return ARTIST_TABS.includes(saved) ? saved : "albums"; } catch (_) { return "albums"; }
})();
let artistFilter = "";
let renderedArtistAlbums = [];

function artistFilterQuery(){ return artistFilter.trim().toLowerCase(); }
function artistMatches(text){
  const q = artistFilterQuery();
  return !q || String(text || "").toLowerCase().includes(q);
}
function artistAlbumMeta(album){
  const n = Number(album.track_count || 0);
  return `${n ? `${n} song${n === 1 ? "" : "s"}` : "Album"}${
    album.release_date ? ` · ${escapeHtml(album.release_date)}` : ""}`;
}

function artistAlbumCard(album, index){
  return `<button type="button" class="facard" data-faalbum="${index}"
    title="Open ${escapeHtml(album.title)}">
    ${artBox(album.artwork, album.title, "facover")}
    <span class="fatitle">${escapeHtml(album.title)}</span>
    <span class="fametas">${artistAlbumMeta(album)}</span>
  </button>`;
}

function artistAlbumTile(album, index){
  return `<button type="button" class="covercell" data-faalbum="${index}"
    title="${escapeHtml(album.title)} — open album">
    ${artBox(album.artwork, album.title, "coverart")}
    <span class="covername"><b>${escapeHtml(album.title)}</b></span>
  </button>`;
}

function artistAlbumRow(album, index){
  const letter = (album.title || "?").slice(0, 1).toUpperCase();
  return `<div class="feedrel" data-faalbum="${index}" tabindex="0" role="button"
    aria-label="Open ${escapeHtml(album.title)}">
    <div class="feedcard">
      <div class="feedart">${letter}${album.artwork
        ? `<img src="${escapeHtml(album.artwork)}" alt="" loading="lazy" onerror="this.remove()">` : ""}</div>
      <div class="feedmain">
        <div class="feedtitle">${escapeHtml(album.title)}</div>
        <div class="feedmeta">${artistAlbumMeta(album)}</div>
      </div>
      <div class="feedacts"><span class="fapchev" aria-hidden="true">›</span></div>
    </div>
  </div>`;
}

function artistAlbumsHtml(albums){
  if (!albums.length)
    return `<div class="empty">${artistFilterQuery()
      ? "Nothing here matches that filter."
      : "No albums — every release is a single."}</div>`;
  if (artistLayout === "cards") return `<div class="songgrid">${albums.map(artistAlbumCard).join("")}</div>`;
  if (artistLayout === "grid") return `<div class="covergrid">${albums.map(artistAlbumTile).join("")}</div>`;
  return `<div class="falist">${albums.map(artistAlbumRow).join("")}</div>`;
}

function artistTabsHtml(){
  const icon = (name, title, inner) => `<button type="button" data-fatab="${name}"
    title="${title}" aria-label="${title}" aria-pressed="${artistTab === name}"
    class="${artistTab === name ? "on" : ""}">${inner}</button>`;
  return `<nav class="seg" id="fapTabs" aria-label="Artist section">${
    icon("albums", "Albums", `<svg viewBox="0 0 24 24" width="20" height="20" fill="none" stroke="currentColor" stroke-width="1.7" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true"><circle cx="12" cy="12" r="8.2"/><circle cx="12" cy="12" r="2.2"/></svg>`)
  }${icon("songs", "Songs", `<svg viewBox="0 0 24 24" width="20" height="20" fill="none" stroke="currentColor" stroke-width="1.7" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true"><path d="M9 18V6.5l9-2V16"/><circle cx="6.6" cy="18" r="2.4"/><circle cx="15.6" cy="16" r="2.4"/></svg>`)
  }</nav>`;
}

function paintArtistTabs(){
  const seg = document.getElementById("fapTabs");
  if (!seg) return;
  seg.querySelectorAll("[data-fatab]").forEach(b => {
    const on = b.dataset.fatab === artistTab;
    b.classList.toggle("on", on);
    b.setAttribute("aria-pressed", String(on));
    b.onclick = () => setArtistTab(b.dataset.fatab);
  });
}

function setArtistTab(tab){
  if (!ARTIST_TABS.includes(tab)) return;
  artistTab = tab;
  try { localStorage.setItem("hl.artistTab", tab); } catch (_) {}
  paintArtistTabs();
  paintArtistPanes();
}

// One pane at a time: the inactive one is emptied, so a big discography is not kept in
// the DOM behind the albums (and vice versa).
function paintArtistPanes(){
  const albumsHost = document.getElementById("fapAlbums");
  const songsHost = document.getElementById("fapSongs");
  const foot = document.getElementById("fap-foot");
  const meter = document.getElementById("fap-song-meter");
  if (!albumsHost || !songsHost || !artistPageData) return;
  const q = artistFilterQuery();
  const onAlbums = artistTab === "albums";
  albumsHost.hidden = !onAlbums;
  songsHost.hidden = onAlbums;
  if (foot) foot.hidden = onAlbums;
  if (onAlbums){
    songsHost.innerHTML = "";
    if (foot) foot.innerHTML = "";
    const albums = (artistPageAlbums || []).filter(a =>
      artistMatches(a.title) || artistMatches(a.release_date));
    renderedArtistAlbums = albums;
    albumsHost.innerHTML = artistAlbumsHtml(albums);
    albumsHost.querySelectorAll("[data-faalbum]").forEach(el => {
      el.onclick = () => showArtistAlbum(Number(el.dataset.faalbum));
      if (el.getAttribute("role") === "button")
        el.onkeydown = e => {
          if (e.key === "Enter" || e.key === " "){ e.preventDefault(); el.click(); }
        };
    });
    if (meter) meter.textContent = `${albums.length} album${albums.length === 1 ? "" : "s"}${
      q ? " shown" : ""}`;
    return;
  }
  albumsHost.innerHTML = "";
  paintArtistSongs();
}

// Opening an album is a page, not a fold-out under the grid: the artist head steps
// aside and the same hero + tracklist shape the Repo's album page uses takes over.
// Two quick taps must not paint the older album last.
let artistAlbumSeq = 0;
async function showArtistAlbum(index){
  const seq = ++artistAlbumSeq;
  const album = (renderedArtistAlbums || [])[index];
  const page = document.getElementById("feedartistpage");
  const body = document.getElementById("fapbody");
  const view = document.getElementById("fapalbumview");
  const head = page && page.querySelector(".fahead");
  if (!album || !body || !view) return;
  body.hidden = true;
  if (head) head.hidden = true;
  view.hidden = false;
  view.innerHTML = `<div class="feedtrkmsg">Reading “${escapeHtml(album.title)}”…</div>`;
  scrollTo({top: 0, behavior: "instant"});
  try {
    const tracks = await loadReleaseTracks(album.source_url);
    // A newer album was opened, or the page was closed, while this one was reading.
    if (seq !== artistAlbumSeq || !document.body.contains(view)) return;
    view.innerHTML = artistAlbumPage(album, tracks);
    bindReleaseRows(view);
    view.querySelectorAll("[data-track-url]").forEach(b => b.onclick = () => queueOne(b));
  } catch (e) {
    if (seq !== artistAlbumSeq || !document.body.contains(view)) return;
    view.innerHTML = `<div class="feedtrkmsg">${escapeHtml(e.message)}</div>`;
  }
}

function closeArtistAlbum(){
  artistAlbumSeq++;                                  // a pending read must not repaint
  const page = document.getElementById("feedartistpage");
  const body = document.getElementById("fapbody");
  const view = document.getElementById("fapalbumview");
  const head = page && page.querySelector(".fahead");
  if (view){ view.hidden = true; view.innerHTML = ""; }
  if (body) body.hidden = false;
  if (head) head.hidden = false;
  scrollTo({top: 0, behavior: "instant"});
}

// The one Back in the artist page is also the album page's Back: whichever level
// is on screen is the one it leaves.
function feedArtistBack(){
  const view = document.getElementById("fapalbumview");
  if (view && !view.hidden) closeArtistAlbum();
  else closeArtistPage();
}

function artistAlbumPage(album, tracks){
  const artist = (artistPageData && artistPageData.artist) || "";
  const total = tracks.reduce((n, t) => n + Number(t.duration || 0), 0);
  const when = album.release_date ? ` · ${escapeHtml(album.release_date)}` : "";
  const rows = tracks.map(t => `
    <div class="songrow">
      <div class="thumb">${album.artwork ? artImg(album.artwork, "", true, album.title) : ""}</div>
      <div class="st"><span class="sttext"><b>${escapeHtml(t.title)}</b></span></div>
      <div class="sartist">${escapeHtml(t.artist || artist)}</div>
      <div class="sdur">${t.duration ? fmtClock(t.duration) : ""}</div>
      <span class="send">
        <button type="button" class="sdl-grab" data-track-url="${escapeHtml(t.url)}"
          data-track-title="${escapeHtml(t.title)}"
          aria-label="Download ${escapeHtml(t.title)}"
          title="Download ${escapeHtml(t.title)}">${DOWNLOAD_ICON}</button>
      </span>
    </div>`).join("");
  return `<div class="hero">
      ${artBox(album.artwork, album.title, "hart")}
      <div class="hmeta">
        <div class="kicker">Album</div>
        <h3>${escapeHtml(album.title)}</h3>
        <div class="hsub">${escapeHtml(artist)} · ${tracks.length} song${tracks.length === 1 ? "" : "s"}${
          total ? ` · ${fmtLong(total)}` : ""}${when}</div>
        <div class="hacts">
          <button type="button" class="primary" data-cat-url="${escapeHtml(album.source_url)}"
            data-cat-title="${escapeHtml(album.title)}">${SVG.download(16)} Download album</button>
        </div>
      </div>
    </div>
    <div class="songtable fapalbumtable">${rows}</div>`;
}

let artistPageData = null;
let artistPageLoading = false;

async function loadCatalogue(watchId, offset){
  const pageStart = Number(offset || 0);
  const key = `${Number(watchId)}:${pageStart}`;
  const cached = catalogueCache.get(key);
  if (cached) return cached;
  const response = await fetch(`/api/catalogue?watch=${watchId}&offset=${pageStart}`, {cache: "no-store"});
  const data = await response.json().catch(() => ({}));
  if (!response.ok) throw new Error(data.error || "Could not read that catalogue");
  catalogueCache.set(key, data);
  return data;
}

function closeArtistPage(){
  const page = document.getElementById("feedartistpage");
  const list = document.getElementById("feedlist");
  const tools = document.querySelector("#repopane-releases .feedtools");
  if (page) page.hidden = true;
  if (list) list.hidden = false;
  if (tools) tools.hidden = false;
  artistPageWatch = 0;
  forgetFeedArtistReturn();
}

// ---- the same artist in the Repo, and the way back ----
// "In Repo" opens the downloaded-artist page; Back there (and on any page below it)
// returns to this Feed page, because that is where he was before.
let feedArtistReturn = null;    // {watchId, name, art} while the Repo was entered from here
let artistPageArt = "";
function forgetFeedArtistReturn(){ feedArtistReturn = null; }

// Which downloaded artist this name belongs to, in one set of rows. `rows` exists because
// "downloaded" is a property of the repository, not of whichever Library scope is on screen:
// a song the player is playing lives in the shared pool even when the tab shows saved songs.
function repoArtistNamed(name, rows){
  const key = String(name || "").trim().toLowerCase();
  if (!key) return null;
  const found = artistsOf(rows || scopedTracks()).find(a =>
    String(a.name || "").trim().toLowerCase() === key);
  return found ? found.name : null;
}

function openArtistInRepo(name){
  // "In Repo" means the repository, so the shared pool decides — not the saved subset.
  const repoName = repoArtistNamed(name, typeof libAll === "undefined" ? null : libAll) ||
                   repoArtistNamed(name);
  if (!repoName){
    showToast(`Nothing downloaded from ${name} yet`);
    return;
  }
  feedArtistReturn = {watchId: artistPageWatch, name, art: artistPageArt};
  // The page reads whichever scope is showing; this one only exists in the shared pool.
  setLibraryScope("shared");
  showLibraryTab();
  openArtistByName(repoName);
}

// "In Repo" opens the downloaded-artist page; Back there (and on any page below it)
// returns to this release page — which is where he was before, in the Repository.
function returnToFeedArtist(state){
  setLibraryScope("shared");
  showLibraryTab();
  setRepoTab("releases");
  if (!state || !state.watchId){ renderLibrary(); return; }
  openArtistPage(state.watchId, state.name, state.art);
}

async function openArtistPage(watchId, name, art){
  const page = document.getElementById("feedartistpage");
  const list = document.getElementById("feedlist");
  const tools = document.querySelector("#repopane-releases .feedtools");
  if (!page || !list || !watchId) return;
  artistPageWatch = Number(watchId);
  artistPageArt = art || "";
  const watch = subscriptions.find(f => Number(f.id) === artistPageWatch) || null;
  list.hidden = true;
  if (tools) tools.hidden = true;
  page.hidden = false;
  page.innerHTML = `<div class="fanav">
      <button type="button" class="backbtn" id="fapback">‹ Back</button>
      <button type="button" class="ghost small" id="faprepo"${repoArtistNamed(name) ? "" : " disabled"}
        title="${repoArtistNamed(name) ? `Open ${escapeHtml(name)} in the Repo` : "Nothing downloaded from this artist yet"}">In Repo</button>
    </div>
    <div class="fahead">
      <div class="faart">${(name || "?").slice(0, 1).toUpperCase()}${art
        ? `<img src="${escapeHtml(art)}" alt="" loading="lazy" onerror="this.remove()">` : ""}</div>
      <div class="faid"><div class="faname">${escapeHtml(name)}</div>
        <div class="fameta" id="fapmeta">Reading the catalogue…</div></div>
      <label class="fasearch">
        <input type="text" id="fapsearch" placeholder="Filter albums and songs"
          aria-label="Filter this artist">
      </label>
    </div>
    <div id="fapbody"><div class="empty">Reading the catalogue…</div></div>
    <div id="fapalbumview" hidden></div>`;
  document.getElementById("fapback").onclick = feedArtistBack;
  document.getElementById("faprepo").onclick = () => openArtistInRepo(name);
  const meta = document.getElementById("fapmeta");
  const body = document.getElementById("fapbody");
  try {
    const data = await loadCatalogue(artistPageWatch);
    if (artistPageWatch !== Number(watchId)) return;      // a different artist was opened
    artistPageData = data;
    const releases = data.releases || [];
    // Every album the catalogue knows, not the four newest.
    artistPageAlbums = releases.filter(albumLike);
    // Songs come from the stored catalogue. A payload without `tracks` (an
    // older server) still works: the single-track releases are the songs.
    if (!Array.isArray(data.tracks)) data.tracks = releases.filter(r => !albumLike(r));
    if (typeof data.track_total !== "number") data.track_total = data.tracks.length;
    meta.textContent = `${artistPageAlbums.length} album${artistPageAlbums.length === 1 ? "" : "s"} · ${data.track_total} song${
      data.track_total === 1 ? "" : "s"} · ${watch ? (watch.enabled ? "subscribed" : "paused") : ""}`.trim();
    artistFilter = "";
    body.innerHTML = `
      <div class="fatop">
        ${artistTabsHtml()}
        ${artistLayoutSeg()}
      </div>
      <div class="fameta" id="fap-song-meter"></div>
      <div id="fapAlbums"></div>
      <div id="fapSongs" hidden></div>
      <div class="fafoot" id="fap-foot" hidden></div>`;
    paintArtistTabs();
    paintArtistLayout();
    paintArtistPanes();
    const filterInput = document.getElementById("fapsearch");
    if (filterInput){
      filterInput.value = "";
      filterInput.oninput = () => { artistFilter = filterInput.value; paintArtistPanes(); };
    }
  } catch (e) {
    meta.textContent = "Could not read the catalogue";
    body.innerHTML = `<div class="empty">${escapeHtml(e.message)}</div>`;
  }
}

function renderFeed(){
  // Only unhandled releases from the selected provider: a queued or ignored one
  // leaves the feed for good. "My" narrows that to the subscriptions this member made.
  paintFeedSwitch();
  paintFeedMine();
  const mine = new Set(subscriptions.filter(f => f.mine).map(f => Number(f.id)));
  const groups = feedGroups(feedItems.filter(i => i.decision === "new" && i.provider === feedProvider
    && (!feedMine || mine.has(Number(i.watch_id)))));
  const where = feedProvider === "apple" ? "Apple Music" : "SoundCloud";
  $("#feedlist").innerHTML = groups.length ? groups.map(feedGroupRow).join("")
    : `<div class="empty">No new ${where} releases${feedMine ? " from the artists you subscribe to" : ""}. New uploads show up here.</div>`;
  $("#feedlist").querySelectorAll(".feedghead").forEach(b => {
    const toggle = () => toggleGroup(b);
    b.onclick = event => {
      if (event.target.closest("[data-follow-url],[data-unsub]")) return;
      // The portrait and the name are both the door to the artist's page; the rest
      // of the row still folds the group open and shut.
      if (event.target.closest(".feedgart,.feedgname")){
        const group = groups.find(g => g.name === b.dataset.artist);
        if (group && group.watch && group.watch.id)
          openArtistPage(group.watch.id, group.name, group.art);
        return;
      }
      toggle();
    };
    b.onkeydown = event => {
      // The fold is a real button inside this role="button" head. Its own Enter/Space
      // already produces a click that bubbles up to the head's onclick, so handling the
      // key here as well would toggle twice and leave the group exactly as it was.
      if (event.target.closest(".feedgfold")) return;
      if (event.key === "Enter" || event.key === " "){ event.preventDefault(); toggle(); }
    };
  });
  // The peek only exists while collapsed, so tapping it simply opens the group.
  $("#feedlist").querySelectorAll("[data-peek]").forEach(p => p.onclick = () => {
    toggleGroup(p.parentElement.querySelector(".feedghead"));
  });
  $("#feedlist").querySelectorAll("[data-follow-url]").forEach(b => b.onclick = async event => {
    event.stopPropagation();
    const name = b.dataset.followName || "that artist";
    b.disabled = true; b.classList.add("busy");
    try {
      await api("/api/subscriptions", {url: b.dataset.followUrl});
      showToast(`Subscribed to ${name}`);
      await loadSubscriptions(); await loadFeed();
    } catch (e) {
      showToast(e.message || `Could not subscribe to ${name}`);
      b.disabled = false; b.classList.remove("busy");
    }
  });
  // The red minus: out of your own list only. The subscription stays, so the
  // artist is still tracked and still listed for everybody else.
  $("#feedlist").querySelectorAll("[data-unsub]").forEach(b => b.onclick = async event => {
    event.stopPropagation();
    const row = b.closest(".feedgroup");
    const name = ((row && row.querySelector(".feedgname")) || {}).textContent || "that artist";
    b.disabled = true; b.classList.add("busy");
    try {
      await api(`/api/subscriptions/${b.dataset.unsub}/unsubscribe`, {});
      showToast(`Unsubscribed from ${name} — they stay on the feed for everyone else`);
      await loadSubscriptions(); await loadFeed();
    } catch (e) {
      showToast(e.message || `Could not unsubscribe from ${name}`);
      b.disabled = false; b.classList.remove("busy");
    }
  });
  $("#feedlist").querySelectorAll("[data-queue]").forEach(b => b.onclick = () => feedAct("queue", b.dataset.queue));
  // the URL stays out of the markup: it is external data, looked up by id
  $("#feedlist").querySelectorAll("[data-open]").forEach(b => b.onclick = () => {
    const item = feedItems.find(i => String(i.id) === b.dataset.open);
    if (item) window.open(item.source_url, "_blank", "noopener");
  });
  bindReleaseRows($("#feedlist"));
}

async function feedAct(kind, id){
  // Errors here used to vanish into an unhandled rejection, leaving a card that
  // looked untouched after the click actually worked server-side.
  const err = $("#feederr");
  const item = feedItems.find(i => String(i.id) === String(id));
  const title = item ? item.title : "";
  const button = document.querySelector(`#feedlist [data-${kind}="${id}"]`);
  const label = button ? button.textContent : "";
  // The card vanishes once it is queued, so the click has to say what happened
  // before it goes: the button turns into "Adding…" and a toast confirms it.
  // The download button is an icon now, so "Adding…" would erase it: swap a busy
  // class in instead and leave the label alone.
  const iconButton = !!(button && button.classList.contains("feeddl"));
  if (button){
    button.disabled = true;
    if (iconButton) button.classList.add("busy");
    else button.textContent = "Adding…";
  }
  err.textContent = "";
  try {
    const result = await api(`/api/feed/${id}/${kind}`, {});
    await loadFeed();
    // Downloading from here also saves the song, so say both halves at once.
    showToast(title
      ? `Queued — ${title} · joins your library when it finishes`
      : "Added to the download queue · joins your library when it finishes");
  } catch (e) {
    window.__feedErr = String((e && e.message) || e);
    err.textContent = window.__feedErr;
    if (button){
      button.disabled = false;
      if (iconButton) button.classList.remove("busy");
      else button.textContent = label;
    }
  }
  refresh();
}

let feedSeq = 0;

function setRepoTab(name){
  repoTab = REPO_TABS.includes(name) ? name : "browse";
  try { localStorage.setItem("hl.repoTab", repoTab); } catch (_) {}
  paintRepoTab();
  if (activeRepoTab() !== "browse") loadSubscriptions();
  if (activeRepoTab() === "releases") loadFeed();
}

// One place that says which section is on screen. The personal Library is a single
// list, so the tab row and the extra panes only exist in the shared Repository.
function paintRepoTab(){
  const shared = libraryScope === "shared";
  const tab = activeRepoTab();
  const row = $("#repotabs");
  if (row) row.hidden = !shared;
  for (const key of REPO_TABS) {
    const button = $("#rtab-" + key);
    if (button) {
      button.classList.toggle("on", key === tab);
      button.setAttribute("aria-selected", String(key === tab));
    }
    const pane = $("#repopane-" + key);
    if (pane) pane.hidden = key !== tab;
  }
  const title = $("#libtitle");
  if (title) title.textContent = !shared ? "Library"
    : tab === "releases" ? "Releases" : tab === "subscribed" ? "Subscribed" : "Repository";
  const count = $("#libcount");
  if (count) count.hidden = tab !== "browse";
  // The import target is the pool, so it belongs to Browse, not to the other sections.
  const zone = $("#dropzone");
  if (zone) zone.hidden = tab !== "browse";
}
