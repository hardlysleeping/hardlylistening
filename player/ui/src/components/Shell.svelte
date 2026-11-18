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
  import type { Album, Song } from "../lib/subsonic";
  import AlbumCard from "./AlbumCard.svelte";
  import Cover from "./Cover.svelte";
  import TrackList from "./TrackList.svelte";

  interface Props {
    library: Library;
    player: PlayerStore;
    onsignout: () => void;
    serverLabel: string;
  }

  let { library, player, onsignout, serverLabel }: Props = $props();

  type Tab = "albums" | "artists" | "playlists" | "favorites" | "search";

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
  }

  // The first screen asks for one page of albums. Nothing else is fetched until it is opened.
  $effect(() => {
    if (library.ready && library.albums.length === 0 && !library.albumsDone) {
      void library.loadAlbums({ reset: true });
    }
  });

  function sing(album: Album): string {
    const count = album.songCount ?? album.song?.length ?? 0;
    return `${count} song${count === 1 ? "" : "s"}`;
  }

  function playList(songs: Song[], index: number): void {
    void player.playQueue(songs, index);
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
    <nav class="ps-tabs" aria-label="Library">
      <button type="button" class:on={tab === "albums"} onclick={() => open("albums")}>Albums</button>
      <button type="button" class:on={tab === "artists"} onclick={() => open("artists")}>Artists</button>
      <button type="button" class:on={tab === "playlists"} onclick={() => open("playlists")}>Playlists</button>
      <button type="button" class:on={tab === "favorites"} onclick={() => open("favorites")}>Favourites</button>
    </nav>
    <form class="ps-search" onsubmit={submitSearch}>
      <input type="search" placeholder="Search" aria-label="Search" bind:value={query} />
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
            <button
              type="button"
              class="ps-play"
              onclick={() => {
                const songs = album.song ?? [];
                if (songs.length) playList(songs, 0);
              }}>Play album</button>
          </div>
        </section>
        <TrackList
          songs={album.song ?? []}
          currentId={player.track?.id ?? null}
          playing={player.playing}
          onplay={(index) => playList(album.song ?? [], index)}
          onqueue={(index) => {
            const song = album.song?.[index];
            if (song) player.enqueue([song], { next: true });
          }}
        />
      {:else if library.artist}
        {@const artist = library.artist}
        <section class="ps-detail">
          <div class="ps-detail-meta">
            <h2>{artist.name}</h2>
            <p class="ps-dim">{artist.albumCount ?? artist.album?.length ?? 0} albums</p>
          </div>
        </section>
        <div class="songgrid">
          {#each artist.album ?? [] as album (album.id)}
            <AlbumCard {library} {album} onopen={() => void openAlbum(album.id)} />
          {/each}
        </div>
      {:else if library.playlist}
        {@const playlist = library.playlist}
        <section class="ps-detail">
          <div class="ps-detail-meta">
            <h2>{playlist.name}</h2>
            <p class="ps-dim">{playlist.songCount ?? playlist.entry?.length ?? 0} songs</p>
            <button
              type="button"
              class="ps-play"
              onclick={() => {
                const songs = playlist.entry ?? [];
                if (songs.length) playList(songs, 0);
              }}>Play playlist</button>
          </div>
        </section>
        <TrackList
          songs={playlist.entry ?? []}
          currentId={player.track?.id ?? null}
          playing={player.playing}
          onplay={(index) => playList(playlist.entry ?? [], index)}
          onqueue={(index) => {
            const song = playlist.entry?.[index];
            if (song) player.enqueue([song], { next: true });
          }}
        />
      {/if}
    {:else if tab === "albums"}
      <div class="songgrid">
        {#each library.albums as album (album.id)}
          <AlbumCard {library} {album} onopen={() => void openAlbum(album.id)} />
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
        />
      {/if}
      {#if library.starred.albums.length}
        <h3 class="ps-h3">Albums</h3>
        <div class="songgrid">
          {#each library.starred.albums as album (album.id)}
            <AlbumCard {library} {album} onopen={() => void openAlbum(album.id)} />
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
            <AlbumCard {library} {album} onopen={() => void openAlbum(album.id)} />
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
        />
      {/if}
      {#if !library.results.artists.length && !library.results.albums.length && !library.results.songs.length}
        <p class="ps-dim">No results.</p>
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

  .ps-h3 {
    margin: 8px 0 0;
    font-size: 13px;
    text-transform: uppercase;
    letter-spacing: 0.08em;
    color: var(--dim);
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