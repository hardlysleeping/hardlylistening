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
   * Reordering has two paths, as it does on the host. Alt with Up/Down moves a row one place and
   * Alt with Home/End sends it to either end (`queueHost`'s own keydown in
   * `web/js/playback-queue.js`) — the path that works without a pointer, and the only one
   * VoiceOver can drive. The other is the pointer: the host's `bindOrderDrag`, where a grip on a
   * wide row drags at once and a phone row is the target itself, lifted by a 280 ms hold. That
   * second path is not decoration here. Without it the queue cannot be reordered on a phone at
   * all, because a touch screen has no Alt key — the reason the host never relied on the keys
   * alone. A cancelled gesture puts the row back; only a released drag writes an order.
   *
   * `in-full` is the class the host adds when it moves this panel into the column; here the panel
   * has no other home, so it is always on. That class is what drops the floating card's border,
   * radius and shadow and lets the page, not an overlay, make room for it.
   */
  import { flushSync, tick } from "svelte";
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
  let host = $state<HTMLElement | null>(null);

  /* Alt+Up/Down/Home/End, as the host's queue row reads them. Positions are offsets into Up
     next, so the playhead is never crossed and the current audio is never touched. */
  async function move(position: number, to: number): Promise<void> {
    const count = player.upNext.length;
    const target = Math.max(0, Math.min(count - 1, to));
    if (target === position) return;
    /* Applied together with the paint. The list is an unkeyed `each`, so a block belongs to the
       node it was created on; a drag that moved a node by hand has to be handed back *before*
       the order changes, or the new data lands in the old physical order and the list looks
       unmoved (measured: the status said "Queue order updated." over an unchanged list). */
    flushSync(() => player.moveUpNext(position, target));
    status = "Queue order updated.";
    await tick();
    /* Rows are patched by position, so without this the focus stays on the slot the song left
       behind and the next Alt+Arrow would move the wrong one. */
    document.querySelectorAll<HTMLElement>("#playback-items .pq-row .pq-play")[target]?.focus();
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

  /* Tap plays, × removes, Alt+arrow moves, and a pointer drag reorders — one gesture
   * implementation for mouse, pen and touch, ported from the host's `bindOrderDrag`.
   *
   * A grip arms at once; a row body arms on a mouse after 3 px of vertical movement and on touch
   * after a 280 ms hold, so a tap still plays and a swipe still scrolls. The drag is tracked on
   * the window because a release below the last row lands outside the list. Cancel restores the
   * DOM order and writes nothing: a reorder that was abandoned is not an edit.
   *
   * `from`/`to` are positions in Up next, which is what the rows are — the store's `moveUpNext`
   * takes the same two numbers the keyboard path passes. */
  function rows(): HTMLElement[] {
    return host ? [...host.querySelectorAll<HTMLElement>(".pq-row")] : [];
  }

  $effect(() => {
    const el = host;
    if (!el) return;

    let drag: {
      row: HTMLElement;
      pointer: number;
      x: number;
      y: number;
      moved: boolean;
      from: number;
      to: number;
      after: Element | null;
    } | null = null;
    let hold: ReturnType<typeof setTimeout> | null = null;
    let armed = false;

    const release = (): void => {
      if (hold !== null) clearTimeout(hold);
      hold = null;
    };

    const lift = (row: HTMLElement, pointer: number): void => {
      row.classList.add("pq-lift");
      el.dataset.dragging = "1";
      try {
        row.setPointerCapture?.(pointer);
      } catch {
        /* A synthetic pointer has no capture; the window listeners still track it. */
      }
    };

    const onMove = (event: PointerEvent): void => {
      if (!drag || drag.pointer !== event.pointerId) return;
      if (!armed) {
        // Past the slop before the hold landed: that was a scroll, not a lift.
        if (Math.abs(event.clientY - drag.y) > 8 || Math.abs(event.clientX - drag.x) > 8) {
          release();
          drag = null;
        }
        return;
      }
      event.preventDefault();
      const dx = Math.abs(event.clientX - drag.x);
      const dy = Math.abs(event.clientY - drag.y);
      // Vertical movement is what makes a mouse drag; sideways is the page's own scrolling.
      if (!drag.moved && dy > 3 && dy >= dx) {
        drag.moved = true;
        if (!drag.row.classList.contains("pq-lift")) lift(drag.row, drag.pointer);
      }
      /* Before the first row whose middle is below the finger: one idempotent answer for every
         position. Hit-testing here would return the lifted row itself, which sits under the
         finger, and swapping on each move slides a row several places. */
      const others = rows().filter((node) => node !== drag?.row);
      const next = others.find((node) => {
        const box = node.getBoundingClientRect();
        return event.clientY < box.top + box.height / 2;
      });
      const last = el.lastElementChild;
      if (last !== drag.row || next) el.insertBefore(drag.row, next ?? null);

      const box = el.getBoundingClientRect();
      if (event.clientY < box.top + 32) el.scrollTop -= 14;
      else if (event.clientY > box.bottom - 32) el.scrollTop += 14;
    };

    const finish = (event: PointerEvent, cancelled: boolean): void => {
      release();
      window.removeEventListener("pointermove", onMove);
      window.removeEventListener("pointerup", onUp);
      window.removeEventListener("pointercancel", onCancel);
      if (!drag || event.pointerId !== drag.pointer) return;
      const { row, from, after, moved, to } = drag;
      row.classList.remove("pq-lift");
      delete el.dataset.dragging;
      drag = null;
      armed = false;
      if (!moved) return;
      /* Hand the DOM back to the order the blocks were made in, cancelled or not. A drop then
         writes the order, which repaints this same list in its new sequence; a cancel writes
         nothing and there is nothing left to undo. */
      if (after && after.parentElement === el) el.insertBefore(row, after);
      else el.appendChild(row);
      if (cancelled) return;
      // The click that trails a drop belongs to the gesture, not to the row's play action.
      el.dataset.justDragged = "1";
      void move(from, to);
    };

    /* Where the row ended up, read before the DOM is handed back. */
    function landed(): number {
      return drag ? rows().indexOf(drag.row) : -1;
    }

    const onUp = (event: PointerEvent): void => {
      if (drag && event.pointerId === drag.pointer) drag.to = landed();
      finish(event, false);
    };
    const onCancel = (event: PointerEvent): void => finish(event, true);

    const onDown = (event: PointerEvent): void => {
      if (event.button > 0) return;
      const grip = (event.target as Element | null)?.closest(".pq-handle") ?? null;
      /* A press on the × is the ×'s; anywhere else in the row, including the play target, may
         begin a drag. The play target is a button, so it cannot be excluded by tag: it *is* the
         row's surface now, and a drag that could not start on it could not start at all. */
      if (!grip && (event.target as Element | null)?.closest(".pq-drop")) return;
      const row = (grip ?? (event.target as Element | null))?.closest<HTMLElement>(".pq-row");
      if (!row || row.parentElement !== el) return;
      // A stale flag would swallow the next genuine tap after a drop that produced no click.
      delete el.dataset.justDragged;
      drag = {
        row,
        pointer: event.pointerId,
        x: event.clientX,
        y: event.clientY,
        moved: false,
        from: rows().indexOf(row),
        to: rows().indexOf(row),
        after: row.nextElementSibling,
      };
      armed = Boolean(grip) || event.pointerType !== "touch";
      if (grip) lift(row, event.pointerId);
      else if (!armed) {
        hold = setTimeout(() => {
          if (!drag) return;
          armed = true;
          lift(drag.row, drag.pointer);
          navigator.vibrate?.(8);
        }, 280);
      }
      window.addEventListener("pointermove", onMove);
      window.addEventListener("pointerup", onUp);
      window.addEventListener("pointercancel", onCancel);
    };

    /* `touch-action: pan-y` is latched when the finger lands, so the browser would scroll the
       list and cancel the pointer mid-drag. Blocking it only while a row is lifted leaves
       ordinary scrolling untouched. */
    const onTouchMove = (event: TouchEvent): void => {
      if (el.dataset.dragging === "1" && event.cancelable) event.preventDefault();
    };
    // A long press belongs to the lift, not to the browser's copy/share sheet.
    const onContextMenu = (event: Event): void => {
      if ((event.target as Element | null)?.closest(".pq-row")) event.preventDefault();
    };

    el.addEventListener("pointerdown", onDown);
    el.addEventListener("touchmove", onTouchMove, { passive: false });
    el.addEventListener("contextmenu", onContextMenu);
    return () => {
      release();
      el.removeEventListener("pointerdown", onDown);
      el.removeEventListener("touchmove", onTouchMove);
      el.removeEventListener("contextmenu", onContextMenu);
      window.removeEventListener("pointermove", onMove);
      window.removeEventListener("pointerup", onUp);
      window.removeEventListener("pointercancel", onCancel);
    };
  });

  /** A row's click, unless the click is the tail of a drag that just ended. */
  function play(position: number): void {
    if (host?.dataset.justDragged === "1") {
      delete host.dataset.justDragged;
      return;
    }
    void player.playUpNext(position);
  }
</script>

<aside id="playback-panel" class:open={open} class:in-full={true} aria-label="Playback queue">
  <!-- A plain header, not `<details><summary>`. The host's panel is a disclosure, and this one
       never discloses: it has no other home, so it is always open, and the two buttons beside the
       heading lived inside the summary — a summary being a control, its buttons were controls
       inside a control, and a press on the heading collapsed the list with nothing in the sheet
       that could open it again. The layout is the host's `summary` rule, restated here because
       there is no summary left to carry it. -->
  <div id="playback-details">
    <div class="pq-head">
      <span class="pq-heading">Up next <span id="playback-count">{upNext.length}</span></span>
      <span class="vh">Reordering: Alt with Up or Down moves a row one place, Alt with Home or End
        sends it to either end, or hold a row and drag it.</span>
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
    </div>

    <!-- The "now" line belongs to the queue, not to the title: it is the head of the play
         order. `index` is what says a playhead exists, which is the host's own test. -->
    {#if player.track && player.index >= 0}
      <div id="playback-now" class="pq-now">
        <span class="pq-live" aria-hidden="true"></span>
        <span class="pq-name">{player.track.title}<small>{player.track.artist ?? ""}</small></span>
        <span class="pq-tag now">now</span>
      </div>
    {/if}

    <div id="playback-items" bind:this={host}>
      <!-- Keyed by position, not by song: a queue may legitimately hold the same song twice
           ("queue next" on a song that is already up next), and a keyed each with duplicate
           keys silently drops the extra rows — the count said four while two were drawn. -->
      {#each upNext as song, position}
        <div class="pq-row" data-id={song.id}>
          <div class="pq-body">
            <!-- The row's play target, under its contents: the same box the whole row used to be,
                 and now a button rather than a `role="button"` around the grip and the `×`. A
                 widget role makes a row's children presentational, and the `×` is the only way to
                 remove a song — it cannot be a decoration of a bigger control. The keys live
                 here because this is where focus lands, and Enter and Space are the browser's
                 own activation of it. -->
            <button
              type="button"
              class="pq-play"
              aria-label="Play {song.title}{song.artist ? `, ${song.artist}` : ""}"
              onclick={() => play(position)}
              onkeydown={(event) => {
                if (!event.altKey) return;
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
              }}
            ></button>
            <!-- The host's grip, hidden below 600 px by the host's own stylesheet: there the row
                 is the target and a hold lifts it. Kept out of the tab order because it is a
                 pointer handle with no activation of its own, and a control that does nothing
                 when pressed is the thing this file keeps refusing to ship. -->
            <button
              class="pq-handle"
              type="button"
              tabindex="-1"
              aria-hidden="true"
              title="Drag to reorder"
            >⠿</button>
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
  </div>
</aside>

<style>
  /* The host's `#playback-panel summary` rule, restated: the heading row is a plain div now
     (a summary cannot hold the broom and the close button without nesting controls). */
  .pq-head {
    display: flex;
    align-items: center;
    gap: 8px;
    padding: 8px 4px 6px;
  }

  /* Said once for the whole sheet, not repeated in every row's name. */
  .vh {
    position: absolute;
    width: 1px;
    height: 1px;
    margin: -1px;
    padding: 0;
    overflow: hidden;
    clip-path: inset(50%);
    white-space: nowrap;
    border: 0;
  }

  /* The play target fills the row and paints under its contents, so the grip and the × keep
     their own presses: a static sibling would be below a positioned one whatever its order. */
  .pq-play {
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

  .pq-handle,
  .pq-drop {
    position: relative;
    z-index: 1;
  }

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
