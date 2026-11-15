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
   */
  interface Props {
    artist?: string;
    album?: string;
    artistId?: string;
    albumId?: string;
    onartist?: (id: string, name: string) => void;
    onalbum?: (id: string, name: string) => void;
  }

  let { artist = "", album = "", artistId, albumId, onartist, onalbum }: Props = $props();
</script>

<!-- `href="#"` on both links is the host's own markup (`paintPlayerSub()` builds the same
     anchors); the click is cancelled there too. Svelte's a11y rule reads a bare `#` as an
     invalid link target, which is exactly what it is — the element is here for its styling and
     its click, and the real destination is the artist/album screen, behind the callbacks. -->
<!-- svelte-ignore a11y_invalid_attribute -->
{#if artist}<a class="psub-a" href="#" title={`Open ${artist}`} onclick={(event) => { event.preventDefault(); event.stopPropagation(); if (artistId) onartist?.(artistId, artist); }}>{artist}</a>{/if}{#if album}<span class="psub-sep">{artist ? " · " : ""}</span><!-- svelte-ignore a11y_invalid_attribute -->
{#if albumId}<a class="psub-b" href="#" title={`Open ${album}`} onclick={(event) => { event.preventDefault(); event.stopPropagation(); onalbum?.(albumId, album); }}>{album}</a>{:else}<span class="psub-b">{album}</span>{/if}{/if}