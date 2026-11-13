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

async function openMember(id){
  if (!id) return;
  const dlg = $("#memberdlg");
  $("#mdname").textContent = "Loading…";
  $("#mdmeta").textContent = "";
  $("#mdicon").textContent = "";
  $("#mdbody").innerHTML = "";
  if (!dlg.open) dlg.showModal();
  try {
    const r = await fetch("/api/member/" + encodeURIComponent(id), {cache: "no-store"});
    const data = await r.json();
    if (!r.ok) throw new Error(data.error || `HTTP ${r.status}`);
    const who = data.member, counts = data.counts || {};
    $("#mdicon").textContent = profileSymbol(who.icon);
    $("#mdname").textContent = who.name;
    $("#mdmeta").textContent = `${who.role === "admin" ? "Admin" : "Member"} · `
      + `${counts.distinct_tracks || 0} songs · ${counts.adds || 0} added · ${counts.votes || 0} votes`;
    const section = (title, rows) => rows.length
      ? `<section class="mds"><h4>${title}</h4>${rows.join("")}</section>` : "";
    const trackRow = t => `<div class="mdrow"><span>${esc(t.title || "…")}</span>`
      + `<small>${esc(t.artist || t.artist_name || "")}${t.plays ? ` · ${Number(t.plays)}×` : ""}`
      + `${t.ts ? ` · ${ago(t.ts)}` : ""}</small></div>`;
    $("#mdbody").innerHTML = [
      section("Listening history", (data.listens || []).map(trackRow)),
      section("Added to the library", (data.adds || []).map(trackRow)),
      section("Playlists", (data.playlists || []).map(p =>
        `<div class="mdrow"><span>${esc(p.title)}</span><small>${Number(p.tracks) || 0} tracks</small></div>`)),
      section("Votes given", (data.votes || []).map(v =>
        `<div class="mdrow"><span>${v.value > 0 ? "▲" : v.value < 0 ? "▼" : "○"} ${esc(v.label || v.kind)}</span>`
        + `<small>${esc(v.kind)}</small></div>`)
        .concat((data.party_votes || []).map(v =>
          `<div class="mdrow"><span>${v.value > 0 ? "▲" : "▼"} party queue</span><small>${esc(v.room)}</small></div>`))),
      section("Saved", (data.saves || []).map(trackRow)),
    ].join("") || `<div class="empty">Nothing recorded yet.</div>`;
  } catch (e) {
    $("#mdname").textContent = "Profile unavailable";
    $("#mdmeta").textContent = e.message;
  }
}

function closeMember(){ const dlg = $("#memberdlg"); if (dlg && dlg.open) dlg.close(); }
