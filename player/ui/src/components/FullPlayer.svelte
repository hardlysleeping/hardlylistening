<script lang="ts">
  /* The fullscreen player: the host's `#player-full`, with the controls this product has.
   *
   * The record, the stage sizing, the pull-to-dismiss geometry, the foot row's three groups, the
   * centred volume row and the lyrics line under it are the host's, unchanged — `player.css` is
   * imported as it is, so the numbers in it (a stage of
   * `min(86vw,520px,54vh,calc(100vh - 360px))`, the 55 px foot glyphs, the 44 px heart box) are
   * the record's numbers.
   *
   * What is not here is what the host puts there and this client does not own: cue pads, the
   * equalizer, AutoDJ, and the "add to my library" chip. Those write to a HardlyListening
   * catalogue. The Lyrics reading stays because a Subsonic server can answer it.
   */
  import { tick } from "svelte";
  import { SVG } from "../lib/ui/icons";
  import type { PlayerStore } from "../lib/player/store.svelte";
  import QueuePanel from "./QueuePanel.svelte";
  import Subtitle from "./Subtitle.svelte";
  import LyricsOverlay from "./LyricsOverlay.svelte";

  interface Props {
    player: PlayerStore;
    open: boolean;
    onclose: () => void;
    queueOpen: boolean;
    onqueue: () => void;
    /**
     * Opens or closes the reading. It comes from the page rather than the store because the
     * queue and the words share one square: the queue slot is drawn over the disc and the
     * overlay inside it, so whichever opens must close the other.
     */
    onlyrics: () => void;
    /** The subtitle's two links open a screen; the host leaves this surface first. */
    onartist?: (id: string, name: string) => void;
    onalbum?: (id: string, name: string) => void;
  }

  let { player, open, onclose, queueOpen, onqueue, onlyrics, onartist, onalbum }: Props = $props();

  let seeking = $state(false);
  let seekAt = $state(0);

  const title = $derived(player.track?.title ?? "—");
  const artist = $derived(player.track?.artist ?? "");
  const album = $derived(player.track?.album ?? "");

  function clock(seconds: number): string {
    if (!Number.isFinite(seconds) || seconds <= 0) return "0:00";
    const total = Math.floor(seconds);
    return `${Math.floor(total / 60)}:${String(total % 60).padStart(2, "0")}`;
  }

  const shownPosition = $derived(seeking ? seekAt * player.duration : player.position);
  const seekValue = $derived(
    seeking ? Math.round(seekAt * 1000) : Math.round(player.progress * 1000),
  );

  const repeatLabel = $derived(
    player.repeat === "one" ? "Repeat one" : player.repeat === "all" ? "Repeat queue" : "Repeat off",
  );

  async function close(): Promise<void> {
    onclose();
  }

  /* ---- the queue in the column ---------------------------------------------------------
   *
   * The host's `showFullQueue()` does three things, and all three are geometry rather than
   * looks: the sheet is placed from the header's own rectangle (`placeSheet`), the
   * `queue-open` class is added a frame later so the slide has a start value, and the list is
   * capped to the space under the now-playing line. The numbers below are the host's numbers,
   * including the 1/64 px snap — a `.5` tie resolved two ways is a panel that sits a pixel
   * differently from one open to the next, which the baselines would then report forever.
   */

  let full = $state<HTMLDivElement>();
  let slot = $state<HTMLDivElement>();
  let textBlock = $state<HTMLDivElement>();
  /** The class that actually moves the column. Set one frame after the measurement. */
  let landed = $state(false);
  let slotTop = $state<string | null>(null);
  let slotBottom = $state<string | null>(null);
  let headTravel = $state(0);

  /* A surface that declares `aria-modal` has to behave like a modal. Nothing moved focus onto it
   * when it opened, so the keyboard stayed on the docked bar behind an opaque screen and Tab had
   * to walk the bar's thirteen controls before the close button came into reach; closing dropped
   * focus to the document body, losing the reader's place entirely. The page marks the rest of the
   * app `inert` while this is open, so Tab cannot leave; this puts the focus in and gives it back.
   * `preventScroll` keeps the sheet's own placement from jumping on the focus call. */
  let returnTo: HTMLElement | null = null;

  $effect(() => {
    if (!open) return;
    returnTo = document.activeElement instanceof HTMLElement ? document.activeElement : null;
    full?.focus({ preventScroll: true });
    return () => {
      /* Handing focus back has to wait for the `inert` to come off the shell, or the browser
       * refuses it and the reader lands on the body — the state this replaced. */
      const target = returnTo;
      returnTo = null;
      void tick().then(() => {
        if (target?.isConnected) target.focus({ preventScroll: true });
      });
    };
  });

  /** The record's own keys, as the host binds them: a second a press, ten with Shift, and the
   * two ends. Here it is a `role="slider"` with a focus ring, so a control that announced itself
   * as a slider and then ignored every key was the worst of both. */
  function discKeys(event: KeyboardEvent): void {
    const duration = player.duration;
    if (!duration) return;
    const step = event.shiftKey ? 10 : 1;
    let seconds: number | null = null;
    if (event.key === "ArrowRight") seconds = player.position + step;
    else if (event.key === "ArrowLeft") seconds = player.position - step;
    else if (event.key === "Home") seconds = 0;
    else if (event.key === "End") seconds = duration;
    else return;
    event.preventDefault();
    player.seekTo(Math.max(0, Math.min(duration, seconds)) / duration);
  }

  const snap = (value: number) => Math.round(value * 64) / 64;

  function placeSheet(): void {
    if (!slot) return;
    const body = full?.querySelector(".pf-body");
    if (!body) return;

    const chevron = document.getElementById("pfclose");
    const seek = document.getElementById("pfseek");
    const targetTop = snap(chevron ? chevron.getBoundingClientRect().bottom : body.getBoundingClientRect().top) + 6;
    const text = textBlock?.getBoundingClientRect();
    const travel = text ? Math.max(0, Math.round(snap(text.top) - targetTop)) : 40;
    headTravel = travel;

    const base = text ? snap(text.bottom) - snap(body.getBoundingClientRect().top) : 100;
    slotTop = `${Math.round(base - travel + 2)}px`;
    slotBottom = seek
      ? `${Math.round(snap(body.getBoundingClientRect().bottom) - snap(seek.getBoundingClientRect().top) + 8)}px`
      : "0px";
  }

  function capList(): void {
    const details = document.getElementById("playback-details");
    const items = document.getElementById("playback-items");
    const now = document.getElementById("playback-now");
    if (!details || !items) return;
    const used = (now ? snap(now.getBoundingClientRect().height) : 0) + 56;
    items.style.maxHeight = `${Math.max(120, Math.round(snap(details.getBoundingClientRect().height) - used))}px`;
  }

  $effect(() => {
    if (!open || !queueOpen) {
      landed = false;
      headTravel = 0;
      slotTop = null;
      slotBottom = null;
      const items = document.getElementById("playback-items");
      if (items) items.style.maxHeight = "";
      return;
    }

    let cancelled = false;
    void tick().then(() => {
      if (cancelled) return;
      // Measured in the closed column, exactly as the host measures it.
      placeSheet();
      requestAnimationFrame(() => {
        if (cancelled) return;
        landed = true;
        requestAnimationFrame(() => {
          if (!cancelled) capList();
        });
      });
    });

    return () => {
      cancelled = true;
    };
  });
</script>

<div
  id="player-full"
  class:on={open}
  class:queue-open={landed}
  role="dialog"
  aria-modal="true"
  aria-label="Now playing"
  tabindex="-1"
  bind:this={full}
>
  <button class="pf-tab" id="pfclose" type="button" aria-label="Close fullscreen player" title="Close" onclick={() => void close()}>⌄</button>
  <div class="pf-body">
    <div class="pf-top" id="pf-top">
      <div class="pf-stage">
        <div class="pf-disc-shell">
          <div
            id="pf-disc"
            class="pf-disc"
            class:spinning={player.playing}
            role="slider"
            tabindex="0"
            aria-label="Scrub playback position"
            onkeydown={discKeys}
            aria-valuemin="0"
            aria-valuemax="1000"
            aria-valuenow={seekValue}
          >
            <span class="pf-grooves" aria-hidden="true"></span>
            <!-- The label is the big artwork; an empty one is hidden rather than left to paint
                 its alt text across the record, which is what the host does in
                 `paintFullPlayer()` (`visibility` on the same test). -->
            <img
              id="pfart"
              alt="Album art on the record label"
              src={player.cover ?? ""}
              style:visibility={player.cover ? "visible" : "hidden"}
            />
            <span class="pf-spindle" aria-hidden="true"></span>
          </div>
        </div>
        <LyricsOverlay
          lyrics={player.lyrics}
          open={player.lyricsOpen}
          current={player.lyricsLine}
          onclose={() => player.closeLyrics()}
        />
      </div>
      <div class="pf-text" bind:this={textBlock} style={headTravel ? `transform:translateY(-${headTravel}px)` : ""}>
        <div class="pf-title" id="pftitle">{title}</div>
        <div class="pf-sub" id="pfsub"><Subtitle
          {artist}
          {album}
          artistId={player.track?.artistId}
          albumId={player.track?.albumId}
          onartist={(id, name) => {
            /* The host's `openArtistFromPlayer` closes the fullscreen player first; navigating
               underneath it looked like a tap that did nothing. */
            onclose();
            onartist?.(id, name);
          }}
          onalbum={(id, name) => {
            onclose();
            onalbum?.(id, name);
          }}
        /></div>
        <!-- A stream that fails used to fail silently: the engine reported it, the store kept
             it, and nothing drew it, so the only sign was a play button that did nothing. -->
        {#if player.error}
          <p class="pf-error" id="pferror" role="status">{player.error}</p>
        {/if}
      </div>
    </div>

    <div
      class="pf-queue-slot"
      id="pf-queue-slot"
      bind:this={slot}
      style={queueOpen
        ? `display:flex;${slotTop ? `--pf-slot-top:${slotTop};` : ""}${slotBottom ? `bottom:${slotBottom}` : ""}`
        : ""}
    >
      <QueuePanel player={player} open={queueOpen} onclose={() => onqueue()} />
    </div>

    <input
      class="pf-seek"
      type="range"
      id="pfseek"
      min="0"
      max="1000"
      value={seekValue}
      aria-label="Seek"
      oninput={(event) => {
        seeking = true;
        seekAt = Number(event.currentTarget.value) / 1000;
      }}
      onchange={(event) => {
        // `change`, not `pointerup`: a range input changed from the keyboard (arrows, Home,
        // End, Page Up) commits through `change` and never sees a pointer event at all.
        player.seekTo(Number(event.currentTarget.value) / 1000);
        seeking = false;
      }}
    />
    <div class="pf-times">
      <span id="pftime">{clock(shownPosition)}</span>
      <span id="pfdur">{clock(player.duration)}</span>
    </div>
    <div class="pf-ctrl">
      <button class="iconbtn small" id="pfshuffle" type="button" title="Shuffle" aria-label="Shuffle" class:on={player.shuffle} onclick={() => player.toggleShuffle()}>{@html SVG.shuffle(16)}</button>
      <button class="iconbtn" id="pfprev" type="button" title="Previous" aria-label="Previous" onclick={() => void player.previous()}>{@html SVG.prev(20)}</button>
      <button class="iconbtn big" id="pfplay" type="button" title={player.playing ? "Pause" : "Play"} aria-label={player.playing ? "Pause" : "Play"} onclick={() => void player.toggle()}>{@html player.playing ? SVG.pause(26) : SVG.play(26)}</button>
      <button class="iconbtn" id="pfnext" type="button" title="Next" aria-label="Next" onclick={() => void player.next()}>{@html SVG.next(20)}</button>
      <button class="iconbtn small" id="pfrepeat" type="button" title={repeatLabel} aria-label="Repeat" class:on={player.repeat !== "off"} aria-pressed={player.repeat !== "off"} onclick={() => player.cycleRepeat()}>{@html player.repeat === "one" ? SVG.repeatOne(16) : SVG.repeat(16)}</button>
    </div>
    <div class="pf-foot">
      <div class="pf-side pf-start"></div>
      <button
        type="button"
        class="iconbtn"
        id="pfqueue"
        title="Playback queue"
        aria-label="Playback queue"
        aria-expanded={queueOpen}
        class:on={queueOpen}
        onclick={() => onqueue()}>☰</button>
      <div class="pf-side pf-end">
        <button type="button" class="iconbtn" id="pflike" title="Like song" aria-label="Like song" aria-pressed={player.liked} class:on={player.liked} onclick={() => void player.toggleLike()}>{player.liked ? "♥" : "♡"}</button>
      </div>
    </div>
    <!-- The filled part of the track is the `--vol` stop the stylesheet reads; the host paints
         it from the audio element itself (`paintVolume()`), and mute counts as zero without
         losing the level. -->
    <div class="pf-vol">
      <svg width="17" height="17" viewBox="0 0 24 24" fill="currentColor" aria-hidden="true"><path d="M4 9v6h3.5L13 19V5L7.5 9H4Z"/><path d="M16 8.5a5 5 0 0 1 0 7" fill="none" stroke="currentColor" stroke-width="1.8" stroke-linecap="round"/></svg>
      <input
        type="range"
        id="pfvol"
        min="0"
        max="100"
        step="1"
        value={Math.round(player.volume * 100)}
        style={`--vol:${Math.round((player.muted ? 0 : player.volume) * 100)}%`}
        title="Volume"
        aria-label="Volume"
        oninput={(event) => player.setVolume(Number(event.currentTarget.value) / 100)}
      />
    </div>
    <!-- The reading's own line, under the volume, as in the host: a secondary action, not a
         fifth control in the foot. -->
    <div class="pf-lyrics">
      <button
        type="button"
        id="pflyrics"
        class:lyrics-ready={!!player.lyrics}
        disabled={!player.lyrics}
        aria-disabled={!player.lyrics}
        title="Lyrics"
        onclick={() => onlyrics()}>Lyrics</button>
    </div>
  </div>
</div>

<style>
  /* The host's record answers an angular drag; this client never carried that gesture, so the
     `grab` cursor in `player.css` promised a control the pointer cannot move. The record is still
     a keyboard slider (`Arrow`, `Shift+Arrow`, `Home`, `End`) and the seek bar under it takes the
     pointer. `touch-action` stays as it is: it is the disc shell's, and it keeps a swipe on the
     record from scrolling the page behind it. */
  #player-full .pf-disc {
    cursor: default;
  }

  /* Under the title, and only when there is something to say: the visual baselines are captured
     without one, so this adds no pixel to the surfaces they pin. */
  .pf-error {
    margin: 6px 0 0;
    font-size: 12.5px;
    line-height: 1.4;
    color: var(--danger, #ff7a7a);
  }
</style>
