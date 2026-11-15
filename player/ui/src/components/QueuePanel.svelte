<script lang="ts">
  /* Up next: the host's `#playback-panel`, in the fullscreen player's queue slot.
   *
   * The heading, the count, the broom, the "now" row and the row itself are the host's markup
   * and classes; `playback-queue.css` is imported by the app's stylesheet entry point. What is
   * gone is the "recently played" drawer, which is a host listening history, and the drag handle,
   * because a reorder that is not wired is a control that lies.
   *
   * `in-full` is the class the host adds when it moves this panel into the column; here the panel
   * has no other home, so it is always on. That class is what drops the floating card's border,
   * radius and shadow and lets the page, not an overlay, make room for it.
   */
  import type { PlayerStore } from "../lib/player/store.svelte";

  interface Props {
    player: PlayerStore;
    open: boolean;
    onclose: () => void;
  }

  let { player, open, onclose }: Props = $props();

  const upNext = $derived(player.upNext);
  /** A sentence the list has to say. Empty means the paragraph is hidden, as in the host. */
  let status = $state("");

  function remove(position: number): void {
    player.removeUpNext(position);
  }
</script>

<aside id="playback-panel" class:open={open} class:in-full={true} aria-label="Playback queue">
  <details id="playback-details" open>
    <summary>
      <span class="pq-heading">Up next <span id="playback-count">{upNext.length}</span></span>
      <button
        type="button"
        class="pq-clear"
        id="pqclear"
        title="Clear queue"
        aria-label="Clear queue"
        disabled={upNext.length === 0}
        onclick={(event) => {
          event.preventDefault();
          event.stopPropagation();
          player.clearUpcoming();
          status = "Queue cleared. Current audio is unchanged.";
        }}
      ><svg viewBox="0 0 24 24" width="18" height="18" fill="none" stroke="currentColor" stroke-width="2.1" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true"><path d="M19.5 3.5 11.2 11.8"/><path d="M6.6 14.6 12 20"/><path d="M11.2 11.8 6.6 14.6"/><path d="M6.6 14.6 3.4 20.8"/><path d="M9.3 16.2 7.6 21.4"/><path d="M11.4 17.6 11.8 22"/></svg></button>
      <button
        type="button"
        class="pq-close"
        onclick={(event) => {
          event.preventDefault();
          event.stopPropagation();
          onclose();
        }}
        aria-label="Close playback queue">×</button>
    </summary>

    <!-- The "now" line belongs to the queue, not to the title: it is the head of the play
         order. `index` is what says a playhead exists, which is the host's own test. -->
    {#if player.track && player.index >= 0}
      <div id="playback-now" class="pq-now">
        <span class="pq-live" aria-hidden="true"></span>
        <span class="pq-name">{player.track.title}<small>{player.track.artist ?? ""}</small></span>
        <span class="pq-tag now">now</span>
      </div>
    {/if}

    <div id="playback-items">
      {#each upNext as song, position (song.id)}
        <div
          class="pq-row"
          data-id={song.id}
          tabindex="0"
          role="button"
          aria-label="Play {song.title}"
          onclick={() => void player.playUpNext(position)}
          onkeydown={(event) => {
            if (event.key === "Enter" || event.key === " ") {
              event.preventDefault();
              void player.playUpNext(position);
            }
          }}
        >
          <div class="pq-body">
            <span class="pq-name">{song.title}<small>{song.artist ?? ""}</small></span>
            <button
              class="pq-remove"
              type="button"
              tabindex="-1"
              aria-label="Remove {song.title} from the queue"
              onclick={(event) => {
                event.stopPropagation();
                remove(position);
              }}>Remove</button>
          </div>
        </div>
      {:else}
        <p class="hint pq-empty">Nothing up next — the queue is empty.</p>
      {/each}
    </div>
    <p id="playback-status" role="status" aria-live="polite">{status}</p>
  </details>
</aside>