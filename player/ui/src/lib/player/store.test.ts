/* The two ways a playback failure is recorded.
 *
 * Two surfaces draw `player.error` — the shell above the content, the fullscreen player under
 * the title — and both are only as good as this store keeping it. Nothing wrote to it before
 * that was noticed, and a stream that could not be started left the screen unchanged: a Play
 * button that did nothing. So the paths that set it are pinned here.
 */

import { describe, expect, it, vi } from "vitest";
import { PlayerStore } from "./store.svelte";
import type { AudioEngine } from "./engine";
import type { MusicServer, Song } from "../subsonic";

const SONG: Song = { id: "song-1", title: "A song", duration: 200 };

function stubEngine(): AudioEngine {
  return {
    kind: "web",
    ownsQueue: false,
    setSource: vi.fn(),
    play: vi.fn(),
    pause: vi.fn(),
    seekFraction: vi.fn(),
    setVolume: vi.fn(),
    setMuted: vi.fn(),
    destroy: vi.fn(),
  };
}

/** A server that answers, for the tests that are about the order rather than the failure. */
function okServer(): MusicServer {
  return {
    source: vi.fn(async (id: string) => ({ url: `http://stub/${id}` })),
    cover: vi.fn(async () => null),
    lyrics: vi.fn(async () => null),
  } as unknown as MusicServer;
}

/** A server whose stream address cannot be resolved — a deleted file, or JSON where audio is. */
function brokenServer(): MusicServer {
  return {
    source: vi.fn(async () => {
      throw new Error("This track could not be played");
    }),
    cover: vi.fn(async () => null),
    lyrics: vi.fn(async () => null),
  } as unknown as MusicServer;
}

function store(server: MusicServer): { player: PlayerStore; engine: AudioEngine } {
  // The store reads the saved volume at construction; the rest of the tree runs in a browser.
  (globalThis as unknown as { localStorage: unknown }).localStorage = {
    getItem: () => null,
    setItem: () => {},
  };
  const engine = stubEngine();
  const player = new PlayerStore(engine);
  player.attach(server);
  return { player, engine };
}

describe("a track that will not play", () => {
  it("records why, instead of leaving a Play button that does nothing", async () => {
    const { player, engine } = store(brokenServer());

    await player.playQueue([SONG], 0);

    expect(player.error).toBe("This track could not be played");
    // Nothing was handed to the engine, so there is no address to leak or retry by accident.
    expect(engine.setSource).not.toHaveBeenCalled();
  });

  it("records what the engine itself reports, since that path never touches the server", () => {
    const { player } = store(brokenServer());

    player.engineEvents.error("the file is gone");

    expect(player.error).toBe("the file is gone");
  });
});

describe("editing the queue", () => {
  const [a, b, c, d] = ["a", "b", "c", "d"].map((id) => ({ id, title: id.toUpperCase(), duration: 100 }));

  it("drops one queued song without moving the playhead", async () => {
    /* The row's `×` calls this. `upNext` is an offset from the playhead into a second list
       (`#order`), so removing from the wrong one, or forgetting the index shift, would play the
       wrong song next — a queue that lies about what is coming. */
    const { player } = store(okServer());
    await player.playQueue([a!, b!, c!, d!], 1);

    expect(player.track?.id).toBe("b");
    expect(player.upNext.map((song) => song.id)).toEqual(["c", "d"]);

    player.removeUpNext(0);

    expect(player.upNext.map((song) => song.id)).toEqual(["d"]);
    expect(player.queue.map((song) => song.id)).toEqual(["a", "b", "d"]);
    expect(player.track?.id).toBe("b");
  });

  it("keeps one song queued twice as two entries", async () => {
    // "Queue next" on a song that is already up next is allowed; the queue is an order, not a set.
    // The list has to draw both, which is why it cannot be keyed by the song's id.
    const { player } = store(okServer());
    await player.playQueue([a!, b!], 0);
    player.enqueue([b!], { next: true });
    player.enqueue([b!], { next: true });

    expect(player.upNext.map((song) => song.id)).toEqual(["b", "b", "b"]);
  });
});
