<script lang="ts">
  /* The lyrics overlay, ported from `web/index.html` + `web/lyrics.js`.
   *
   * Same ids, same tools row, same three readings: a plain file is one `<pre>`, a line-timed
   * file is one `<p>` per line with `.current` / `.next` on the line being sung. The stylesheet
   * is `web/styles/components/lyrics.css`, already part of the bundle, so the look is not
   * re-specified here.
   *
   * What the public player does not carry: the host's Edit button (it writes a correction to
   * the host's own catalogue through `POST /api/track/<id>/lyrics`) and Karaoke word-mode (the
   * host builds it from the editor's word timings). Both were `disabled` in the host until a
   * file with word timings was loaded; here there is no such source, so they are absent rather
   * than present-and-dead. The status line says what is being shown.
   */
  import type { Lyrics } from "../lib/subsonic";
  import { tick } from "svelte";

  interface Props {
    lyrics: Lyrics | null;
    open: boolean;
    /** The line being sung, as an index into `lyrics.line`. `-1` when nothing is timed. */
    current: number;
    onclose: () => void;
  }

  let { lyrics, open, current, onclose }: Props = $props();

  let overlay: HTMLDivElement | undefined = $state();
  let body: HTMLDivElement | undefined = $state();

  const lines = $derived(lyrics?.line ?? []);
  const plain = $derived(lyrics && !lyrics.synced ? lyrics.value ?? "" : "");
  // The host's own wording, from `message()` in `web/lyrics.js`. A Subsonic server sends line
  // timing or nothing (word timing was the host editor's), so those are the two readings.
  const status = $derived(
    !lyrics
      ? ""
      : lyrics.synced
        ? "Synchronized lyrics · line timing, not word timing"
        : "Plain lyrics · synchronized timing unavailable",
  );

  // The line being sung is the one a person needs to see; the host scrolls it to the nearest
  // edge rather than centring it, so the next line is already in view.
  $effect(() => {
    if (!open || current < 0 || !body) return;
    const node = body.querySelectorAll("p")[current];
    if (!node) return;
    void tick().then(() => node.scrollIntoView({ block: "nearest", behavior: "smooth" }));
  });

  function fullscreen(): void {
    if (!overlay) return;
    if (document.fullscreenElement === overlay) void document.exitFullscreen?.().catch(() => {});
    else void overlay.requestFullscreen?.().catch(() => {});
  }
</script>

<div
  id="lyrics-overlay"
  aria-label="Lyrics"
  class:on={open}
  hidden={!open}
  bind:this={overlay}
>
  <div class="lyrics-tools">
    <span id="lyrics-status" role="status">{status}</span>
    <button id="lyrics-fullscreen" type="button" onclick={fullscreen}>Fullscreen</button>
    <button id="lyrics-close" aria-label="Hide lyrics" onclick={() => onclose()}>×</button>
  </div>
  <div id="lyrics-body" bind:this={body}>
    {#if plain}
      <pre>{plain}</pre>
    {:else}
      {#each lines as line, index (index)}
        <p
          class:current={index === current}
          class:next={index === current + 1}
          aria-current={index === current ? "true" : undefined}
        >{line.value}</p>
      {/each}
    {/if}
  </div>
</div>