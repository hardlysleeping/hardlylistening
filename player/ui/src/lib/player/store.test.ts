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

  it("keeps the playhead and the next song straight when the order is shuffled", async () => {
    /* The plain queue's order is the queue's own numbering, so the index shift on removal looks
       after itself. Under shuffle the order is a permutation: removing a song moves the queue
       position of entries that sit *before* the playhead too, and the playhead is a position in
       the order, not in the queue. Nothing exercised that branch. */
    const random = vi.spyOn(Math, "random").mockReturnValue(0);
    const { player } = store(okServer());
    await player.playQueue([a!, b!, c!, d!], 0);
    player.toggleShuffle();
    random.mockRestore();

    // Fixed shuffle: a, then c, d, b.
    expect(player.track?.id).toBe("a");
    expect(player.upNext.map((song) => song.id)).toEqual(["c", "d", "b"]);

    player.removeUpNext(1);

    expect(player.queue.map((song) => song.id)).toEqual(["a", "b", "c"]);
    expect(player.upNext.map((song) => song.id)).toEqual(["c", "b"]);
    expect(player.track?.id).toBe("a");

    // And the order the player now holds still says: next is c, the song that was after d.
    await player.next();

    expect(player.track?.id).toBe("c");
  });

  it("moves an up-next song without touching the playhead or the queue", async () => {
    /* The host's Alt+Arrow reorder writes the order, not the queue: a reorder is not an edit of
       what is queued, it is a different reading of the same list. Removing and re-inserting the
       song would renumber the queue and, with a playhead, move the thing that is playing. */
    const { player } = store(okServer());
    await player.playQueue([a!, b!, c!, d!], 1);

    // Up next is c, d; the last of them moves to the front.
    player.moveUpNext(1, 0);

    expect(player.queue.map((song) => song.id)).toEqual(["a", "b", "c", "d"]);
    expect(player.upNext.map((song) => song.id)).toEqual(["d", "c"]);
    expect(player.track?.id).toBe("b");

    // Out of range is a no-op rather than a wrap-around, which is what a row at the end needs.
    player.moveUpNext(0, 2);
    player.moveUpNext(-1, 0);
    expect(player.upNext.map((song) => song.id)).toEqual(["d", "c"]);

    // The order is what `next` reads, so the moved song is now the one that plays.
    await player.next();
    expect(player.track?.id).toBe("d");
  });

  it("moves a row the same way when the order is shuffled", async () => {
    const random = vi.spyOn(Math, "random").mockReturnValue(0);
    const { player } = store(okServer());
    await player.playQueue([a!, b!, c!, d!], 0);
    player.toggleShuffle();
    random.mockRestore();
    // Fixed shuffle: a, then c, d, b.
    expect(player.upNext.map((song) => song.id)).toEqual(["c", "d", "b"]);

    player.moveUpNext(2, 0);

    expect(player.upNext.map((song) => song.id)).toEqual(["b", "c", "d"]);
    expect(player.queue.map((song) => song.id)).toEqual(["a", "b", "c", "d"]);
    expect(player.track?.id).toBe("a");

    await player.next();
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

  it("puts the new song in front of the next one instead of replacing it", async () => {
    /* Found by walking the queue: with `a` playing, one `+` on a playlist row left Up Next
       reading `x, x, c` — the song that was going to play next had vanished and the one just
       added was listed twice, so the queue silently skipped `b` and played `x` again. The
       reindex asked "is this entry new?" by comparing index *values*, and an old entry that
       happened to land on the inserted number was not shifted. */
    const { player } = store(okServer());
    await player.playQueue([a!, b!, c!], 0);
    const x = { id: "x", title: "X", duration: 100 };

    player.enqueue([x], { next: true });

    expect(player.queue.map((song) => song.id)).toEqual(["a", "x", "b", "c"]);
    expect(player.upNext.map((song) => song.id)).toEqual(["x", "b", "c"]);
    expect(player.track?.id).toBe("a");
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

describe("the queue comes back", () => {
  /** Storage that survives the two stores in a case, the way a page reload does. */
  function sharedStorage(): Storage {
    const map = new Map<string, string>();
    return {
      getItem: (key: string) => map.get(key) ?? null,
      setItem: (key: string, value: string) => void map.set(key, String(value)),
      removeItem: (key: string) => void map.delete(key),
      clear: () => map.clear(),
      key: (at: number) => [...map.keys()][at] ?? null,
      get length() {
        return map.size;
      },
    } as Storage;
  }

  function boot(server: MusicServer, origin: string) {
    const engine = stubEngine();
    const player = new PlayerStore(engine);
    player.attach(server, origin);
    return { player, engine };
  }

  const songs: Song[] = [
    { id: "a", title: "A", duration: 100 },
    { id: "b", title: "B", duration: 120 },
    { id: "c", title: "C", duration: 140 },
  ];

  it("returns the list and the playhead, paused, without asking for audio", async () => {
    /* The player's queue lives inside the fullscreen player, which only exists once there is a
       track, so a restored list without a playhead would be a queue nobody could reach. The
       track comes back too — and stays silent until the person presses play. */
    (globalThis as unknown as { localStorage: unknown }).localStorage = sharedStorage();
    const first = boot(okServer(), "http://one");
    await first.player.playQueue(songs, 1);

    const server = okServer();
    const second = boot(server, "http://one");

    expect(second.player.queue.map((song) => song.id)).toEqual(["a", "b", "c"]);
    expect(second.player.upNext.map((song) => song.id)).toEqual(["c"]);
    expect(second.player.track?.id).toBe("b");
    expect(second.player.playing).toBe(false);
    expect(second.player.position).toBe(0);
    // Restoring is not playing: the server was never asked for bytes and the engine never started.
    expect(server.source).not.toHaveBeenCalled();
    expect(second.engine.setSource).not.toHaveBeenCalled();
    expect(second.engine.play).not.toHaveBeenCalled();

    // The first press is the one that resolves the source, because there is none yet.
    await second.player.toggle();
    expect(server.source).toHaveBeenCalledWith("b");
    expect(second.engine.setSource).toHaveBeenCalledWith("http://stub/b");
    expect(second.engine.play).toHaveBeenCalled();
    // `playing` itself follows the engine's event, which a stub never raises.
  });

  it("does not adopt a queue that belongs to another server", async () => {
    /* A song id is only a name on the server that issued it. Adopting another server's ids
       would either play the wrong song or draw rows for tracks that do not exist. */
    (globalThis as unknown as { localStorage: unknown }).localStorage = sharedStorage();
    const first = boot(okServer(), "http://one");
    await first.player.playQueue(songs, 1);

    const other = boot(okServer(), "http://two");

    expect(other.player.queue).toEqual([]);
    expect(other.player.track).toBeNull();
    expect(other.player.index).toBe(-1);
  });

  it("leaves an empty saved queue behind when the account is signed out", async () => {
    // Signing out empties the queue for this session; the saved copy has to agree, or the next
    // sign-in resurrects a list the person cleared by leaving.
    (globalThis as unknown as { localStorage: unknown }).localStorage = sharedStorage();
    const first = boot(okServer(), "http://one");
    await first.player.playQueue(songs, 0);
    first.player.detach();

    const again = boot(okServer(), "http://one");

    expect(again.player.queue).toEqual([]);
    expect(again.player.track).toBeNull();
  });

  it("ignores a saved order that is not the queue", async () => {
    /* A permutation with a hole, a repeat, or an index past the end is a queue that plays the
       wrong song. Dropped rather than repaired — there is nothing to repair it from. */
    const storage = sharedStorage();
    (globalThis as unknown as { localStorage: unknown }).localStorage = storage;
    storage.setItem(
      "hl.player.queue.v1",
      JSON.stringify({
        server: "http://one",
        queue: songs,
        order: [0, 0, 1],
        index: 0,
      }),
    );

    const { player } = boot(okServer(), "http://one");

    expect(player.queue).toEqual([]);
    expect(player.track).toBeNull();
  });
});
