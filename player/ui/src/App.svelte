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
  import { nativeInvoke, type Song } from "./lib/subsonic";
  import { nativeListen } from "./lib/player/native-engine";
  import { useNativeAudioIfPresent } from "./lib/player/select";
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

  // A phone gets the platform's player, so playback survives the lock screen; a browser and a
  // desktop build get the WebView's. Asked once, and the answer decides — see `select.ts`.
  void useNativeAudioIfPresent(player, nativeInvoke(), nativeListen());

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

  function toggleLyrics(): void {
    if (!fullscreen) fullscreen = true;
    queueOpen = false;
    player.toggleLyrics();
  }
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
  <CompactPlayer
    {player}
    onqueue={() => (queueOpen = true)}
    onopen={() => (fullscreen = true)}
    onlyrics={toggleLyrics}
    saveSlot={params.get("save") === "1"}
  />
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
  <Shell
    {library}
    {player}
    serverLabel={session.active?.label ?? "Server"}
    onsignout={() => void session.signOut()}
  />
  <CompactPlayer
    {player}
    onqueue={openQueue}
    onopen={() => (fullscreen = true)}
    onlyrics={toggleLyrics}
  />
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
{/if}