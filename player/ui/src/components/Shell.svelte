<script lang="ts">
  /* The library shell: five destinations, one of them open at a time.
   *
   * Albums, Artists, Playlists, Favourites and Search are the whole product. There is no Repo
   * tab, no download queue, no acquisition, and no source matching — those are the host app's
   * job, and this client is not that app.
   *
   * The chrome is this component's own small stylesheet built from the host's tokens
   * (`--panel`, `--line`, `--accent`), because the host's `#appnav` carries five destinations
   * that include Repo and Feed and would have to be dismantled anyway. The player surfaces
   * themselves are the host's, imported whole.
   */
  import type { Library } from "../lib/library/store.svelte";
  import type { PlayerStore } from "../lib/player/store.svelte";
  import type { KeepStore } from "../lib/keep/store.svelte";
  import type { Album, Song } from "../lib/subsonic";
  import AlbumCard from "./AlbumCard.svelte";
  import Cover from "./Cover.svelte";
  import TrackList, { type KeepState } from "./TrackList.svelte";

  interface Props {
    library: Library;
    player: PlayerStore;
    keep: KeepStore;
    onsignout: () => void;
    serverLabel: string;
  }

  let { library, player, keep, onsignout, serverLabel }: Props = $props();

  type Tab = "albums" | "artists" | "playlists" | "favorites" | "search" | "kept";

  let tab = $state<Tab>("albums");
  let query = $state("");

  /* Signing out is one tap away from somebody browsing their library — the server chip is the
     only control in the header that is not a destination. So it asks: the first tap turns the
     chip into "Sign out?", the second does it, and ignoring it puts the chip back. The record
     and its keychain secret survive, which is why the connect screen can offer the account
     again; this just stops a mis-tap from looking like a lost library. */
  let confirming = $state(false);
  let confirmTimer: ReturnType<typeof setTimeout> | null = null;

  function signOutTap(): void {
    if (!confirming) {
      confirming = true;
      if (confirmTimer) clearTimeout(confirmTimer);
      confirmTimer = setTimeout(() => (confirming = false), 5000);
      return;
    }
    if (confirmTimer) clearTimeout(confirmTimer);
    confirmTimer = null;
    confirming = false;
    onsignout();
  }

  function signOutKey(event: KeyboardEvent): void {
    if (event.key !== "Escape" || !confirming) return;
    event.preventDefault();
    if (confirmTimer) clearTimeout(confirmTimer);
    confirmTimer = null;
    confirming = false;
  }

  /* A load that failed is not the same as a library with nothing in it, and the wall cannot tell
     the difference by itself: `albumsDone` is set on failure too, so "Show more" disappears and
     nothing is left to press. This is the one control that asks again. */
  function retry(): void {
    library.error = null;
    if (tab === "albums") void library.loadAlbums({ reset: true });
    if (tab === "artists") void library.loadArtists();
    if (tab === "playlists") void library.loadPlaylists();
    if (tab === "favorites") void library.loadStarred();
    if (tab === "search" && query.trim()) void library.search(query);
  }

  const detail = $derived(library.album ?? library.artist ?? library.playlist);

  function open(next: Tab): void {
    tab = next;
    library.closeDetail();
    if (next === "albums") void library.loadAlbums({ reset: false });
    if (next === "artists") void library.loadArtists();
    if (next === "playlists") void library.loadPlaylists();
    if (next === "favorites") void library.loadStarred();
    if (next === "kept") void keep.refresh();
  }

  // The first screen asks for one page of albums. Nothing else is fetched until it is opened.
  $effect(() => {
    if (library.ready && library.albums.length === 0 && !library.albumsDone) {
      void library.loadAlbums({ reset: true });
    }
  });

  function songs(count: number | undefined): string {
    const total = count ?? 0;
    return `${total} song${total === 1 ? "" : "s"}`;
  }

  function sing(album: Album): string {
    const count = album.songCount ?? album.song?.length ?? 0;
    return `${count} song${count === 1 ? "" : "s"}`;
  }

  function playList(songs: Song[], index: number): void {
    void player.playQueue(songs, index);
  }

  /* Kept copies.
   *
   * The store is the truth about what is held (it re-reads the folder's index after every
   * change), so the shell asks it rather than keeping a second list. A row reads its own state
   * rather than the shell deciding per screen, which is why an album's songs and a search
   * result look the same: they are the same song. */
  function keepState(id: string): KeepState {
    if (keep.keeping(id)) return "keeping";
    return keep.has(id) ? "kept" : null;
  }

  function keepOne(song: Song | undefined): void {
    if (song) void keep.keep(song);
  }

  function keepMany(songs: readonly Song[] | undefined): void {
    if (songs?.length) void keep.keepAll(songs);
  }

  /** True while a song in this list is being fetched, so the button can say what is happening. */
  function keepingAny(songs: readonly Song[] | undefined): boolean {
    return (songs ?? []).some((song) => keep.keeping(song.id));
  }

  /** True when every song in this list is already on this device. */
  function allKept(songs: readonly Song[] | undefined): boolean {
    const list = songs ?? [];
    return list.length > 0 && list.every((song) => keep.has(song.id));
  }

  function keepLabel(songs: readonly Song[] | undefined): string {
    if (keepingAny(songs)) return "Keeping…";
    return allKept(songs) ? "Kept on this device" : "Keep for offline";
  }

  /** A size a person can hold in their head. Approximate on purpose: this is a phone, not `df`. */
  function size(bytes: number): string {
    if (bytes < 1024 * 1024) return `${Math.max(1, Math.round(bytes / 1024))} KB`;
    if (bytes < 1024 * 1024 * 1024) return `${(bytes / (1024 * 1024)).toFixed(1)} MB`;
    return `${(bytes / (1024 * 1024 * 1024)).toFixed(2)} GB`;
  }

  /* Playlists are the one thing this client writes, so the controls live where the lists are: a
     name field above the Playlists tab makes one and opens it, and an open playlist carries its
     rename, its add of whatever is playing, and a remove on every row. Every write is answered by
     re-reading the server, so what is on screen is what the server holds — a locally patched row
     would disagree with it the first time a write was refused. */
  let newName = $state("");
  let renaming = $state(false);
  let renameTo = $state("");

  async function makePlaylist(event: SubmitEvent): Promise<void> {
    event.preventDefault();
    if (await library.createPlaylist(newName)) newName = "";
  }

  function startRename(): void {
    renameTo = library.playlist?.name ?? "";
    renaming = true;
  }

  async function saveRename(event: SubmitEvent): Promise<void> {
    event.preventDefault();
    const name = renameTo.trim();
    if (!name || name === library.playlist?.name) {
      renaming = false;
      return;
    }
    if (await library.editPlaylist({ name })) renaming = false;
  }

  async function addPlaying(): Promise<void> {
    const song = player.track;
    if (song) await library.editPlaylist({ add: song.id });
  }

  async function removeAt(index: number): Promise<void> {
    await library.editPlaylist({ removeIndex: index });
  }

  async function openAlbum(id: string): Promise<void> {
    await library.openAlbum(id);
  }

  async function submitSearch(event: SubmitEvent): Promise<void> {
    event.preventDefault();
    if (!query.trim()) return;
    tab = "search";
    library.closeDetail();
    await library.search(query);
  }
</script>

<div class="player-shell">
  <header class="ps-nav">
    <div class="ps-brand">Hardly<span>Listening</span></div>
    <!-- Which view is open was carried by the `.on` class alone, which is colour and nothing
         else: a screen reader heard four buttons with no way to tell which one it was already
         looking at. `aria-current` is that fact, stated. -->
    <nav class="ps-tabs" aria-label="Library">
      <button type="button" class:on={tab === "albums"} aria-current={tab === "albums" ? "page" : undefined} onclick={() => open("albums")}>Albums</button>
      <button type="button" class:on={tab === "artists"} aria-current={tab === "artists" ? "page" : undefined} onclick={() => open("artists")}>Artists</button>
      <button type="button" class:on={tab === "playlists"} aria-current={tab === "playlists" ? "page" : undefined} onclick={() => open("playlists")}>Playlists</button>
      <button type="button" class:on={tab === "favorites"} aria-current={tab === "favorites" ? "page" : undefined} onclick={() => open("favorites")}>Favourites</button>
      <!-- Six destinations, not five: the kept copies are a place a person goes, not a setting. -->
      <button type="button" class:on={tab === "kept"} aria-current={tab === "kept" ? "page" : undefined} onclick={() => open("kept")}>Kept</button>
    </nav>
    <form class="ps-search" onsubmit={submitSearch}>
      <input type="search" placeholder="Search" aria-label="Search" autocomplete="off"
        autocapitalize="off" autocorrect="off" spellcheck="false" enterkeyhint="search"
        bind:value={query} />
    </form>
    <button
      type="button"
      class="ps-server"
      class:confirming
      title={confirming ? "Tap again to sign out" : serverLabel}
      aria-label={confirming ? `Sign out of ${serverLabel}` : `Signed in to ${serverLabel}. Sign out`}
      onkeydown={signOutKey}
      onclick={signOutTap}>
      {confirming ? "Sign out?" : serverLabel}
    </button>
  </header>

  <main class="ps-main">
    {#if library.error}
      <p class="ps-error" role="status">{library.error}</p>
      <button type="button" class="ps-more" onclick={retry}>Try again</button>
    {/if}

    {#if player.error}
      <p class="ps-error" role="status">{player.error}</p>
    {/if}

    {#if detail}
      <button type="button" class="ps-back" onclick={() => library.closeDetail()}>← Back</button>

      {#if library.album}
        {@const album = library.album}
        <section class="ps-detail">
          <div class="ps-detail-art">
            <Cover library={library} coverArt={album.coverArt} label={album.name} size={400} />
          </div>
          <div class="ps-detail-meta">
            <h2>{album.name}</h2>
            <p>{album.artist ?? ""}{album.year ? ` · ${album.year}` : ""}</p>
            <p class="ps-dim">{sing(album)}</p>
            <div class="ps-actions">
              <button
                type="button"
                class="ps-play"
                disabled={!album.song?.length}
                onclick={() => {
                  const songs = album.song ?? [];
                  if (songs.length) playList(songs, 0);
                }}>Play album</button>
              <!-- Keep the album in one tap: the songs a person listens to offline are albums and
                   playlists, not single rows, and 12 taps per album is not a feature. -->
              <button
                type="button"
                class="ps-keep"
                disabled={!album.song?.length || keepingAny(album.song) || allKept(album.song)}
                onclick={() => keepMany(album.song)}>{keepLabel(album.song)}</button>
            </div>
          </div>
        </section>
        {#if album.song?.length}
          <TrackList
            songs={album.song}
            currentId={player.track?.id ?? null}
            playing={player.playing}
            onplay={(index) => playList(album.song ?? [], index)}
            onqueue={(index) => {
              const song = album.song?.[index];
              if (song) player.enqueue([song], { next: true });
            }}
            onkeep={(index) => keepOne(album.song?.[index])}
            {keepState}
          />
        {:else}
          <p class="ps-dim">This album has no songs on this server.</p>
        {/if}
      {:else if library.artist}
        {@const artist = library.artist}
        <section class="ps-detail">
          <div class="ps-detail-meta">
            <h2>{artist.name}</h2>
            <p class="ps-dim">{artist.albumCount ?? artist.album?.length ?? 0} albums</p>
          </div>
        </section>
        {#if artist.album?.length}
          <div class="songgrid">
            {#each artist.album as album (album.id)}
              <AlbumCard {library} {album} playing={player.track?.albumId === album.id} onopen={() => void openAlbum(album.id)} />
            {/each}
          </div>
        {:else}
          <p class="ps-dim">This artist has no albums here.</p>
        {/if}
      {:else if library.playlist}
        {@const playlist = library.playlist}
        <section class="ps-detail">
          <div class="ps-detail-meta">
            <h2>{playlist.name}</h2>
            <p class="ps-dim">{songs(playlist.songCount ?? playlist.entry?.length)}</p>
            <button
              type="button"
              class="ps-play"
              disabled={!playlist.entry?.length}
              onclick={() => {
                const songs = playlist.entry ?? [];
                if (songs.length) playList(songs, 0);
              }}>Play playlist</button>
            <div class="ps-edit">
              <button
                type="button"
                disabled={!playlist.entry?.length || keepingAny(playlist.entry) || allKept(playlist.entry)}
                onclick={() => keepMany(playlist.entry)}>{keepLabel(playlist.entry)}</button>
              {#if renaming}
                <form class="ps-rename" onsubmit={saveRename}>
                  <input
                    type="text"
                    bind:value={renameTo}
                    aria-label="Playlist name"
                    autocomplete="off"
                    autocorrect="off"
                    enterkeyhint="done"
                  />
                  <button type="submit" disabled={!renameTo.trim()}>Save</button>
                  <button type="button" onclick={() => (renaming = false)}>Cancel</button>
                </form>
              {:else}
                <button type="button" onclick={startRename}>Rename</button>
              {/if}
              <button
                type="button"
                disabled={!player.track}
                title={player.track ? `Add ${player.track.title}` : "Nothing is playing"}
                onclick={() => void addPlaying()}>Add the playing song</button>
            </div>
          </div>
        </section>
        {#if playlist.entry?.length}
          <TrackList
            songs={playlist.entry}
            currentId={player.track?.id ?? null}
            playing={player.playing}
            onplay={(index) => playList(playlist.entry ?? [], index)}
            onqueue={(index) => {
              const song = playlist.entry?.[index];
              if (song) player.enqueue([song], { next: true });
            }}
            onremove={(index) => void removeAt(index)}
            onkeep={(index) => keepOne(playlist.entry?.[index])}
            {keepState}
          />
        {:else}
          <p class="ps-dim">This playlist is empty.</p>
        {/if}
      {/if}
    {:else if tab === "albums"}
      <div class="songgrid">
        {#each library.albums as album (album.id)}
          <AlbumCard {library} {album} playing={player.track?.albumId === album.id} onopen={() => void openAlbum(album.id)} />
        {/each}
      </div>
      {#if library.loadingAlbums}
        <p class="ps-dim">Loading…</p>
      {:else if !library.albumsDone}
        <button type="button" class="ps-more" onclick={() => void library.loadAlbums()}>Show more</button>
      {:else if library.albums.length === 0 && !library.error}
        <p class="ps-dim">This server has no albums yet.</p>
      {/if}
    {:else if tab === "artists"}
      <div class="ps-rows">
        {#each library.artists as artist (artist.id)}
          <button type="button" class="ps-row" onclick={() => void library.openArtist(artist.id)}>
            <span>{artist.name}</span>
            <span class="ps-dim">{artist.albumCount ?? ""}</span>
          </button>
        {/each}
      </div>
      {#if library.loadingArtists}
        <p class="ps-dim">Loading…</p>
      {:else if library.artists.length === 0 && !library.error}
        <p class="ps-dim">This server has no artists yet.</p>
      {/if}
    {:else if tab === "playlists"}
      <form class="ps-new" onsubmit={makePlaylist}>
        <input
          type="text"
          bind:value={newName}
          placeholder="New playlist"
          aria-label="New playlist name"
          autocomplete="off"
          autocorrect="off"
          enterkeyhint="done"
        />
        <button type="submit" disabled={!newName.trim()}>Create</button>
      </form>
      <div class="ps-rows">
        {#each library.playlists as playlist (playlist.id)}
          <button type="button" class="ps-row" onclick={() => void library.openPlaylist(playlist.id)}>
            <span>{playlist.name}</span>
            <span class="ps-dim">{playlist.songCount ?? 0}</span>
          </button>
        {/each}
      </div>
      {#if library.loadingPlaylists}
        <p class="ps-dim">Loading…</p>
      {:else if library.playlists.length === 0 && !library.error}
        <p class="ps-dim">This server has no playlists yet.</p>
      {/if}
    {:else if tab === "favorites"}
      {#if library.starred.songs.length}
        <TrackList
          songs={library.starred.songs}
          currentId={player.track?.id ?? null}
          playing={player.playing}
          onplay={(index) => playList(library.starred.songs, index)}
          onkeep={(index) => keepOne(library.starred.songs[index])}
          {keepState}
        />
      {/if}
      {#if library.starred.albums.length}
        <h3 class="ps-h3">Albums</h3>
        <div class="songgrid">
          {#each library.starred.albums as album (album.id)}
            <AlbumCard {library} {album} playing={player.track?.albumId === album.id} onopen={() => void openAlbum(album.id)} />
          {/each}
        </div>
      {/if}
      {#if library.starred.artists.length}
        <h3 class="ps-h3">Artists</h3>
        <div class="ps-rows">
          {#each library.starred.artists as artist (artist.id)}
            <button type="button" class="ps-row" onclick={() => void library.openArtist(artist.id)}>
              <span>{artist.name}</span>
              <span class="ps-dim">{artist.albumCount ?? ""}</span>
            </button>
          {/each}
        </div>
      {/if}
      {#if !library.starred.songs.length && !library.starred.albums.length && !library.starred.artists.length && !library.error}
        <p class="ps-dim">Nothing is starred on this server yet.</p>
      {/if}
    {:else if tab === "search"}
      {#if library.lastQuery && (library.results.artists.length || library.results.albums.length || library.results.songs.length)}
        <h3 class="ps-h3">Results for <span class="ps-term">{library.lastQuery}</span></h3>
      {/if}
      {#if library.results.artists.length}
        <h3 class="ps-h3">Artists</h3>
        <div class="ps-rows">
          {#each library.results.artists as artist (artist.id)}
            <button type="button" class="ps-row" onclick={() => void library.openArtist(artist.id)}>
              <span>{artist.name}</span>
              <span class="ps-dim">{artist.albumCount ?? ""}</span>
            </button>
          {/each}
        </div>
      {/if}
      {#if library.results.albums.length}
        <h3 class="ps-h3">Albums</h3>
        <div class="songgrid">
          {#each library.results.albums as album (album.id)}
            <AlbumCard {library} {album} playing={player.track?.albumId === album.id} onopen={() => void openAlbum(album.id)} />
          {/each}
        </div>
      {/if}
      {#if library.results.songs.length}
        <h3 class="ps-h3">Songs</h3>
        <TrackList
          songs={library.results.songs}
          currentId={player.track?.id ?? null}
          playing={player.playing}
          onplay={(index) => playList(library.results.songs, index)}
          onkeep={(index) => keepOne(library.results.songs[index])}
          {keepState}
        />
      {/if}
      {#if !library.results.artists.length && !library.results.albums.length && !library.results.songs.length && !library.error}
        <p class="ps-dim">
          {library.lastQuery ? `No results for “${library.lastQuery}”.` : "No results."}
        </p>
      {/if}
    {:else if tab === "kept"}
      {#if keep.error}
        <!-- A shell without the keep commands says so, rather than drawing an empty folder that
             looks like a person kept nothing. -->
        <p class="ps-dim">{keep.error}</p>
      {:else if keep.rows.length}
        <div class="ps-kept-head">
          <p class="ps-dim">
            {keep.rows.length} song{keep.rows.length === 1 ? "" : "s"} · {size(keep.bytes)} on this device
          </p>
          <button type="button" class="ps-more" onclick={() => void keep.clear()}>Remove all</button>
        </div>
        <TrackList
          songs={keep.rows}
          currentId={player.track?.id ?? null}
          playing={player.playing}
          onplay={(index) => playList(keep.rows, index)}
          onqueue={(index) => {
            const song = keep.rows[index];
            if (song) player.enqueue([song], { next: true });
          }}
          onremove={(index) => {
            const id = keep.rows[index]?.id;
            if (id) void keep.forget(id);
          }}
        />
      {:else}
        <p class="ps-dim">
          No songs are kept on this device yet. Open an album or a playlist and choose
          “Keep for offline”.
        </p>
      {/if}
    {/if}
  </main>
</div>

<style>
  /* The player's own chrome. Tokens come from the host's `tokens.css`, so it is the same
     palette, the same radii and the same type; only the arrangement is this shell's. */
  .player-shell {
    min-height: 100vh;
    padding: 0 16px 140px;
    display: flex;
    flex-direction: column;
    gap: 16px;
  }

  .ps-nav {
    position: sticky;
    top: 0;
    z-index: 20;
    display: flex;
    align-items: center;
    gap: 14px;
    flex-wrap: wrap;
    padding: 12px 0;
    background: linear-gradient(180deg, var(--bg) 70%, transparent);
    backdrop-filter: blur(10px);
  }

  .ps-brand {
    font-weight: 700;
    letter-spacing: -0.02em;
  }

  .ps-brand span {
    color: var(--accent);
  }

  .ps-tabs {
    display: flex;
    gap: 6px;
    flex-wrap: wrap;
  }

  .ps-tabs button {
    border: 1px solid var(--line);
    background: var(--panel);
    color: var(--text);
    border-radius: 999px;
    padding: 7px 14px;
    font-size: 13px;
    cursor: pointer;
  }

  .ps-tabs button.on {
    border-color: var(--accent);
    color: var(--accent);
  }

  .ps-search {
    margin-left: auto;
  }

  .ps-search input {
    width: 200px;
    max-width: 45vw;
    padding: 8px 12px;
    border-radius: 999px;
    border: 1px solid var(--line);
    background: var(--panel);
    color: var(--text);
  }

  .ps-server {
    max-width: 200px;
    overflow: hidden;
    text-overflow: ellipsis;
    white-space: nowrap;
    border: 1px solid var(--line);
    background: none;
    color: var(--dim);
    border-radius: 999px;
    padding: 7px 12px;
    font-size: 12px;
    cursor: pointer;
  }

  /* The second tap is the one that signs out, so it has to look different from the first. */
  .ps-server.confirming {
    border-color: var(--danger, #ff7a7a);
    color: var(--danger, #ff7a7a);
    max-width: none;
  }

  .ps-main {
    display: flex;
    flex-direction: column;
    gap: 14px;
    /* `main` in the host's layout is a block: `max-width:1000px; margin:0 auto` centres it and it
       still fills the page. Here it is a flex item, and auto cross-axis margins cancel the
       stretch — so it shrink-to-fits. With `repeat(4, minmax(0,1fr))` album columns that inner
       min-content is 0, and every card collapsed to a sliver. The width is this shell's job. */
    width: 100%;
    min-width: 0;
  }

  .ps-dim {
    color: var(--dim);
    font-size: 13px;
  }

  .ps-error {
    color: var(--danger, #ff7a7a);
    font-size: 13px;
  }

  .ps-more {
    align-self: center;
    border: 1px solid var(--line);
    background: var(--panel);
    color: var(--text);
    border-radius: 999px;
    padding: 9px 20px;
    cursor: pointer;
  }

  .ps-back {
    align-self: flex-start;
    border: 0;
    background: none;
    color: var(--dim);
    cursor: pointer;
    padding: 4px 0;
  }

  .ps-detail {
    display: flex;
    gap: 20px;
    align-items: flex-end;
  }

  .ps-detail-art {
    width: 180px;
    flex: 0 0 auto;
  }

  .ps-detail-meta h2 {
    margin: 0 0 6px;
    font-size: 24px;
  }

  .ps-detail-meta p {
    margin: 0 0 4px;
  }

  .ps-play {
    margin-top: 10px;
    border: 0;
    background: var(--accent);
    color: #08110d;
    font-weight: 700;
    border-radius: 999px;
    padding: 9px 20px;
    cursor: pointer;
  }

  /* An album with nothing in it has a Play button that would do nothing, so it says so by
     looking inert rather than by promising a queue. */
  .ps-play:disabled {
    opacity: 0.45;
    cursor: default;
  }

  /* Play and Keep sit side by side under the album's title: one starts the music, the other makes
     it work with no signal. Keep wraps below Play before either shrinks. */
  .ps-actions {
    display: flex;
    flex-wrap: wrap;
    align-items: center;
    gap: 8px;
    margin-top: 10px;
  }

  .ps-actions .ps-play {
    margin-top: 0;
  }

  .ps-keep {
    border: 1px solid var(--line);
    background: var(--panel);
    color: var(--text);
    border-radius: 999px;
    padding: 9px 18px;
    font-weight: 600;
    cursor: pointer;
  }

  .ps-keep:hover:not(:disabled) {
    border-color: var(--accent);
    color: var(--accent);
  }

  .ps-keep:disabled {
    opacity: 0.45;
    cursor: default;
  }

  /* What this device holds, and the one control that empties it. */
  .ps-kept-head {
    display: flex;
    align-items: center;
    justify-content: space-between;
    flex-wrap: wrap;
    gap: 8px;
  }

  /* Making a playlist is one row: the field takes the width, the button keeps its own, so a long
     name never pushes Create off the edge. The field is 16px because anything smaller makes a
     phone zoom the page the moment it is focused. */
  .ps-new {
    display: flex;
    gap: 8px;
  }

  .ps-new input,
  .ps-rename input {
    flex: 1 1 auto;
    min-width: 0;
    font-size: 16px;
    padding: 9px 12px;
    border: 1px solid var(--line);
    border-radius: 10px;
    background: var(--panel);
    color: var(--text);
  }

  .ps-new button,
  .ps-rename button,
  .ps-edit > button {
    flex: 0 0 auto;
    border: 1px solid var(--line);
    background: var(--panel);
    color: var(--text);
    border-radius: 999px;
    padding: 9px 16px;
    cursor: pointer;
  }

  .ps-new button:disabled,
  .ps-rename button:disabled,
  .ps-edit > button:disabled {
    opacity: 0.45;
    cursor: default;
  }

  /* The playlist's own two controls, under Play: they edit the list on this screen, so they sit
     below it rather than in the header with the destination tabs. */
  .ps-edit {
    display: flex;
    flex-wrap: wrap;
    gap: 8px;
    margin-top: 8px;
  }

  .ps-rename {
    display: flex;
    flex: 1 1 100%;
    gap: 8px;
  }

  .ps-rows {
    display: flex;
    flex-direction: column;
  }

  .ps-row {
    display: flex;
    justify-content: space-between;
    gap: 12px;
    border: 0;
    border-bottom: 1px solid var(--line);
    background: none;
    color: var(--text);
    padding: 12px 4px;
    text-align: left;
    cursor: pointer;
  }

  .ps-row:hover {
    background: var(--panel);
  }

  /* The host's base sheet sets `white-space: nowrap` on every button, which is right for a chip
     and wrong for a row whose label is whatever the server called the artist: a long name ran
     past the card and pushed the album count off the edge. The name truncates and keeps its
     column; the count keeps its own. */
  .ps-row > span:first-child {
    min-width: 0;
    overflow: hidden;
    text-overflow: ellipsis;
  }

  .ps-row > span:last-child {
    flex: 0 0 auto;
  }

  .ps-h3 {
    margin: 8px 0 0;
    font-size: 13px;
    text-transform: uppercase;
    letter-spacing: 0.08em;
    color: var(--dim);
  }

  /* The term is what somebody typed, not a section label: it keeps its own case. */
  .ps-h3 .ps-term {
    text-transform: none;
    letter-spacing: 0;
    color: var(--text);
  }

  /* The host sizes this grid from its density control (`--song-cols`: 2 on a phone, 4 on a
     desktop). The player has no such control, so it takes the same defaults — without them the
     grid falls back to 4 columns at any width, which at 390 px leaves each title as "N…". */
  .ps-main .songgrid {
    --song-cols: 2;
  }

  @media (min-width: 761px) {
    .ps-main .songgrid {
      --song-cols: 4;
    }
  }

  @media (max-width: 700px) {
    /* Five destinations at the desktop chip size wrapped onto a second row, which put two bands of
       chips above the fold on a phone. Tighter chips keep them on one row at 390 and 440; at 320
       they still wrap, which is the honest outcome there. */
    .ps-tabs {
      gap: 4px;
    }

    .ps-tabs button {
      padding: 6px 10px;
      font-size: 12px;
    }

    .ps-detail {
      flex-direction: column;
      align-items: flex-start;
    }

    .ps-detail-art {
      width: 140px;
    }

    .ps-search {
      margin-left: 0;
      order: 5;
      width: 100%;
    }

    .ps-search input {
      width: 100%;
      max-width: none;
    }
  }
</style>