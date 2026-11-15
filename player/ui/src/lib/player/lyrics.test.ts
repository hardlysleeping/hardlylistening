/* Lyrics: which line is being sung, and what the overlay is allowed to show.
 *
 * The reading itself is the server's; what can go wrong here is arithmetic. A line's `start` is
 * a time in seconds, the playhead is a time in seconds, and the line being sung is the last one
 * whose start has passed — not the next one, and not the first one before anything is timed.
 */

import { describe, expect, it } from "vitest";
import type { AudioEngine } from "./engine";
import type { Lyrics } from "../subsonic";

// The store reads the saved volume at construction; a node test has no storage.
(globalThis as unknown as { localStorage: unknown }).localStorage = {
  getItem: () => null,
  setItem: () => {},
};

const engine: AudioEngine = {
  kind: "web",
  ownsQueue: false,
  setSource: () => {},
  play: () => {},
  pause: () => {},
  seekFraction: () => {},
  setVolume: () => {},
  setMuted: () => {},
  destroy: () => {},
};

const { PlayerStore } = await import("./store.svelte");

const timed: Lyrics = {
  synced: true,
  line: [
    { start: 0, value: "one" },
    { start: 4, value: "two" },
    { start: 9.5, value: "three" },
    { value: "four" },
  ],
};

function store(): InstanceType<typeof PlayerStore> {
  return new PlayerStore(engine);
}

describe("the line being sung", () => {
  it("is nothing at all when the lyrics are not timed", () => {
    const player = store();
    player.lyrics = { value: "just a paragraph of words" };
    player.position = 12;
    expect(player.lyricsLine).toBe(-1);
  });

  it("is nothing before the first timed line, and that is the state the overlay opens in", () => {
    const player = store();
    player.lyrics = { synced: true, line: [{ start: 2, value: "one" }] };
    player.position = 0;
    expect(player.lyricsLine).toBe(-1);
  });

  it("is the last line whose start has passed", () => {
    const player = store();
    player.lyrics = timed;
    const at = (position: number) => {
      player.position = position;
      return player.lyricsLine;
    };
    expect(at(0)).toBe(0);
    expect(at(3.9)).toBe(0);
    expect(at(4)).toBe(1);
    expect(at(9.49)).toBe(1);
    expect(at(9.5)).toBe(2);
    expect(at(600)).toBe(2);
  });

  it("stops at the last timed line even if a later line has no start", () => {
    const player = store();
    player.lyrics = timed;
    player.position = 60;
    // Line three is the last one carrying a time; the untimed line is not guessed at.
    expect(player.lyricsLine).toBe(2);
  });
});

describe("opening the overlay", () => {
  it("does nothing when there is nothing to show", () => {
    const player = store();
    player.toggleLyrics();
    expect(player.lyricsOpen).toBe(false);
  });

  it("opens and closes when the server sent words", () => {
    const player = store();
    player.lyrics = timed;
    player.toggleLyrics();
    expect(player.lyricsOpen).toBe(true);
    player.toggleLyrics();
    expect(player.lyricsOpen).toBe(false);
    player.toggleLyrics();
    player.closeLyrics();
    expect(player.lyricsOpen).toBe(false);
  });
});