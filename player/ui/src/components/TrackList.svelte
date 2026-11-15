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
  }

  let { songs, currentId = null, playing = false, onplay, onqueue }: Props = $props();

  function clock(seconds: number | undefined): string {
    if (!seconds || !Number.isFinite(seconds)) return "";
    const total = Math.floor(seconds);
    return `${Math.floor(total / 60)}:${String(total % 60).padStart(2, "0")}`;
  }
</script>

<div class="songtable">
  {#each songs as song, index (song.id)}
    {@const on = song.id === currentId}
    <div
      class="songrow"
      class:playing={on}
      role="button"
      tabindex="0"
      aria-label={song.title}
      onclick={() => onplay(index)}
      onkeydown={(event) => {
        if (event.key === "Enter" || event.key === " ") {
          event.preventDefault();
          onplay(index);
        }
      }}
    >
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
    </div>
  {/each}
</div>