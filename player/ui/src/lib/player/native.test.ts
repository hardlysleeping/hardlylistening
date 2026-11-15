/* The store with an engine that owns its queue.
 *
 * The point of these tests is the division of labour, not sound. A queue-owning engine is handed
 * ids and an order; it moves through them itself, because on a locked phone there is no
 * JavaScript to ask. So the store must not fetch a stream address, must not decide the next
 * index, and must follow what the player reports rather than lead it.
 *
 * The fake engine throws if it is ever handed a URL, which turns "the page built one" from a
 * review question into a failed test.
 */

import { describe, expect, it, vi } from "vitest";
import type { AudioEngine, EngineEvents, Repeat } from "./engine";
import type { MusicServer, Song } from "../subsonic";

(globalThis as unknown as { localStorage: unknown }).localStorage = {
  getItem: () => null,
  setItem: () => {},
};

const { PlayerStore } = await import("./store.svelte");
const { useNativeAudioIfPresent } = await import("./select");

function song(id: string, duration = 200): Song {
  return { id, title: `Song ${id}`, duration };
}

type Queue = { ids: string[]; repeat: Repeat };

function nativeEngine() {
  const calls: string[] = [];
  const queues: Queue[] = [];
  const engine: AudioEngine = {
    kind: "native",
    ownsQueue: true,
    // A URL reaching here would mean the page built one, which is the property P3 removes.
    setSource: (url) => {
      if (url !== null) throw new Error(`a native engine was handed ${url}`);
      calls.push("setSource:null");
    },
    play: () => calls.push("play"),
    pause: () => calls.push("pause"),
    seekFraction: () => calls.push("seek"),
    setVolume: () => {},
    setMuted: () => {},
    destroy: () => {},
    setQueue: (songs, order, repeat) => {
      calls.push("setQueue");
      queues.push({ ids: order.map((at) => songs[at]!.id), repeat });
    },
    playAt: (index) => calls.push(`playAt:${index}`),
    skip: (direction) => calls.push(`skip:${direction}`),
    setRepeat: (repeat) => calls.push(`repeat:${repeat}`),
  };
  return { engine, calls, queues };
}

function webEngine() {
  const calls: string[] = [];
  const engine: AudioEngine = {
    kind: "web",
    ownsQueue: false,
    setSource: (url) => calls.push(`source:${url ?? "null"}`),
    play: () => calls.push("play"),
    pause: () => calls.push("pause"),
    seekFraction: () => calls.push("seek"),
    setVolume: () => {},
    setMuted: () => {},
    destroy: () => {},
  };
  return { engine, calls };
}

function fakeServer() {
  const source = vi.fn(async (id: string) => ({
    songId: id,
    url: `https://music.example.com/rest/stream.view?id=${id}`,
    transcoded: false,
  }));
  const cover = vi.fn(async () => "https://music.example.com/rest/getCoverArt.view?id=1");
  const lyrics = vi.fn(async () => null);
  const server = { source, cover, lyrics } as unknown as MusicServer;
  return { server, source, cover, lyrics };
}

function nativePlayer() {
  const { engine, calls, queues } = nativeEngine();
  const { server, source } = fakeServer();
  const player = new PlayerStore(engine);
  player.attach(server);
  return { player, events: player.engineEvents, calls, queues, source };
}

describe("a queue-owning player", () => {
  it("is handed the order as ids and started at an index, and never sees a stream address", async () => {
    const { player, calls, queues, source } = nativePlayer();

    await player.playQueue([song("a"), song("b"), song("c")], 1);

    expect(queues).toEqual([{ ids: ["a", "b", "c"], repeat: "off" }]);
    expect(calls).toEqual(["setQueue", "playAt:1"]);
    // The store never asked for bytes: on this path Rust resolves each id itself.
    expect(source).not.toHaveBeenCalled();
    expect(player.track?.id).toBe("b");
    expect(player.index).toBe(1);
  });

  it("shows the song the player moved to without starting anything", async () => {
    const { player, events, calls, source } = nativePlayer();
    await player.playQueue([song("a"), song("b"), song("c")], 0);
    calls.length = 0;

    // The player advanced by itself while the page was suspended, and says where it got to.
    events.track?.(2);

    expect(player.track?.id).toBe("c");
    expect(player.index).toBe(2);
    // Following is not leading: no play, no skip, no address.
    expect(calls).toEqual([]);
    expect(source).not.toHaveBeenCalled();
  });

  it("asks the player to skip instead of working out the next track", async () => {
    const { player, calls, source } = nativePlayer();
    await player.playQueue([song("a"), song("b"), song("c")], 0);
    calls.length = 0;

    await player.next();

    expect(calls).toEqual(["skip:1"]);
    // The index moves when the player reports it, not when the button is pressed.
    expect(player.index).toBe(0);
    expect(source).not.toHaveBeenCalled();
  });

  it("leaves the three-second previous rule to the player that owns the playhead", async () => {
    const { player, events, calls } = nativePlayer();
    await player.playQueue([song("a"), song("b")], 1);
    // Well past three seconds, where the web path restarts the song instead of going back.
    events.time?.(42, 200);
    calls.length = 0;

    await player.previous();

    expect(calls).toEqual(["skip:-1"]);
    expect(calls).not.toContain("seek");
  });

  it("plays an item from Up Next by its position in the order", async () => {
    const { player, calls, source } = nativePlayer();
    await player.playQueue([song("a"), song("b"), song("c")], 0);
    calls.length = 0;

    await player.playUpNext(1);

    expect(calls).toEqual(["playAt:2"]);
    expect(player.track?.id).toBe("c");
    expect(source).not.toHaveBeenCalled();
  });

  it("changes repeat mode without replacing the order", async () => {
    const { player, calls, queues } = nativePlayer();
    await player.playQueue([song("a"), song("b")], 0);
    calls.length = 0;

    player.cycleRepeat();

    expect(player.repeat).toBe("all");
    expect(calls).toEqual(["repeat:all"]);
    // Re-pushing the order for a repeat change would restart the track on a real player.
    expect(queues).toHaveLength(1);
  });

  it("stops at the end of the order rather than asking the server for another track", async () => {
    const { player, events, calls, source } = nativePlayer();
    await player.playQueue([song("a"), song("b")], 0);
    player.playing = true;
    calls.length = 0;

    // `ended` from a queue-owning player means the order ran out: it has already done its own
    // advancing, so there is nothing for the store to start.
    events.ended();
    await Promise.resolve();

    expect(player.playing).toBe(false);
    expect(calls).toEqual([]);
    expect(player.index).toBe(0);
    expect(source).not.toHaveBeenCalled();
  });

  it("re-pushes the order when shuffle changes, and restarts where the person is", async () => {
    const { player, calls, queues } = nativePlayer();
    await player.playQueue([song("a"), song("b"), song("c")], 0);
    calls.length = 0;

    player.toggleShuffle();

    expect(queues).toHaveLength(2);
    expect(queues[1]?.ids.slice().sort()).toEqual(["a", "b", "c"]);
    // Shuffle keeps the current song first; the player is told to start there, which restarts it.
    expect(calls).toEqual(["setQueue", "playAt:0"]);
    expect(queues[1]?.ids[0]).toBe("a");
  });

  it("tells the player about a queue edit, so an added song is not silently lost", async () => {
    const { player, queues } = nativePlayer();
    await player.playQueue([song("a")], 0);

    player.enqueue([song("b")]);

    expect(queues).toHaveLength(2);
    expect(queues[1]?.ids).toEqual(["a", "b"]);
  });
});

describe("the browser engine, unchanged", () => {
  it("still asks the server for a stream address and plays it", async () => {
    const { engine, calls } = webEngine();
    const { server, source } = fakeServer();
    const player = new PlayerStore(engine);
    player.attach(server);

    await player.playQueue([song("a")], 0);

    expect(source).toHaveBeenCalledWith("a");
    expect(calls).toEqual(["source:https://music.example.com/rest/stream.view?id=a", "play"]);
  });

  it("advances the queue itself when a track ends", async () => {
    const { engine } = webEngine();
    const { server, source } = fakeServer();
    const player = new PlayerStore(engine);
    player.attach(server);
    await player.playQueue([song("a"), song("b")], 0);

    player.engineEvents.ended();
    await Promise.resolve();
    await Promise.resolve();

    expect(source).toHaveBeenCalledWith("b");
    expect(player.track?.id).toBe("b");
  });
});

describe("choosing the engine", () => {
  it("switches to the native engine only when the shell says it has one", async () => {
    const { engine } = webEngine();
    const player = new PlayerStore(engine);
    const invoke = vi.fn(async (command: string) => command === "native_audio_available");

    const used = await useNativeAudioIfPresent(player, invoke, null);

    expect(used).toBe(true);
    expect(player.engine.kind).toBe("native");
  });

  it("keeps the browser engine on a build whose player is not there", async () => {
    const { engine } = webEngine();
    const player = new PlayerStore(engine);
    const invoke = vi.fn(async () => false);

    expect(await useNativeAudioIfPresent(player, invoke, null)).toBe(false);
    expect(player.engine.kind).toBe("web");
  });

  it("keeps the browser engine when a build predates the command", async () => {
    const { engine } = webEngine();
    const player = new PlayerStore(engine);
    const invoke = vi.fn(async () => {
      throw new Error("Command native_audio_available not found");
    });

    expect(await useNativeAudioIfPresent(player, invoke, null)).toBe(false);
    expect(player.engine.kind).toBe("web");
  });

  it("hands a queue that is already playing to the engine it swaps in", async () => {
    // The swap happens at boot, before anything plays — but if it ever happens later, a queue
    // nobody handed over is a queue that stops at the end of the current song.
    const { engine } = webEngine();
    const { server } = fakeServer();
    const player = new PlayerStore(engine);
    player.attach(server);
    await player.playQueue([song("a"), song("b")], 0);

    const invoke = vi.fn(async (command: string) => command === "native_audio_available");
    await useNativeAudioIfPresent(player, invoke, null);

    const commands = invoke.mock.calls.map(([command]) => command);
    expect(commands).toContain("native_audio_queue");
    expect(commands).toContain("native_audio_play");
  });

  it("does nothing at all in a browser, which has no bridge", async () => {
    const { engine } = webEngine();
    const player = new PlayerStore(engine);

    expect(await useNativeAudioIfPresent(player, null, null)).toBe(false);
    expect(player.engine.kind).toBe("web");
  });
});

describe("the event seam", () => {
  it("is one object the store owns, so any engine reports into the same place", () => {
    const { engine } = webEngine();
    const player = new PlayerStore(engine);
    const events: EngineEvents = player.engineEvents;
    expect(typeof events.track).toBe("function");
  });
});