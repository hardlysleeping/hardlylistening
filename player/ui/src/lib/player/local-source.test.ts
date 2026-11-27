/* A kept copy beats the stream.
 *
 * The whole point of keeping songs is the moment the network is not there: if the player asked
 * the server first, a kept song would fail on a tunnel exactly like an unkept one. These tests
 * pin that order, and that a song which is *not* held still plays from the server as before.
 */

import { describe, expect, it, vi } from "vitest";
import { PlayerStore } from "./store.svelte";
import type { AudioEngine } from "./engine";
import type { MusicServer, Song } from "../subsonic";

const SONG: Song = { id: "42", title: "A song", duration: 200 };

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
    setRepeat: vi.fn(),
    destroy: vi.fn(),
  };
}

/** The store reads the saved volume and the saved queue at construction; tests have neither. */
function store(music: MusicServer): { player: PlayerStore; engine: AudioEngine } {
  (globalThis as unknown as { localStorage: unknown }).localStorage = {
    getItem: () => null,
    setItem: () => {},
  };
  const engine = stubEngine();
  const player = new PlayerStore(engine);
  player.attach(music);
  return { player, engine };
}

function server(): MusicServer {
  return {
    source: vi.fn(async (id: string) => ({ url: `http://stream/${id}` })),
    cover: vi.fn(async () => null),
    lyrics: vi.fn(async () => null),
  } as unknown as MusicServer;
}

describe("where a song is played from", () => {
  it("plays the kept copy without asking the server for a stream", async () => {
    const music = server();
    const { player, engine } = store(music);
    player.useLocalSource(async (id) => (id === "42" ? "http://127.0.0.1:9/42" : null));

    await player.playQueue([SONG], 0);

    expect(engine.setSource).toHaveBeenCalledWith("http://127.0.0.1:9/42");
    expect(music.source).not.toHaveBeenCalled();
    expect(engine.play).toHaveBeenCalled();
  });

  it("streams a song this device does not hold", async () => {
    const music = server();
    const { player, engine } = store(music);
    player.useLocalSource(async () => null);

    await player.playQueue([SONG], 0);

    expect(engine.setSource).toHaveBeenCalledWith("http://stream/42");
  });

  it("streams when the kept copy cannot be resolved instead of failing the play", async () => {
    const music = server();
    const { player, engine } = store(music);
    player.useLocalSource(async () => {
      throw new Error("the file is gone");
    });

    await player.playQueue([SONG], 0);

    expect(engine.setSource).toHaveBeenCalledWith("http://stream/42");
    expect(player.error).toBeNull();
  });
});