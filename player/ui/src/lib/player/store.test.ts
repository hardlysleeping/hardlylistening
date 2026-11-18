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

describe("signing out", () => {
  it("leaves no queue, no song, and nothing to explain", async () => {
    /* Song ids are names the *server* issued, so a queue outlives its meaning the moment the
       account ends. It also kept the failure line: signing out of one server and in to another
       showed "This track could not be played" over a library that had just loaded fine. */
    const { player, engine } = store(brokenServer());
    await player.playQueue([SONG], 0);
    expect(player.error).toBe("This track could not be played");
    player.lyrics = { value: "someone's words" };
    player.lyricsOpen = true;

    player.detach();

    expect(player.track).toBeNull();
    expect(player.queue).toEqual([]);
    expect(player.upNext).toEqual([]);
    expect(player.index).toBe(-1);
    expect(player.error).toBeNull();
    // The words too: another account's lyric sheet is as meaningless as its queue, and a closed
    // overlay left open would reappear over a library that has just loaded.
    expect(player.lyrics).toBeNull();
    expect(player.lyricsOpen).toBe(false);
    expect(engine.pause).toHaveBeenCalled();
    expect(engine.setSource).toHaveBeenLastCalledWith(null);
  });
});

describe("words", () => {
  /* OpenSubsonic answers line timing in milliseconds; the store's clock is seconds, because
     that is what every engine reports. The conversion happens in the client, and these cases
     hold the store to the result: a line shows while the song is inside it, and the last line
     stays up to the end rather than falling back to the first. */
  const TIMED = {
    synced: true,
    line: [
      { start: 0, value: "first" },
      { start: 2, value: "second" },
      { start: 5, value: "third" },
    ],
  };

  function at(player: PlayerStore, seconds: number): number {
    player.engineEvents.time(seconds, 200);
    return player.lyricsLine;
  }

  it("follows the clock, line by line", () => {
    const { player } = store(okServer());
    player.lyrics = TIMED;

    expect(at(player, 0)).toBe(0);
    expect(at(player, 1.9)).toBe(0);
    expect(at(player, 2)).toBe(1);
    expect(at(player, 4.99)).toBe(1);
    expect(at(player, 5)).toBe(2);
    expect(at(player, 180)).toBe(2);
  });

  it("has nothing to follow when no line is timed", () => {
    const { player } = store(okServer());
    player.lyrics = { value: "just words" };
    expect(player.lyricsLine).toBe(-1);

    player.lyrics = { synced: false, line: TIMED.line };
    expect(player.lyricsLine).toBe(-1);

    player.lyrics = null;
    expect(player.lyricsLine).toBe(-1);
  });

  it("keeps a slow answer for the old track off the new one", async () => {
    /* Two requests are in flight whenever a listener skips: the words for the song they left
       and the words for the one playing. Painting the first one over the second is the visible
       bug — lyrics that belong to the wrong song, against the wrong clock. */
    const pending: Array<(value: unknown) => void> = [];
    const slow = {
      ...okServer(),
      lyrics: vi.fn(
        () =>
          new Promise((resolve) => {
            pending.push(resolve);
          }),
      ),
    } as unknown as MusicServer;

    const { player } = store(slow);
    await player.playQueue([SONG, { ...SONG, id: "song-2", title: "Next" }], 0);

    // Skip before the first answer arrives; the second track asks its own question.
    await player.playQueue(player.queue, 1);
    expect(player.track?.id).toBe("song-2");
    expect(pending).toHaveLength(2);

    const flush = async () => {
      await Promise.resolve();
      await Promise.resolve();
      await Promise.resolve();
    };

    // The song left behind answers last, and does not get to paint.
    pending[0]?.({ synced: true, line: [{ start: 0, value: "words for the song before" }] });
    await flush();
    expect(player.lyrics).toBeNull();

    // The song playing answers, and does.
    pending[1]?.({ synced: true, line: [{ start: 0, value: "words for the one playing" }] });
    await flush();
    expect(player.lyrics?.line?.[0]?.value).toBe("words for the one playing");
  });

  it("closes when the next track has no words of its own", async () => {
    /* The host hides its overlay on `loadstart`, and for the same reason: the reading belongs
       to the track. Left open across a skip it is an empty panel covering the disc — no lines,
       no status, just a close button — because the words were cleared with the old song. */
    const { player } = store(okServer());
    await player.playQueue([SONG], 0);
    player.lyrics = { synced: true, line: [{ start: 0, value: "words" }] };
    player.toggleLyrics();
    expect(player.lyricsOpen).toBe(true);

    await player.playQueue([{ ...SONG, id: "song-2", title: "Next" }], 0);

    expect(player.lyricsOpen).toBe(false);
    expect(player.lyrics).toBeNull();
  });

  it("opens only when there is something to read", () => {
    const { player } = store(okServer());

    player.toggleLyrics();
    expect(player.lyricsOpen).toBe(false);

    player.lyrics = { synced: false, line: [] };
    player.toggleLyrics();
    expect(player.lyricsOpen).toBe(false);

    player.lyrics = { value: "plain words" };
    player.toggleLyrics();
    expect(player.lyricsOpen).toBe(true);

    player.toggleLyrics();
    expect(player.lyricsOpen).toBe(false);
  });
});
