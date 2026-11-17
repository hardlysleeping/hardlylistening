<script lang="ts">
  /* One album cover, resolved through the client's transport.
   *
   * The markup and the fallback are the host's: an image when the server has one, and the
   * tile's own gradient with initials when it does not — a cover that fails to load must read
   * as a release without artwork, not as a broken image.
   */
  import type { Library } from "../lib/library/store.svelte";

  interface Props {
    library: Library;
    coverArt: string | undefined;
    label: string;
    size?: number;
  }

  let { library, coverArt, label, size = 400 }: Props = $props();

  let url = $state<string | null>(null);
  let failed = $state(false);

  function hue(text: string): number {
    let h = 0;
    for (const ch of text) h = (h * 31 + ch.charCodeAt(0)) % 360;
    return h;
  }

  function initials(text: string): string {
    return (
      String(text || "?")
        .trim()
        .split(/\s+/)
        .slice(0, 2)
        .map((word) => word[0] ?? "")
        .join("") || "?"
    ).toUpperCase();
  }

  const gradient = $derived(`linear-gradient(140deg,hsl(${hue(label)} 42% 26%),hsl(${(hue(label) + 48) % 360} 38% 16%))`);

  $effect(() => {
    let alive = true;
    failed = false;
    if (!coverArt) {
      url = null;
      return;
    }
    void library
      .serverCover(coverArt, size)
      .then((resolved) => {
        if (alive) url = resolved;
      })
      .catch(() => {
        if (alive) url = null;
      });
    return () => {
      alive = false;
    };
  });
</script>

<div class="acover" style={url && !failed ? "" : `background:${gradient}`}>
  {#if url && !failed}
    <img
      class="cover"
      src={url}
      alt=""
      draggable="false"
      loading="lazy"
      onerror={() => {
        failed = true;
      }}
      onload={(event) => {
        /* A server with no artwork can still answer 200 — this one answers a 70-byte 1×1 PNG
           placeholder — so `onerror` never fires and the tile goes blank instead of showing the
           host's gradient and initials. One pixel is not a cover. */
        const img = event.currentTarget as HTMLImageElement;
        if (img.naturalWidth <= 1 || img.naturalHeight <= 1) failed = true;
      }}
    />
  {:else}
    {initials(label)}
  {/if}
</div>

<style>
  /* The tile's own shape.
   *
   * The host styles a cover through its card — `.acard .acover` — because every cover it draws
   * sits inside one. This component is also used where there is no card (the album header, the
   * now-playing bar), and there the element had no box at all: no aspect ratio, no radius, no
   * centring, so the header showed a one-line strip with the initials in the corner. Same tile,
   * stated once, in the component that draws it.
   */
  .acover {
    aspect-ratio: 1 / 1;
    border-radius: 10px;
    overflow: hidden;
    display: grid;
    place-items: center;
    background: var(--panel2);
    color: #cfd3e1;
    font-weight: 700;
    font-size: 22px;
    letter-spacing: -0.02em;
  }

  .acover .cover {
    width: 100%;
    height: 100%;
    object-fit: cover;
    display: block;
  }
</style>
