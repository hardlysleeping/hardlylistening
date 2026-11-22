<script lang="ts">
  /* A list of songs: the host's `.songrow` structure, with this player's actions.
   *
   * What is gone from the host row is everything about acquiring or storing a file — the save
   * chip, the download pair, the offline badge, the vote row. A Subsonic client plays what the
   * server has; there is no second copy to manage.
   */
  import type { Song } from "../lib/subsonic";

  interface Props {
    songs: Song[];
    /** The song the player is on, so the row can read as playing. */
    currentId?: string | null;
    playing?: boolean;
    onplay: (index: number) => void;
    onqueue?: (index: number) => void;
    /** Present only where the list is one member's to edit: a playlist. */
    onremove?: (index: number) => void;
  }

  let { songs, currentId = null, playing = false, onplay, onqueue, onremove }: Props = $props();

  function clock(seconds: number | undefined): string {
    if (!seconds || !Number.isFinite(seconds)) return "";
    const total = Math.floor(seconds);
    return `${Math.floor(total / 60)}:${String(total % 60).padStart(2, "0")}`;
  }
</script>

<div class="songtable" class:has-remove={!!onremove}>
  <!-- No key on the song id: a playlist is allowed to list the same song twice, and a keyed
       each with duplicate keys drops the extra rows rather than drawing them. -->
  {#each songs as song, index}
    {@const on = song.id === currentId}
    <div class="songrow" class:playing={on} data-id={song.id}>
      <!-- The row's whole box is the play target, as it was; the control is now a real button
           stretched under the row's contents rather than a `role="button"` wrapped around them.
           The row it replaces contained the queue `+`, and a widget role makes its children
           presentational in the accessibility tree — the `+` could be announced as part of the
           row's name or not at all, depending on the reader. An empty button with a name of its
           own, sitting under the text, is the same target with no nesting: Enter and Space are
           the browser's own activation, so the `event.target !== event.currentTarget` guard that
           this handler needed is gone with the handler. -->
      <button
        type="button"
        class="row-play"
        aria-label="Play {song.title}"
        onclick={() => onplay(index)}
      ></button>
      <div class="idx" class:playing={on}>
        {#if on}
          <span class="nowbars" aria-hidden="true">{playing ? "❚❚" : "▶"}</span>
        {:else}
          {index + 1}
        {/if}
      </div>
      <div class="st"><span class="sttext"><b>{song.title}</b></span></div>
      <div class="sartist">{song.artist ?? ""}</div>
      <span class="sdur">{clock(song.duration)}</span>
      {#if onqueue}
        <button
          type="button"
          class="snext"
          title="Queue next"
          aria-label="Queue next"
          onclick={(event) => {
            event.stopPropagation();
            onqueue(index);
          }}>+</button>
      {/if}
      {#if onremove}
        <button
          type="button"
          class="sremove"
          title="Remove from this playlist"
          aria-label="Remove {song.title} from this playlist"
          onclick={(event) => {
            event.stopPropagation();
            onremove(index);
          }}>✕</button>
      {/if}
    </div>
  {/each}
</div>

<style>
  /* The row's own columns.
   *
   * `library-row.css` gives `.songrow` the host's eight columns — index, cover, title,
   * artist, album, duration, download pair, mark — because the host row manages a file. This row
   * has five cells, so inheriting that template auto-placed them into the wrong cells: the queue
   * button landed where the cover goes and the title started one column too far right.
   *
   * Naming the columns is not enough on its own. The host's player list re-places the same cells
   * by *area* (`44px 1fr auto`, "art st more") because its row carries a cover and a "more" menu,
   * and switches the index and the duration off. This row has neither, so the areas are switched
   * off here and those two cells are switched back on — otherwise every list of songs drew with an
   * empty cover-sized hole at its left, no index, and no duration. The row's type, padding and
   * borders still come from the host.
   */
  .songtable .songrow {
    grid-template-columns: 32px minmax(0, 2.2fr) minmax(0, 1.3fr) 72px 34px;
    grid-template-areas: none;
  }

  .songtable .songrow .st,
  .songtable .songrow .sartist {
    grid-area: auto;
  }

  .songtable .songrow .idx {
    display: block;
  }

  /* The playing indicator belongs in the index cell.
   *
   * `.nowbars` is the host's chip for a cover tile: absolute, 24 px square, dark background and
   * an accent border. This row is not a tile, and its `.songrow` is positioned, so the chip
   * anchored to the row's bottom-right corner and landed on the queue button — the row read as
   * a covered `+` with an empty index column. The host's own list row puts the bars straight into
   * `.idx` as an inline glyph; this makes the same placement hold here. */
  .songtable .songrow .idx .nowbars {
    position: static;
    width: auto;
    height: auto;
    display: inline-block;
    border: 0;
    border-radius: 0;
    background: none;
    box-shadow: none;
    color: inherit;
    font-size: 10px;
    line-height: 1;
  }

  .songtable .songrow .sdur {
    display: block;
    grid-column: 4;
    grid-row: 1;
  }

  /* The play target fills the row without taking a grid cell. It paints under the row's text and
     under the `+`, which is why that button is given a stacking position below. */
  .songtable .songrow .row-play {
    position: absolute;
    inset: 0;
    padding: 0;
    border: 0;
    border-radius: inherit;
    background: none;
    color: inherit;
    font: inherit;
    cursor: inherit;
  }

  .songtable .songrow .snext {
    position: relative;
    z-index: 1;
    grid-column: 5;
    grid-row: 1;
    width: 30px;
    height: 30px;
    min-width: 0;
    min-height: 0;
    padding: 0;
    border-radius: 50%;
    border: 1px solid var(--line);
    background: var(--panel2);
    color: var(--text);
    line-height: 1;
    cursor: pointer;
  }

  .songtable .songrow .snext:hover {
    border-color: var(--accent);
    color: var(--accent);
  }

  /* A playlist is the member's own list, so its rows carry the remove. It takes a column of its
     own beside the queue button rather than sharing it: two 30 px targets in one cell is one
     mis-tap away from queueing the song somebody meant to remove. */
  .songtable.has-remove .songrow {
    grid-template-columns: 32px minmax(0, 2.2fr) minmax(0, 1.3fr) 72px 34px 34px;
  }

  .songtable .songrow .sremove {
    position: relative;
    z-index: 1;
    grid-column: 6;
    grid-row: 1;
    width: 30px;
    height: 30px;
    min-width: 0;
    min-height: 0;
    padding: 0;
    border-radius: 50%;
    border: 1px solid transparent;
    background: none;
    color: var(--dim);
    font-size: 13px;
    line-height: 1;
    cursor: pointer;
  }

  .songtable .songrow .sremove:hover {
    border-color: var(--line);
    color: var(--text);
  }

  /* On a phone the artist line goes: the title and the duration are what a row is for. */
  @media (max-width: 760px) {
    .songtable .songrow {
      grid-template-columns: 22px minmax(0, 1fr) 46px 30px;
    }

    .songtable.has-remove .songrow {
      grid-template-columns: 22px minmax(0, 1fr) 46px 30px 30px;
    }

    /* Phone columns are one tighter than the desktop's, where the duration is switched off and
       the index is narrower: the queue button takes the fourth cell and the remove the fifth. */
    .songtable.has-remove .songrow .snext {
      grid-column: 4;
    }

    .songtable.has-remove .songrow .sremove {
      grid-column: 5;
    }

    .songtable .songrow .sartist {
      display: none;
    }

    .songtable .songrow .sdur {
      grid-column: 3;
      grid-row: 1;
      font-size: 12px;
    }
  }
</style>
