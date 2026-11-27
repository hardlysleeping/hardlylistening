<script lang="ts">
  /* The player's entry point.
   *
   * Three states: sign in, browse, and the fullscreen player. Nothing else is on this page —
   * no repo tab, no acquisition, no download queue. The library is asked for one page of albums
   * and nothing more, which is the whole point of P1.
   */
  import { PlayerStore } from "./lib/player/store.svelte";
  import { Session } from "./lib/app/session.svelte";
  import { Library } from "./lib/library/store.svelte";
  import { KeepStore } from "./lib/keep/store.svelte";
  import { nativeInvoke, type Song } from "./lib/subsonic";
  import { nativeListen } from "./lib/player/native-engine";
  import { useNativeAudioIfPresent } from "./lib/player/select";
  import { NowPlaying } from "./lib/player/media";
  import Connect from "./components/Connect.svelte";
  import Shell from "./components/Shell.svelte";
  import CompactPlayer from "./components/player/CompactPlayer.svelte";
  import FullPlayer from "./components/FullPlayer.svelte";

  const params = new URLSearchParams(window.location.search);
  const preview = params.get("preview");
  const isPreview =
    preview === "compact" || preview === "fullscreen" || preview === "queue" || preview === "lyrics";

  const player = new PlayerStore();
  // Built before the session: the session binds it, because the moment a client exists is the
  // only correct moment to bind it (and the moment it stops existing is the only correct moment
  // to let it go — see `Session.signOut`).
  const library = new Library();
  const session = new Session(player, { library });
  /* What this device holds. Built here rather than inside the shell because the player needs it
     too: `useLocalSource` is what makes a kept song play with no network, and it is the same
     store the shell lists. In a browser there is no folder and no native command, so it stays
     detached and every song streams, exactly as before. */
  const keep = new KeepStore();

  // A phone gets the platform's player, so playback survives the lock screen; a browser and a
  // desktop build get the WebView's. Asked once, and the answer decides — see `select.ts`.
  void useNativeAudioIfPresent(player, nativeInvoke(), nativeListen());

  /* The other half of a lock screen: the tag it draws and the buttons it offers. Built from the
     store's own state, so the session is a picture of the player and never a second player —
     a press on a head unit takes the same path as a press on the docked bar. */
  const nowPlaying = new NowPlaying({
    source: () => ({
      track: player.track,
      playing: player.playing,
      position: player.position,
      duration: player.duration,
      artwork: player.cover,
    }),
    target: {
      play: () => {
        if (!player.playing) void player.toggle();
      },
      pause: () => {
        if (player.playing) void player.toggle();
      },
      next: () => void player.next(),
      previous: () => void player.previous(),
      seekTo: (fraction) => player.seekTo(fraction),
    },
  });

  $effect(() => {
    // A signed-out player has nothing to announce; leaving the last tag up would offer a song
    // from an account the phone is no longer holding.
    if (!player.track) nowPlaying.clear();
    else nowPlaying.publish();
  });

  let fullscreen = $state(preview === "fullscreen" || preview === "queue" || preview === "lyrics");
  let queueOpen = $state(preview === "queue");

  const ready = $derived(session.status === "ready");

  if (isPreview) {
    // The parity harness renders one surface at a time with the same fixture the visual baselines
    // were captured from. It is a preview, not a second app: it never opens a socket.
    player.track = {
      id: "42",
      title: "Baseline Track",
      artist: "Artist",
      album: "Album",
      duration: 0,
    } satisfies Song;
  }

  if (preview === "lyrics") {
    // The same words the host's own fixture answers with, so the two overlays can be put side
    // by side. Timed lines only: a plain file is one `<pre>` and has no line to follow.
    player.lyrics = {
      synced: true,
      line: [
        { start: 0, value: "First line of the song" },
        { start: 4, value: "Second line, a little longer" },
        { start: 8, value: "Third line of the song" },
        { start: 12, value: "Fourth line, and the last one" },
      ],
    };
    player.position = 0;
    player.lyricsOpen = true;
  }

  $effect(() => {
    if (session.client) library.attach(session.client);
  });

  /* Kept copies belong to the account that is signed in: the folder's index is per device, but
     the songs in it are one server's ids, and a different server would read the wrong ones. So
     the store binds when an account becomes active and lets go when it stops being one. */
  $effect(() => {
    const client = session.client;
    const account = session.active;
    if (!client || !account || !session.native) return;
    const invoke = nativeInvoke();
    if (!invoke) return;
    keep.attach(invoke, account.id);
    player.useLocalSource((id) => keep.sourceFor(id));
    return () => {
      player.useLocalSource(null);
      keep.detach();
    };
  });

  $effect(() => {
    document.body.classList.toggle("fullplayer", fullscreen);
  });

  let restored = false;
  $effect(() => {
    if (restored || preview) return;
    restored = true;
    void session.restore();
  });

  /* The queue and the lyrics are two readings of the same square: the queue slot is drawn over
     the disc, and the lyrics overlay is drawn inside the disc's column. Opening both left the
     queue's rows printing through the words — "A starred song Alex Ross" across the second
     line of the song — and the words' own tools row underneath the queue's header. One of them
     at a time is the only arrangement that can be read. */
  function openQueue(): void {
    if (!fullscreen) fullscreen = true;
    player.closeLyrics();
    queueOpen = true;
  }

  function closeQueue(): void {
    queueOpen = false;
  }

  /* The subtitle's links, from either surface. The host switches to the library tab and opens the
     screen; here the shell draws whichever detail the library holds, so opening it is enough. */
  function openArtist(id: string): void {
    void library.openArtist(id);
  }

  function openAlbum(id: string): void {
    void library.openAlbum(id);
  }

  function toggleLyrics(): void {
    if (!fullscreen) fullscreen = true;
    queueOpen = false;
    player.toggleLyrics();
  }

  /* Escape peels one layer at a time, as the host's single document listener does: the queue sheet
     first, then the fullscreen player. It lives here because this is the only place that holds
     both states. Two listeners sharing the key is a race decided by registration order — the
     player's listener is registered when the player opens and the queue's when the sheet opens,
     so pressing Escape after `#qgrip` opened both (queue's listener first) closed both in one
     press, while the same key after `#pfqueue` peeled one layer. One reader, one decision. */
  $effect(() => {
    const escape = (event: KeyboardEvent): void => {
      if (event.key !== "Escape") return;
      if (queueOpen) {
        queueOpen = false;
        return;
      }
      if (!fullscreen) return;
      fullscreen = false;
      player.closeLyrics();
    };
    window.addEventListener("keydown", escape);
    return () => window.removeEventListener("keydown", escape);
  });
</script>

{#if preview === "compact"}
  <CompactPlayer
    {player}
    onqueue={() => {}}
    onopen={() => {}}
    onlyrics={toggleLyrics}
    saveSlot={params.get("save") === "1"}
  />
{:else if preview === "fullscreen" || preview === "queue" || preview === "lyrics"}
  <div class="app-surface" inert={fullscreen}>
    <CompactPlayer
      {player}
      onqueue={() => (queueOpen = true)}
      onopen={() => (fullscreen = true)}
      onlyrics={toggleLyrics}
      saveSlot={params.get("save") === "1"}
    />
  </div>
  <FullPlayer
    {player}
    open={fullscreen}
    onclose={() => {
      fullscreen = false;
      queueOpen = false;
      player.closeLyrics();
    }}
    {queueOpen}
    onqueue={() => (queueOpen ? closeQueue() : openQueue())}
    onlyrics={toggleLyrics}
  />
{:else if !ready}
  <Connect {session} />
{:else}
  <!-- The dialog covers this surface, so it also takes the keyboard out of it: `inert` is what
       makes `aria-modal` true rather than decorative. `display: contents` keeps the wrapper from
       adding a box, so nothing about the layout moves. -->
  <div class="app-surface" inert={fullscreen}>
    <Shell
      {library}
      {player}
      {keep}
      serverLabel={session.active?.label ?? "Server"}
      onsignout={() => void session.signOut()}
    />
    <CompactPlayer
      {player}
      onqueue={openQueue}
      onopen={() => (fullscreen = true)}
      onlyrics={toggleLyrics}
      onartist={openArtist}
      onalbum={openAlbum}
    />
  </div>
  <FullPlayer
    {player}
    open={fullscreen}
    onclose={() => {
      fullscreen = false;
      queueOpen = false;
      player.closeLyrics();
    }}
    {queueOpen}
    onqueue={() => (queueOpen ? closeQueue() : openQueue())}
    onlyrics={toggleLyrics}
    onartist={openArtist}
    onalbum={openAlbum}
  />
{/if}

<style>
  /* The covered surface. `display: contents` is the point: the wrapper takes part in none of the
     page's boxes, so the shell and the docked bar keep the positions they had, while `inert`
     still applies to everything inside it. */
  .app-surface {
    display: contents;
  }
</style>
