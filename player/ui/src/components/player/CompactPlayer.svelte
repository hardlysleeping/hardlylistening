<script lang="ts">
  /* The compact bar: the host page's `#player`, ported mark for mark.
   *
   * The markup is deliberately identical to `web/index.html` — same ids, same order, same
   * attributes — because `web/styles/components/player.css` is the authority on how it looks,
   * and it is loaded as-is. Every number in that file (112 px tall, the 56 px artwork, the
   * 49 px scrub rectangle, the 14/16/20 px glyphs) is a measurement the visual baselines pin.
   * Changing the markup here is a visual change.
   *
   * What changed is underneath: no `paintPlayer()` writing `textContent` into five nodes, no
   * global `fetch`, no hidden host controls. State comes in from the store.
   */
  import { SVG } from "../../lib/ui/icons";
  import { drawCompactWave, waveSurface, DEFAULT_WAVE_CONFIG } from "../../lib/player/wave";
  import type { PlayerStore } from "../../lib/player/store.svelte";
  import Subtitle from "../Subtitle.svelte";
  import { fitMarquee, MARQUEE_BAR } from "../../lib/ui/marquee";

  interface Props {
    player: PlayerStore;
    onqueue: () => void;
    onopen: () => void;
    onlyrics: () => void;
    /**
     * The host's "add to library" chip. It is `false` in the public player: that control writes
     * to a HardlyListening catalogue, which this client does not have. The slot is kept behind a
     * prop so the layout can be proved identical to the baseline while the shipped player
     * carries only its own controls.
     */
    saveSlot?: boolean;
    /** The artist · album line is two links on the host, and each opens a screen here too. */
    onartist?: (id: string, name: string) => void;
    onalbum?: (id: string, name: string) => void;
  }

  let { player, onqueue, onopen, onlyrics, saveSlot = false, onartist, onalbum }: Props = $props();

  let titleEl = $state<HTMLDivElement>();
  let canvas: HTMLCanvasElement | undefined = $state();
  let volumeButton: HTMLButtonElement | undefined = $state();
  let volumeMenu: HTMLDivElement | undefined = $state();
  let volumeOpen = $state(false);
  let volumeLeft = $state(0);
  let volumeBottom = $state(0);

  /* A held finger owns the playhead: the audio element only reports a seek once it lands, so the
     drawing follows the control while it is down. Same rule as the host's `markScrub`. */
  let scrubbing = $state(false);
  let scrubAt = $state(0);

  const surface = $derived(canvas ? waveSurface(canvas) : undefined);

  function clock(seconds: number): string {
    if (!Number.isFinite(seconds) || seconds <= 0) return "0:00";
    const total = Math.floor(seconds);
    const minutes = Math.floor(total / 60);
    const rest = total % 60;
    return `${minutes}:${String(rest).padStart(2, "0")}`;
  }

  const progress = $derived(player.progress);
  const seekValue = $derived(scrubbing ? Math.round(scrubAt * 1000) : Math.round(progress * 1000));
  const title = $derived(player.track?.title ?? "—");
  const artist = $derived(player.track?.artist ?? "");
  const album = $derived(player.track?.album ?? "");

  function draw(): void {
    if (!canvas || !surface) return;
    drawCompactWave(
      canvas,
      {
        progress,
        scrubbing,
        scrubAt,
        envelope: null,
      },
      DEFAULT_WAVE_CONFIG,
      surface,
    );
  }

  /* Redraw whenever the playhead, the scrub or the bar's width changes. */
  $effect(() => {
    void progress;
    void scrubbing;
    void scrubAt;
    draw();
  });

  $effect(() => {
    if (!canvas) return;
    const observer = new ResizeObserver(() => draw());
    observer.observe(canvas);
    return () => observer.disconnect();
  });

  function placeVolume(): void {
    if (!volumeButton || !volumeMenu) return;
    const button = volumeButton.getBoundingClientRect();
    const size = volumeMenu.getBoundingClientRect();
    const pad = 8;
    volumeLeft = Math.round(
      Math.min(
        Math.max(button.left + button.width / 2 - size.width / 2, pad),
        Math.max(pad, window.innerWidth - pad - size.width),
      ),
    );
    volumeBottom = Math.round(window.innerHeight - button.top + 10);
  }

  function toggleVolume(event: MouseEvent): void {
    event.stopPropagation();
    const open = !volumeOpen;
    if (open) placeVolume();
    volumeOpen = open;
  }

  $effect(() => {
    if (!volumeOpen) return;
    const close = (event: Event) => {
      if (volumeMenu && event.target instanceof Node && !volumeMenu.contains(event.target)) {
        volumeOpen = false;
      }
    };
    const onKey = (event: KeyboardEvent) => {
      if (event.key === "Escape") volumeOpen = false;
    };
    const onResize = () => {
      volumeOpen = false;
    };
    document.addEventListener("click", close);
    document.addEventListener("keydown", onKey);
    window.addEventListener("resize", onResize);
    return () => {
      document.removeEventListener("click", close);
      document.removeEventListener("keydown", onKey);
      window.removeEventListener("resize", onResize);
    };
  });

  /* The bar's title is the host's marquee line: it scrolls in place when the track name is wider
     than the box, and stays a plain one-line title when it is not. `fitMarquee` owns the text
     because the decision is a measurement, not a render. */
  $effect(() => {
    fitMarquee(titleEl, title, MARQUEE_BAR);
  });

  const repeatLabel = $derived(
    player.repeat === "one" ? "Repeat one" : player.repeat === "all" ? "Repeat queue" : "Repeat off",
  );
</script>

<div
  id="player"
  class:on={!!player.track}
  class:has-art={!!player.cover}
  class:loading={player.loading}
>
  <button type="button" class="qgrip" id="qgrip" aria-label="Show or hide the queue" title="Show or hide the queue" onclick={() => onqueue()}><span aria-hidden="true">▲</span></button>
  <button
    type="button"
    class="iconbtn small"
    id="pllike"
    title="Like song"
    aria-label="Like song"
    aria-pressed={player.liked}
    class:on={player.liked}
    onclick={() => void player.toggleLike()}>{player.liked ? "♥" : "♡"}</button>
  {#if saveSlot}
    <span class="plsave" id="plsave"></span>
  {/if}
  <button class="plartwrap" id="plartwrap" title="Open album" onclick={() => onopen()}>
    <img id="plart" alt="" src={player.cover ?? ""} style:visibility={player.cover ? "visible" : "hidden"} />
  </button>
  <div
    class="pmeta"
    id="plmeta"
    title="Open fullscreen player"
    role="button"
    tabindex="0"
    onclick={() => onopen()}
    onkeydown={(event) => {
      /* The links under the title are children of this block, and they are the only way a
         keyboard reaches the artist and the release from here. `preventDefault` on the keys they
         own cancelled their activation and opened the fullscreen player instead — the one place
         the link must not go. */
      if (event.target !== event.currentTarget) return;
      if (event.key === "Enter" || event.key === " ") {
        event.preventDefault();
        onopen();
      }
    }}
  >
    <div class="ptitle" id="pltitle" bind:this={titleEl}></div>
    <div class="psub" id="plsub"><Subtitle variant="bar" {artist} {album} artistId={player.track?.artistId} albumId={player.track?.albumId} {onartist} {onalbum} /></div>
  </div>
  <div class="pctrl">
    <div class="ptrans">
      <button class="iconbtn small" id="plrepeat" title={repeatLabel} aria-label="Repeat" class:on={player.repeat !== "off"} aria-pressed={player.repeat !== "off"} onclick={() => player.cycleRepeat()}>{@html player.repeat === "one" ? SVG.repeatOne(16) : SVG.repeat(16)}</button>
      <button class="iconbtn small" id="plshuffle" title="Shuffle" aria-label="Shuffle" aria-pressed={player.shuffle} class:on={player.shuffle} onclick={() => player.toggleShuffle()}>{@html SVG.shuffle(16)}</button>
      <button class="iconbtn" id="plprev" title="Previous" aria-label="Previous" onclick={() => void player.previous()}>{@html SVG.prev(20)}</button>
      <button class="iconbtn big" id="plplay" title={player.playing ? "Pause" : "Play"} aria-label={player.playing ? "Pause" : "Play"} onclick={() => void player.toggle()}>{@html player.playing ? SVG.pause(14) : SVG.play(14)}</button>
      <button class="iconbtn" id="plnext" title="Next" aria-label="Next" onclick={() => void player.next()}>{@html SVG.next(20)}</button>
      <button class="iconbtn small" id="plmute" title="Mute" aria-label="Mute" aria-pressed={player.muted} onclick={() => player.toggleMute()}>{@html player.muted ? SVG.mute(16) : SVG.volume(16)}</button>
      <button
        class="iconbtn small"
        id="plvolmore"
        bind:this={volumeButton}
        title="Volume"
        aria-label="Volume"
        aria-haspopup="dialog"
        aria-expanded={volumeOpen}
        aria-controls="plvolmenu"
        class:on={volumeOpen}
        onclick={toggleVolume}>{@html SVG.volumeUp(16)}</button>
    </div>
    <div class="pend">
      <button
        type="button"
        id="pllyrics"
        class:lyrics-ready={!!player.lyrics}
        title="Lyrics"
        disabled={!player.lyrics}
        aria-disabled={!player.lyrics}
        onclick={() => onlyrics()}>Lyrics</button>
    </div>
  </div>
  <span class="ptime" id="pltime">{clock(player.position)}</span>
  <canvas class="plwave-seek" aria-hidden="true" bind:this={canvas}></canvas>
  <input
    class="pseek"
    type="range"
    id="plseek"
    min="0"
    max="1000"
    value={seekValue}
    aria-label="Seek"
    oninput={(event) => {
      scrubbing = true;
      scrubAt = Number(event.currentTarget.value) / 1000;
    }}
    onpointerdown={() => {
      scrubbing = true;
      scrubAt = progress;
    }}
    onchange={(event) => {
      // Same as the full player: the keyboard's arrow keys change a range input without any
      // pointer event, so only `change` can honour them.
      player.seekTo(Number(event.currentTarget.value) / 1000);
      scrubbing = false;
    }}
    onpointercancel={() => {
      scrubbing = false;
    }}
  />
  <span class="ptime" id="pldur">{clock(player.duration)}</span>
</div>

<div
  class="plvol-menu"
  class:open={volumeOpen}
  id="plvolmenu"
  role="dialog"
  aria-label="Volume"
  bind:this={volumeMenu}
  style="left:{volumeLeft}px;bottom:{volumeBottom}px"
>
  <input
    type="range"
    id="plvolv"
    min="0"
    max="100"
    step="1"
    value={Math.round(player.volume * 100)}
    title="Volume"
    aria-label="Volume"
    oninput={(event) => player.setVolume(Number(event.currentTarget.value) / 100)}
  />
</div>