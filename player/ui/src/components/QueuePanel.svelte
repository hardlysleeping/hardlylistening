<script lang="ts">
  /* Up next: the host's `#playback-panel`, in the fullscreen player's queue slot.
   *
   * The heading, the count, the broom, the "now" row and the row itself are the host's markup
   * and classes; `playback-queue.css` is imported by the app's stylesheet entry point. What is
   * gone is the "recently played" drawer, which is a host listening history, and the drag handle,
   * because a reorder that is not wired is a control that lies.
   *
   * The host's per-row Remove panel is also gone: it is revealed by a horizontal swipe, and this
   * client carries no swipe controller, so the 92 px red panel stayed parked just inside each
   * row's clipped edge — a red sliver at the right of every queued song, reading as an error on
   * rows that were fine. Removal is now a `×` of its own, which is a control a mouse and a thumb
   * can both reach, and it calls the same `removeUpNext(position)`.
   *
   * Reordering is the host's keyboard gesture and nothing else: Alt with Up/Down moves a row one
   * place, Alt with Home/End sends it to either end (`queueHost`'s own keydown in
   * `web/js/playback-queue.js`). The pointer grip is not ported, for the same reason the swipe is
   * not: a drag nobody bound is a control that lies, and the keys are the path that has to work
   * on a phone being read by VoiceOver as well.
   *
   * `in-full` is the class the host adds when it moves this panel into the column; here the panel
   * has no other home, so it is always on. That class is what drops the floating card's border,
   * radius and shadow and lets the page, not an overlay, make room for it.
   */
  import { tick } from "svelte";
  import type { PlayerStore } from "../lib/player/store.svelte";

  interface Props {
    player: PlayerStore;
    open: boolean;
    onclose: () => void;
  }

  let { player, open, onclose }: Props = $props();

  /* Escape closes this sheet from `App`, which holds both layers and peels one per press — the
   * host's arrangement, where one document listener in `index.html` closes the queue first and the
   * player second. A listener here as well made the key a race between two registrations: after
   * `#qgrip` opened both surfaces, the sheet's listener ran first, so the player's read the queue
   * as already shut and closed the player too. */

  const upNext = $derived(player.upNext);
  /** A sentence the list has to say. Empty means the paragraph is hidden, as in the host. */
  let status = $state("");

  /* Alt+Up/Down/Home/End, as the host's queue row reads them. Positions are offsets into Up
     next, so the playhead is never crossed and the current audio is never touched. */
  async function move(position: number, to: number): Promise<void> {
    const count = player.upNext.length;
    const target = Math.max(0, Math.min(count - 1, to));
    if (target === position) return;
    player.moveUpNext(position, target);
    status = "Queue order updated.";
    await tick();
    /* Rows are patched by position, so without this the focus stays on the slot the song left
       behind and the next Alt+Arrow would move the wrong one. */
    document.querySelectorAll<HTMLElement>("#playback-items .pq-row")[target]?.focus();
  }

  async function remove(position: number): Promise<void> {
    player.removeUpNext(position);
    status = "Removed from queue. Current audio is unchanged.";
    await tick();
    /* The row that held focus is gone with its button, and the browser then drops focus to
       `body` — the next Tab starts again from the top of the page, outside the sheet. The host
       refocuses the same control after a queue edit; here that is whichever button took the
       removed row's place, or the one above it when the last row went, or the broom, or the
       sheet's own close button when there is nothing left to remove. */
    const drops = document.querySelectorAll<HTMLButtonElement>("#playback-items .pq-drop");
    const next =
      drops[Math.min(position, drops.length - 1)] ??
      document.querySelector<HTMLElement>("#pqclear:not([disabled])") ??
      document.querySelector<HTMLElement>("#playback-panel .pq-close");
    next?.focus();
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
      <!-- Keyed by position, not by song: a queue may legitimately hold the same song twice
           ("queue next" on a song that is already up next), and a keyed each with duplicate
           keys silently drops the extra rows — the count said four while two were drawn. -->
      {#each upNext as song, position}
        <div
          class="pq-row"
          data-id={song.id}
          tabindex="0"
          role="button"
          aria-label="Play {song.title}, Alt+Up or Alt+Down to reorder"
          onclick={() => void player.playUpNext(position)}
          onkeydown={(event) => {
            /* The row's own keys, not its children's. Enter and Space on the remove button are
               that button's — and because this handler also called `preventDefault`, the browser
               never got to activate it: one keystroke on the × played the song and left it in the
               queue, which is the opposite of what the button says it does. */
            if (event.target !== event.currentTarget) return;
            if (event.altKey) {
              const moves: Record<string, number> = {
                ArrowUp: position - 1,
                ArrowDown: position + 1,
                Home: 0,
                End: player.upNext.length - 1,
              };
              const to = moves[event.key];
              if (to === undefined) return;
              event.preventDefault();
              void move(position, to);
              return;
            }
            if (event.key === "Enter" || event.key === " ") {
              event.preventDefault();
              void player.playUpNext(position);
            }
          }}
        >
          <div class="pq-body">
            <span class="pq-name">{song.title}<small>{song.artist ?? ""}</small></span>
            <button
              class="pq-drop"
              type="button"
              title="Remove from the queue"
              aria-label="Remove {song.title} from the queue"
              onclick={(event) => {
                event.stopPropagation();
                remove(position);
              }}>×</button>
          </div>
        </div>
      {:else}
        <p class="hint pq-empty">Nothing up next — the queue is empty.</p>
      {/each}
    </div>
    <p id="playback-status" role="status" aria-live="polite">{status}</p>
  </details>
</aside>

<style>
  /* The host styles `#playback-items .pq-remove` as a 92 px panel that slides in from the right
     edge. This component draws its own button instead, so the host rule has nothing to apply to
     and every queued row keeps its own small target at the end. */
  .pq-drop {
    flex: 0 0 auto;
    width: 32px;
    height: 32px;
    min-height: 0;
    padding: 0;
    display: grid;
    place-items: center;
    border: 0;
    border-radius: 9px;
    background: none;
    color: var(--dim);
    font: inherit;
    font-size: 17px;
    line-height: 1;
    cursor: pointer;
  }

  .pq-drop:hover,
  .pq-drop:focus-visible {
    color: var(--text);
    background: var(--panel2);
  }
</style>
