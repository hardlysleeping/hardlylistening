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
    /** The album the player is on, so the wall can say which one that is. */
    playing?: boolean;
    onopen: () => void;
  }

  let { library, album, playing = false, onopen }: Props = $props();

  const count = $derived(album.songCount ?? album.song?.length ?? 0);
</script>

<!-- The host's card is a div with a click handler; keeping the element keeps the grid. The
     keyboard path is what makes it a control rather than a picture. -->
<div
  class="songcard acard"
  class:playing={playing}
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
    <!-- The host's card marks the album that is playing, in the corner of its cover. The player
         carried the row's version of this and not the card's, so a wall never said which album
         the docked bar belonged to. -->
    {#if playing}
      <span class="nowbars" aria-hidden="true">❚❚</span>
    {/if}
  </div>
  <div class="st"><span class="sttext"><b>{album.name}</b></span></div>
  <div class="sartist">{album.artist ?? ""}</div>
  <div class="salbum">{count} song{count === 1 ? "" : "s"}</div>
</div>

<style>
  /* The host's chip holds a 14 px SVG; this card holds the same glyph the row does, and the
     card's type is 34 px, so without a size of its own the bars overflowed the 24 px box. */
  .thumb .nowbars {
    font-size: 10px;
    line-height: 1;
  }
</style>
