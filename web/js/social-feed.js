/* Activity: what people did — one collapsed day card per member, the boost pin, and
   the member profile dialog those cards open.

   `JS-031`. A plain classic script, loaded before the inline one: no build step, and every
   name below stays global exactly as it was in index.html. Nothing here paints at load — the
   list is painted by loadActivity() when the Activity view asks for it — and nothing looks up
   markup or reads a `const` from the inline script, which does not exist yet. `esc`, `ago`,
   `profileSymbol`, `api` and `showToast` stay where they were and resolve at call time.
*/
let activityExpiryTimer = null;
const openDays = new Set();   // collapsed day cards stay as you left them across polls


function ago(ts){
  const s = Math.max(0, Date.now() / 1000 - Number(ts || 0));
  if (s < 60) return "just now";
  if (s < 3600) return Math.floor(s / 60) + "m ago";
  if (s < 86400) return Math.floor(s / 3600) + "h ago";
  if (s < 604800) return Math.floor(s / 86400) + "d ago";
  return new Date(Number(ts) * 1000).toLocaleDateString();
}

function actWho(item){
  // Every interpolated value is tag/name data, so it is escaped: esc() is defined
  // above for the library cards and gives the same guarantee here.
  const name = esc(item.member || "Someone");
  const icon = profileSymbol(item.member_icon || "music");
  return item.member_id
    ? `<button class="actwho" data-member="${esc(item.member_id)}" type="button">${icon} ${name}</button>`
    : `<span class="actwho">${name}</span>`;
}

function actLine(item){
  const who = actWho(item);
  const track = item.track && item.track.title
    ? `<b>${esc(item.track.title)}</b>${item.track.artist_name ? " — " + esc(item.track.artist_name) : ""}` : "";
  switch (item.kind) {
    case "join": return `${who} joined`;
    case "name": return `${who} is now known as <b>${esc(item.detail || "…")}</b>`;
    case "add": return `${who} added ${track}`;
    case "playlist_new": return `${who} made the playlist <b>${esc(item.playlist || item.detail)}</b>`;
    case "playlist_edit": return `${who} edited the playlist <b>${esc(item.playlist || item.detail)}</b>`;
    case "playlist_delete": return `${who} deleted the playlist <b>${esc(item.detail)}</b>`;
    case "boost": return `${who} boosted ${track} to the top of the Feed`;
    // Every kind above is handled; this only catches an event added later, and says
    // so instead of a shrug.
    default: return `${who} was active`;
  }
}

// ---- Activity: one collapsed card per member per day ------------------------
// A day card carries a count per kind, so an evening of listening is one line at a
// glance; the entries themselves are inside it, five at a time.
const ACT_REVEAL = 5;

function actDayLabel(day){
  const at = new Date(`${day}T00:00:00`);
  if (Number.isNaN(at.getTime())) return day;
  const today = new Date(); today.setHours(0, 0, 0, 0);
  const nights = Math.round((today - at) / 86400000);
  if (nights === 0) return "Today";
  if (nights === 1) return "Yesterday";
  return at.toLocaleDateString(undefined, {weekday: "short", day: "numeric", month: "short"});
}

function actDaySummary(card){
  const c = card.counts || {};
  const parts = [];
  if (c.listen) parts.push(`${c.listen} song${c.listen === 1 ? "" : "s"} played`);
  if (c.add) parts.push(`${c.add} added`);
  if (c.playlist) parts.push(`${c.playlist} playlist${c.playlist === 1 ? "" : "s"}`);
  // Joins and renames used to be lumped into "other actions", which told nobody
  // anything. Name them.
  const other = (card.groups || {}).other || [];
  const joins = other.filter(o => o.kind === "join").length;
  const renames = other.filter(o => o.kind === "name").length;
  if (joins) parts.push("joined");
  if (renames) parts.push(`${renames} rename${renames === 1 ? "" : "s"}`);
  return parts.join(" · ");
}

function actGroup(label, rows){
  if (!rows || !rows.length) return "";
  const head = rows.slice(0, ACT_REVEAL).join("");
  const rest = rows.slice(ACT_REVEAL);
  return `<div class="actgroup">
    <div class="actghead">${esc(label)}</div>
    <ul class="actitems">${head}</ul>
    ${rest.length ? `<div class="actrest" hidden><ul class="actitems">${rest.join("")}</ul></div>
      <button class="actmore" type="button">Show ${rest.length} more</button>` : ""}
  </div>`;
}

function actSongLine(t){
  return `<li><b>${esc(t.title || "Untitled")}</b>${t.artist_name ? ` <span>— ${esc(t.artist_name)}</span>` : ""}</li>`;
}

function actDayCard(item){
  const g = item.groups || {};
  const counts = item.counts || {};
  const playlists = (g.playlist || []).map(p => {
    const verb = p.kind === "playlist_new" ? "made" : p.kind === "playlist_delete" ? "deleted" : "edited";
    return `<li>${verb} <b>${esc(p.title || "a playlist")}</b></li>`;
  });
  const others = (g.other || []).map(o => {
    if (o.kind === "join") return `<li>joined the library</li>`;
    if (o.kind === "name") return `<li>is now known as <b>${esc(o.detail || "…")}</b></li>`;
    return "";   // never print a raw internal kind
  }).filter(Boolean);
  const body = [
    actGroup(`${counts.listen || 0} played`, (g.listen || []).map(actSongLine)),
    actGroup(`${counts.add || 0} added`, (g.add || []).map(actSongLine)),
    actGroup(`${counts.playlist || 0} playlist${counts.playlist === 1 ? "" : "s"}`, playlists),
    actGroup(`Account`, others),
  ].join("");
  return `<div class="actrow actday" data-day="${esc(item.day || "")}" data-key="${esc(item.member_id || "")}|${esc(item.day || "")}">
    <div class="acttop" role="button" tabindex="0" aria-expanded="false" aria-label="Expand activities">
      ${actWho(item)}
      <span class="actsum">${esc(actDaySummary(item))}</span>
      <span class="actwhen">${esc(actDayLabel(item.day))}</span>
      <span class="actchev" aria-hidden="true">▾</span>
    </div>
    <div class="actbody" hidden>${body}</div>
  </div>`;
}

function actCard(item){
  if (item.kind === "boost") {
    const seconds = Math.max(0, Number(item.expires_at || 0) - Date.now()/1000);
    const left = seconds >= 3600 ? `${Math.ceil(seconds/3600)}h left` : `${Math.ceil(seconds/60)}m left`;
    const track = item.track || {};
    return `<div class="actrow actboost"><div class="actline">⚡ ${actWho(item)} boosted <b>${esc(track.title || 'a song')}</b>${track.artist_name ? ` — ${esc(track.artist_name)}` : ''}</div><div class="acttime">Pinned at the top · ${left}</div></div>`;
  }
  if (item.kind === "day") return actDayCard(item);
  return `<div class="actrow"><div class="actline">${actLine(item)}</div>
    <div class="acttime">${ago(item.ts)}</div></div>`;
}

// One place binds a rendered activity list: member links, card toggles, show-more.
function bindActivityCards(list){
  list.querySelectorAll("[data-member]").forEach(b =>
    b.onclick = () => openMember(b.dataset.member));
  // A day card stays collapsed unless you opened it; the poll re-renders the list.
  list.querySelectorAll(".actday").forEach(card => {
    if (!openDays.has(card.dataset.key)) return;
    const top = card.querySelector(".acttop"), body = card.querySelector(".actbody");
    if (top && body) { body.hidden = false; top.setAttribute("aria-expanded", "true"); card.classList.add("open"); }
  });
  list.querySelectorAll(".acttop").forEach(top => {
    const card = top.closest(".actday");
    const flip = () => {
      const body = top.nextElementSibling;
      if (!body) return;
      const open = body.hidden;
      body.hidden = !open;
      top.setAttribute("aria-expanded", String(open));
      card?.classList.toggle("open", open);
      if (card) open ? openDays.add(card.dataset.key) : openDays.delete(card.dataset.key);
    };
    top.onclick = event => { if (event.target.closest(".actwho")) return; flip(); };
    top.onkeydown = event => {
      if (event.key === "Enter" || event.key === " ") { event.preventDefault(); flip(); }
    };
  });
  list.querySelectorAll(".actmore").forEach(button => button.onclick = () => {
    const rest = button.previousElementSibling;
    if (rest) rest.hidden = false;
    button.remove();
  });}

// ---- Feed sections: Activity | Popular --------------------------------------
// One play log, two readings: Activity is the log itself, Popular is the chart built
// from it across every member. Both selectors are the Repository's own row, so a feed
// section and a repo section are chosen the same way. The pane in use is remembered.
const FEED_TABS = ["activity", "popular"];
const CHART_PERIODS = ["day", "week", "month", "year", "all"];
let feedTab = "activity";
let popularPeriod = "day";
let popularAsked = 0;              // last request wins; an older answer is dropped

function feedStored(key, fallback, allowed){
  try {
    const value = localStorage.getItem(key);
    return allowed.includes(value) ? value : fallback;
  } catch (_) { return fallback; }
}

function paintFeedTab(){
  for (const key of FEED_TABS){
    const button = document.getElementById("ftab-" + key);
    if (button){
      button.classList.toggle("on", key === feedTab);
      button.setAttribute("aria-selected", String(key === feedTab));
    }
    const pane = document.getElementById("feedpane-" + key);
    if (pane) pane.hidden = key !== feedTab;
  }
}

function paintChartPeriod(){
  const row = document.getElementById("feedperiods");
  if (!row) return;
  for (const button of row.querySelectorAll("[data-period]")){
    const on = button.dataset.period === popularPeriod;
    button.classList.toggle("on", on);
    button.setAttribute("aria-pressed", String(on));
  }
}

// What the Feed view asks for when it comes on screen: the section you left it on.
function openFeedTab(){
  feedTab = feedStored("hl.feedTab", "activity", FEED_TABS);
  popularPeriod = feedStored("hl.chartPeriod", "day", CHART_PERIODS);
  paintFeedTab();
  paintChartPeriod();
  if (feedTab === "popular") loadPopular(); else loadActivity();
}

function setFeedTab(key){
  if (!FEED_TABS.includes(key) || key === feedTab) return;
  feedTab = key;
  try { localStorage.setItem("hl.feedTab", feedTab); } catch (_) {}
  paintFeedTab();
  if (feedTab === "popular") loadPopular(); else loadActivity();
}

function setPopularPeriod(period){
  if (!CHART_PERIODS.includes(period) || period === popularPeriod) return;
  popularPeriod = period;
  try { localStorage.setItem("hl.chartPeriod", popularPeriod); } catch (_) {}
  paintChartPeriod();
  loadPopular();
}

// The cover is the library's own row when it has one, which is what makes it the stored
// digest (`/api/art/<hash>`) rather than the provider's CDN copy.
function popularArt(row){
  const rows = (typeof libAll !== "undefined" && libAll) || [];
  const local = rows.find(t => t.id === Number(row.id));
  if (local && typeof coverArt === "function"){
    const art = coverArt(local);
    if (art) return art;
  }
  return row.artwork || "";
}

function popularRow(row, index){
  const art = popularArt(row);
  const plays = Number(row.plays) || 0;
  const people = Number(row.listeners) || 0;
  const artist = row.artist_name || "—";
  // Only say how many people when it took more than one: "1 person" on every row of a
  // one-member library is noise.
  const sub = people > 1 ? `${artist} · ${people} people` : artist;
  return `<button type="button" class="mprow" data-st-track="${Number(row.id)}"
      aria-label="Play ${esc(row.title || "untitled")} by ${esc(artist)}">
    <b class="strank">${index + 1}</b>
    ${art ? `<img class="mpart" src="${esc(art)}" alt="" loading="lazy">`
          : `<span class="mpart" aria-hidden="true"></span>`}
    <span class="mptext"><b>${esc(row.title || "…")}</b><small>${esc(sub)}</small></span>
    <span class="mpmeta">${plays}×</span></button>`;
}

async function loadPopular(){
  const err = document.getElementById("populare");
  const list = document.getElementById("popularlist");
  if (!err || !list) return;
  const asked = ++popularAsked;
  try {
    const r = await fetch(`/api/popular?period=${encodeURIComponent(popularPeriod)}`,
      {cache: "no-store"});
    const data = await r.json();
    if (!r.ok) throw new Error(data.error || `HTTP ${r.status}`);
    if (asked !== popularAsked) return;
    err.textContent = "";
    const items = data.items || [];
    // Each window is named the way it was asked for. All time has no window to name.
    const window = {day: "today", week: "this week", month: "this month",
                    year: "this year", all: ""}[popularPeriod];
    list.innerHTML = items.length
      ? `<div class="mplist">${items.map(popularRow).join("")}</div>`
      : `<p class="mpempty">Nothing played${window ? " " + window : ""} yet.</p>`;
  } catch (e) {
    if (asked === popularAsked) err.textContent = e.message;
  }
}

async function loadActivity(){
  const err = $("#acterr");
  try {
    const r = await fetch("/api/activity?limit=80", {cache: "no-store"});
    const data = await r.json();
    if (!r.ok) throw new Error(data.error || `HTTP ${r.status}`);
    err.textContent = "";
    // actCard escapes every database-derived name/title before returning this HTML.
    $("#activitylist").innerHTML = (data.items || []).length
      ? data.items.map(actCard).join("")
      : `<div class="empty">Nothing yet — plays, additions and playlist edits land here.</div>`;
    clearTimeout(activityExpiryTimer);
    const nearestExpiry = (data.items || []).filter(i => i.kind === 'boost').reduce((min, i) => Math.min(min, Number(i.expires_at) || min), Infinity);
    if (Number.isFinite(nearestExpiry)) activityExpiryTimer = setTimeout(() => loadActivity(), Math.max(50, nearestExpiry*1000-Date.now()+100));
    const list = $("#activitylist");
    bindActivityCards(list);
    bindActivityCards(list);
  } catch (e) { err.textContent = e.message; }
}

async function boostTrack(trackId){
  try {
    const result = await api('/api/boost', {track_id:Number(trackId)});
    await loadActivity();
    const status = result.boost?.swapped ? 'Boost switched; its original 24-hour expiry stays.'
      : result.boost?.already_active ? 'That song already has your active boost; its expiry is unchanged.'
      : 'Song boosted to the top of the Feed for 24 hours.';
    showToast(status);
  } catch(e) { showToast(e.message); }
}

// ---- a member's profile ----------------------------------------------------------
// A page, not a dialog. Opened from any name in Activity, it takes the column instead of
// floating a box over the list it was opened from, and it scrolls with the document: on a
// phone that is the difference between a profile you read and a box inside a box.
//
// The numbers are rolling windows, not calendar ones: "last 7 days" is what the question
// means when somebody asks what they have been playing. The yearly top-100 is the one
// calendar window, because a year is a year.
const STATS_PERIODS = [["week", "Week"], ["month", "Month"], ["year", "Year"], ["all", "All"]];
const MEMBER_SECTIONS = [
  ["history", "History", "Nothing played yet."],
  ["added", "Added", "Nothing added to the library yet."],
  ["playlists", "Playlists", "No playlists."],
  ["votes", "Votes", "No votes yet."],
  ["saved", "Saved", "Nothing saved."],
];
const MEMBER_LIMIT = 40;              // one page of rows per section, as the server sends it
const memberPage = {
  id: "", name: "", icon: "music", role: "", counts: {}, data: null,
  section: "history", period: "week", stats: null, shown: 20,
};

const memberEl = id => document.getElementById(id);
function memberOpen(){
  const page = memberEl("memberpage");
  return !!page && !page.hidden;
}
// The history module in index.html records the open profile by id, so Back and Forward
// reopen the right person. It asks rather than reads, because this file owns the state.
window.memberNavState = () => (memberOpen() && memberPage.id ? String(memberPage.id) : null);

function statsHours(seconds){
  const hours = Number(seconds || 0) / 3600;
  if (hours >= 10) return Math.round(hours) + "h";
  if (hours >= 1) return hours.toFixed(1) + "h";
  const minutes = Math.round(Number(seconds || 0) / 60);
  return (minutes || 0) + "m";
}

function statsList(title, rows, limit, render){
  if (!rows.length) return "";
  const shown = rows.slice(0, limit);
  const more = rows.length > limit
    ? `<button type="button" class="stmore" onclick="statsShowMore()">Show all ${rows.length}</button>` : "";
  return `<h5>${title}</h5><div class="strows">${shown.map(render).join("")}</div>${more}`;
}

function statsRankRow(rank, title, sub, plays){
  return `<div class="strow"><b class="strank">${rank}</b>
    <span class="stname"><b>${esc(title)}</b><small>${esc(sub)}</small></span>
    <em>${Number(plays)}×</em></div>`;
}

function statsSongRow(row, index){
  return `<div class="strow stsong" role="button" tabindex="0" data-st-track="${Number(row.id)}"
      aria-label="Play ${esc(row.title)} by ${esc(row.artist_name || "unknown artist")}">
    <b class="strank">${index + 1}</b>
    <span class="stname"><b>${esc(row.title)}</b><small>${esc(row.artist_name || "—")}</small></span>
    <em>${Number(row.plays)}×</em></div>`;
}

function statsBody(){
  const s = memberPage.stats;
  if (!s) return `<p class="stempty">Listening numbers unavailable.</p>`;
  const periods = STATS_PERIODS.map(([key, label]) =>
    `<button type="button" class="stp" data-stats-period="${key}" aria-pressed="${memberPage.period === key}"
      onclick="setStatsPeriod('${key}')">${label}</button>`).join("");
  const head = `<div class="sthead"><h4>Listening</h4>
    <div class="stperiods" role="group" aria-label="Listening period">${periods}</div></div>`;
  if (s.empty) {
    return head + `<p class="stempty">No plays recorded in this period yet.</p>`;
  }
  const t = s.totals || {};
  const tiles = `<div class="sttiles">
    <div class="sttile"><b>${Number(t.plays || 0)}</b><small>plays</small></div>
    <div class="sttile"><b>${statsHours(t.seconds)}</b><small>listened</small></div>
    <div class="sttile"><b>${Number(t.tracks || 0)}</b><small>songs</small></div>
    <div class="sttile"><b>${Number(t.artists || 0)}</b><small>artists</small></div>
    <div class="sttile"><b>${Number((s.streak || {}).current || 0)}</b><small>day streak</small></div>
  </div>`;
  const fave = s.favourite_artist
    ? `<p class="stlead">Most played: <b>${esc(s.favourite_artist.artist_name)}</b>
        — ${Number(s.favourite_artist.plays)} play${s.favourite_artist.plays === 1 ? "" : "s"}</p>` : "";
  const bars = (s.day_histogram || []).length ? (() => {
    const peak = Math.max(1, ...s.day_histogram.map(d => Number(d.plays)));
    return `<h5>Last 30 days</h5><div class="stbars" aria-hidden="true">${s.day_histogram.map(d =>
      `<i style="height:${Math.max(2, Math.round(Number(d.plays) / peak * 100))}%"
         title="${d.day}: ${Number(d.plays)} play${Number(d.plays) === 1 ? "" : "s"}"></i>`).join("")}</div>`;
  })() : "";
  const extras = [];
  if (s.streak && s.streak.longest) extras.push(`Longest streak ${Number(s.streak.longest)} day${s.streak.longest === 1 ? "" : "s"}`);
  if (s.busiest_day) extras.push(`Biggest day ${esc(s.busiest_day.day)} · ${Number(s.busiest_day.plays)} plays`);
  if ((s.new_artists || []).length) extras.push(`${s.new_artists.length} new artist${s.new_artists.length === 1 ? "" : "s"}`);
  const extraLine = extras.length ? `<p class="stextras">${extras.join(" · ")}</p>` : "";
  return head + fave + tiles + bars + extraLine +
    statsList("Top songs", s.top_tracks || [], memberPage.shown, statsSongRow) +
    statsList("Top artists", s.top_artists || [], 20,
      (r, i) => statsRankRow(i + 1, r.artist_name, `${Number(r.tracks)} song${Number(r.tracks) === 1 ? "" : "s"}`, r.plays)) +
    statsList("Top albums", s.top_albums || [], 20,
      (r, i) => statsRankRow(i + 1, r.album, r.artist_name || "—", r.plays));
}

function statsShowMore(){
  memberPage.shown = 100;
  repaintStats();
}

function repaintStats(){
  const host = document.querySelector("#mpbody .mpstats");
  if (host) host.innerHTML = statsBody();
}

async function setStatsPeriod(period){
  if (!memberPage.id || period === memberPage.period) return;
  memberPage.period = period;
  memberPage.shown = 20;
  const host = document.querySelector("#mpbody .mpstats");
  if (host) host.classList.add("busy");
  document.querySelectorAll("#mpbody [data-stats-period]").forEach(b =>
    b.setAttribute("aria-pressed", String(b.dataset.statsPeriod === period)));
  try {
    const r = await fetch(`/api/stats/${encodeURIComponent(memberPage.id)}?period=${encodeURIComponent(period)}`,
      {cache: "no-store"});
    const data = await r.json();
    if (!r.ok) throw new Error(data.error || `HTTP ${r.status}`);
    if (!memberPage.id || memberPage.period !== period) return;   // another name, or another period since
    memberPage.stats = data;
    repaintStats();
  } catch (e) { showToast(e.message); }
  finally { if (host) host.classList.remove("busy"); }
}

// A song row is a play target, like every other row in the app.
document.addEventListener("click", event => {
  const row = event.target.closest?.("#mpbody [data-st-track], #popularlist [data-st-track]");
  if (!row) return;
  playTrackNow(Number(row.dataset.stTrack));
});
document.addEventListener("keydown", event => {
  if (event.key !== "Enter" && event.key !== " ") return;
  const row = event.target.closest?.("#mpbody [data-st-track], #popularlist [data-st-track]");
  if (!row) return;
  event.preventDefault();
  playTrackNow(Number(row.dataset.stTrack));
});

// ---- the page --------------------------------------------------------------------
// The cover comes from the client's own library: the profile payload carries the song and
// the artist, so the row reads as a song without a second request per line.
function memberTrackArt(id){
  const rows = (typeof libAll !== "undefined" && libAll) || [];
  const row = rows.find(t => t.id === Number(id));
  return row && row.artwork ? row.artwork : "";
}

function memberTrackRow(t, meta){
  const art = memberTrackArt(t.id);
  const title = t.title || "…";
  const artist = t.artist || t.artist_name || "—";
  return `<button type="button" class="mprow" data-st-track="${Number(t.id)}"
      aria-label="Play ${esc(title)} by ${esc(artist)}">
    ${art ? `<img class="mpart" src="${esc(art)}" alt="" loading="lazy">`
          : `<span class="mpart" aria-hidden="true"></span>`}
    <span class="mptext"><b>${esc(title)}</b><small>${esc(artist)}</small></span>
    <span class="mpmeta">${esc(meta || "")}</span></button>`;
}

function memberPlaylistRow(p){
  const n = Number(p.tracks) || 0;
  return `<button type="button" class="mprow" onclick="openPlaylistFromMember(${Number(p.id)})"
      aria-label="Open the playlist ${esc(p.title)}">
    <span class="mptext"><b>${esc(p.title || "…")}</b><small>${n} track${n === 1 ? "" : "s"}</small></span>
    <span class="mpmeta" aria-hidden="true">›</span></button>`;
}

function memberVoteRow(v){
  const mark = v.value > 0 ? "▲" : v.value < 0 ? "▼" : "○";
  return `<div class="mprow mpstill">
    <span class="mpvote" aria-hidden="true">${mark}</span>
    <span class="mptext"><b>${esc(v.label || "—")}</b><small>${esc(v.kind || "")}</small></span></div>`;
}

function memberSectionRows(){
  const d = memberPage.data || {};
  switch (memberPage.section){
    case "added": return (d.adds || []).map(t => memberTrackRow(t, t.ts ? ago(t.ts) : ""));
    case "playlists": return (d.playlists || []).map(memberPlaylistRow);
    case "votes":
      return (d.votes || []).map(memberVoteRow)
        .concat((d.party_votes || []).map(v =>
          memberVoteRow({value: v.value, label: "Party queue", kind: v.room})));
    case "saved": return (d.saves || []).map(t => memberTrackRow(t, ""));
    default: return (d.listens || []).map(t => memberTrackRow(t, `${Number(t.plays) || 1}×`));
  }
}

function memberSectionCount(key){
  const d = memberPage.data || {};
  if (key === "added") return (d.adds || []).length;
  if (key === "playlists") return (d.playlists || []).length;
  if (key === "votes") return (d.votes || []).length + (d.party_votes || []).length;
  if (key === "saved") return (d.saves || []).length;
  return (d.listens || []).length;
}

function memberTabs(){
  return MEMBER_SECTIONS.map(([key, label]) =>
    `<button type="button" class="mptab" aria-pressed="${memberPage.section === key}"
      onclick="setMemberSection('${key}')">${label}<span class="mpn">${memberSectionCount(key)}</span></button>`)
    .join("");
}

function memberLists(){
  const rows = memberSectionRows();
  const hint = rows.length >= MEMBER_LIMIT
    ? `<p class="mpfoot">Showing the ${MEMBER_LIMIT} most recent.</p>` : "";
  const empty = MEMBER_SECTIONS.find(([key]) => key === memberPage.section)?.[2] || "Nothing here yet.";
  return `<nav class="mpswitch" role="group" aria-label="Profile sections">${memberTabs()}</nav>
    ${rows.length ? `<div class="mplist">${rows.join("")}</div>` : `<p class="mpempty">${empty}</p>`}
    ${hint}`;
}

function memberPaintLists(){
  const host = memberEl("mplists");
  if (host) host.innerHTML = memberLists();
}

function setMemberSection(key){
  if (!MEMBER_SECTIONS.some(([k]) => k === key) || key === memberPage.section) return;
  memberPage.section = key;
  memberPaintLists();
}

function memberTiles(){
  const c = memberPage.counts || {};
  const num = value => Number(value || 0).toLocaleString();
  const tile = (value, label) => `<div class="mptile"><b>${num(value)}</b><small>${label}</small></div>`;
  return tile(c.listens, "plays") + tile(c.distinct_tracks, "songs")
    + tile(c.adds, "added") + tile(c.votes, "votes");
}

function memberPaint(){
  if (!memberOpen()) return;
  const face = memberEl("mpface");
  if (face) face.textContent = profileSymbol(memberPage.icon);
  const name = memberEl("mpname");
  if (name) name.textContent = memberPage.name || "Member";
  const more = memberEl("mpmore");
  if (more){
    more.textContent = memberPage.role === "admin" ? "Admin" : "Member";
    more.classList.toggle("admin", memberPage.role === "admin");
  }
  const body = memberEl("mpbody");
  if (!body) return;
  if (!memberPage.data){
    body.innerHTML = `<p class="mpempty">Loading…</p>`;
    return;
  }
  body.innerHTML = `<div class="mptiles">${memberTiles()}</div>
    <section class="mpstats">${statsBody()}</section>
    <div id="mplists">${memberLists()}</div>`;
}

// The profile lives in the Feed section, so opening it from anywhere — a comment, a vote
// chip, the admin member list — has to put the Feed section on screen first.
function memberShowView(){
  for (const key of ["view-queue", "view-library", "view-feed"]){
    const el = document.getElementById(key);
    if (el) el.hidden = key !== "view-feed";
  }
  document.querySelectorAll(".tab[data-view]").forEach(tab =>
    tab.classList.toggle("on", tab.dataset.view === "feed"));
  // The profile is a page inside the Feed: the page header, the section row and the panes
  // step aside, because the profile carries the member's name where the title was.
  const top = memberEl("feedtop");
  if (top) top.hidden = true;
  const tabs = memberEl("feedtabs");
  if (tabs) tabs.hidden = true;
  for (const key of ["activity", "popular"]){
    const pane = memberEl("feedpane-" + key);
    if (pane) pane.hidden = true;
  }
  const page = memberEl("memberpage");
  if (page) page.hidden = false;
}

function memberClosePage(){
  const page = memberEl("memberpage");
  if (page) page.hidden = true;
  const top = memberEl("feedtop");
  if (top) top.hidden = false;
  const tabs = memberEl("feedtabs");
  if (tabs) tabs.hidden = false;
  // Restore whichever section it was opened from, not always Activity.
  paintFeedTab();
}

async function openMember(id, options){
  if (!id) return;
  const key = String(id);
  memberPage.id = key;
  memberPage.name = "";
  memberPage.icon = "music";
  memberPage.role = "";
  memberPage.counts = {};
  memberPage.data = null;
  memberPage.stats = null;
  memberPage.section = "history";
  memberPage.period = "week";
  memberPage.shown = 20;
  memberShowView();
  memberPaint();
  if (!options || options.nav !== false){
    try { window.scrollTo({top: 0, behavior: "instant"}); } catch (_) { window.scrollTo(0, 0); }
    if (typeof memberNavPush === "function") memberNavPush();
  }
  try {
    const r = await fetch("/api/member/" + encodeURIComponent(key), {cache: "no-store"});
    const data = await r.json();
    if (!r.ok) throw new Error(data.error || `HTTP ${r.status}`);
    if (memberPage.id !== key) return;             // another name was tapped while this read
    const who = data.member || {};
    memberPage.name = who.name || "Member";
    memberPage.icon = who.icon || "music";
    memberPage.role = who.role || "";
    memberPage.counts = data.counts || {};
    memberPage.data = data;
    memberPage.stats = data.stats || null;
    memberPaint();
  } catch (e) {
    if (memberPage.id !== key) return;
    const name = memberEl("mpname");
    if (name) name.textContent = "Profile unavailable";
    const more = memberEl("mpmore");
    if (more) more.textContent = "";
    const body = memberEl("mpbody");
    if (body) body.innerHTML = `<p class="mpempty">${esc(e.message)}</p>`;
  }
}

function closeMemberPage(){
  memberClosePage();
  // Opening the profile pushed a history entry, so leaving it is one step back: that
  // restores whichever section it was opened from, including a library page.
  if (history.state && history.state.hlnav && history.state.hlnav.m){
    history.back();
  }
}

function openPlaylistFromMember(id){
  memberClosePage();
  if (typeof showLibraryTab === "function") showLibraryTab();
  openPlaylist(id);
}

document.addEventListener("keydown", event => {
  if (event.key !== "Escape" || !memberOpen()) return;
  if (document.querySelector("dialog[open]")) return;         // the dialog above owns the key
  const full = document.getElementById("player-full");
  if (full && full.classList.contains("on")) return;          // and so does the player
  closeMemberPage();
});