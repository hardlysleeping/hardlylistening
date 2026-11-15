<script lang="ts">
  /* One album card: the host's `.songcard.acard` and its children, with this product's actions.
   *
   * The host's card carries a save chip, a vote row, and a download pair; a Subsonic client
   * opens the album and plays it. The cover, the title line, the artist line and the song count
   * are the same elements, so `library-cards.css` lays them out the same way.
   */
  import type { Library } from "../lib/library/store.svelte";
  import type { Album } from "../lib/subsonic";
  import Cover from "./Cover.svelte";

  interface Props {
    library: Library;
    album: Album;
    onopen: () => void;
  }

  let { library, album, onopen }: Props = $props();

  const count = $derived(album.songCount ?? album.song?.length ?? 0);
</script>

<!-- The host's card is a div with a click handler; keeping the element keeps the grid. The
     keyboard path is what makes it a control rather than a picture. -->
<div
  class="songcard acard"
  role="button"
  tabindex="0"
  aria-label={album.name}
  onclick={() => onopen()}
  onkeydown={(event) => {
    if (event.key === "Enter" || event.key === " ") {
      event.preventDefault();
      onopen();
    }
  }}
>
  <div class="thumb">
    <Cover {library} coverArt={album.coverArt} label={album.name} />
  </div>
  <div class="st"><span class="sttext"><b>{album.name}</b></span></div>
  <div class="sartist">{album.artist ?? ""}</div>
  <div class="salbum">{count} song{count === 1 ? "" : "s"}</div>
</div>