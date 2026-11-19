<script lang="ts">
  /* The artist · album line under a title, as the host builds it.
   *
   * The host does not set this line as one string: `paintPlayerSub()` appends an `<a>` for the
   * artist, a `.psub-sep` span holding " · ", and then either an `<a>` or a `<span>` for the
   * album. Two reasons, and both matter here.
   *
   * The visible one: the stylesheet lays this container out as flex (`.psub`, `.pf-sub`), and
   * `.psub-a` / `.psub-b` are the elements that shrink and ellipsize. A single text node is one
   * anonymous flex item that cannot ellipsize on its own.
   *
   * The invisible one: element boundaries are shaping boundaries. The same characters set as one
   * text node start a fraction of a layout unit earlier than they do when broken across inline
   * elements, and that fraction is enough to rasterise the middot a pixel differently. The
   * visual baselines were captured from the host's structure, so this is the structure.
   *
   * Each name is written by `fitMarquee`, not by the template: a name wider than its own box
   * scrolls in place on the host, and whether it fits is a measurement. The element is therefore
   * empty in the markup and filled once it has a width — the same order the host paints in.
   */
  import { fitMarquee, resetMarquee, MARQUEE_BAR, MARQUEE_FULL } from "../lib/ui/marquee";

  interface Props {
    artist?: string;
    album?: string;
    artistId?: string;
    albumId?: string;
    onartist?: (id: string, name: string) => void;
    onalbum?: (id: string, name: string) => void;
    /** Which surface is drawing the line: they use different gaps, floors and wrap classes. */
    variant?: "bar" | "full";
    /**
     * Whether the line is on screen. The fullscreen player sets its title and subtitle while it
     * is `display:none`, so a measurement taken then is a measurement of nothing; this is what
     * makes the fit run again at the moment the box first has a width.
     */
    active?: boolean;
  }

  let {
    artist = "",
    album = "",
    artistId,
    albumId,
    onartist,
    onalbum,
    variant = "bar",
    active = true,
  }: Props = $props();

  let artistEl = $state<HTMLElement>();
  let albumEl = $state<HTMLElement>();

  $effect(() => {
    if (!active) return;
    const fit = variant === "full" ? MARQUEE_FULL : MARQUEE_BAR;
    /* Both names share one flex row, so both go back to their plain line before either is
       measured. Measuring the release first while the artist's box was still empty gave it room
       the artist then took: at 1200 px the release fitted, the artist did not, and the release
       ended up 333 px wide for a 753 px name with no marquee. */
    resetMarquee(albumEl, album);
    resetMarquee(artistEl, artist);
    fitMarquee(albumEl, album, fit);
    fitMarquee(artistEl, artist, fit);
    /* The docked bar's extra rule, the host's `artist-only`: a name wider than its own box turns
       the centred row into a left-aligned one, so the scrolling line starts at a visible edge
       instead of being clipped where the centred row began. `rolling` is exactly "this line is
       wider than its box" — the state `fitMarquee` just decided — and reading it here avoids
       measuring a marquee's own two copies as if they were the line. The fullscreen line keeps to
       one arrangement: both names, centred, each scrolling inside its own box. */
    if (variant === "bar" && artistEl) {
      artistEl
        .closest("#plsub")
        ?.classList.toggle("artist-only", artistEl.classList.contains("rolling"));
    }
  });
</script>

<!-- `href="#"` on both links is the host's own markup (`paintPlayerSub()` builds the same
     anchors); the click is cancelled there too. Svelte's a11y rule reads a bare `#` as an
     invalid link target, which is exactly what it is — the element is here for its styling and
     its click, and the real destination is the artist/album screen, behind the callbacks. -->
{#if artist}<!-- svelte-ignore a11y_invalid_attribute -->
{#if artistId}<a class="psub-a" href="#" title={`Open ${artist}`} bind:this={artistEl} onclick={(event) => { event.preventDefault(); event.stopPropagation(); onartist?.(artistId, artist); }}></a>{:else}<span class="psub-a" bind:this={artistEl}></span>{/if}{/if}{#if album}<span class="psub-sep">{artist ? " · " : ""}</span>{#if albumId}<!-- svelte-ignore a11y_invalid_attribute -->
<a class="psub-b" href="#" title={`Open ${album}`} bind:this={albumEl} onclick={(event) => { event.preventDefault(); event.stopPropagation(); onalbum?.(albumId, album); }}></a>{:else}<span class="psub-b" bind:this={albumEl}></span>{/if}{/if}
