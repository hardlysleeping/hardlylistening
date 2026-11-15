/* The native audio engine, as the page sees it.
 *
 * Two properties matter, and neither is about sound:
 *
 * - **The page sends ids and an order, never a URL.** Rust resolves each id to a stream address
 *   when it builds the platform player's queue, so the credential-in-the-page that P2 left behind
 *   for `<audio>` stops existing on a phone.
 * - **The platform player advances the queue itself.** With the screen off and the app suspended
 *   there is no JavaScript to call `advance()`, so the order is handed over once and the player
 *   reports back what it did.
 *
 * Everything here is checked against a fake `invoke` and a fake event channel; the platform
 * player itself is not on this machine (see `docs/player/native-audio.md`).
 */

import { describe, expect, it, vi } from "vitest";
import type { EngineEvents } from "./engine";
import type { Song } from "../subsonic";

const { NativeAudioEngine, NATIVE_AUDIO_EVENT } = await import("./native-engine");

function song(id: string): Song {
  return { id, title: `Song ${id}` };
}

function events(): EngineEvents & Record<string, ReturnType<typeof vi.fn>> {
  return {
    time: vi.fn(),
    ended: vi.fn(),
    playing: vi.fn(),
    loading: vi.fn(),
    error: vi.fn(),
    track: vi.fn(),
  } as never;
}

/** A Tauri-shaped event channel: handlers are recorded, and the test fires them. */
function channel() {
  const handlers = new Map<string, (payload: unknown) => void>();
  const unlisten = vi.fn();
  const listen = vi.fn(async (name: string, handler: (payload: unknown) => void) => {
    handlers.set(name, handler);
    return unlisten;
  });
  const emit = (name: string, payload: unknown) => handlers.get(name)?.(payload);
  return { listen, emit, unlisten };
}

function engine(recorded: Array<[string, Record<string, unknown>]>, listen?: unknown) {
  const invoke = vi.fn(async (command: string, args?: Record<string, unknown>) => {
    recorded.push([command, args ?? {}]);
    return undefined;
  });
  const bus = channel();
  const native = new NativeAudioEngine({
    invoke,
    listen: (listen as typeof bus.listen | undefined) ?? bus.listen,
    events: events(),
  });
  return { native, invoke, bus };
}

describe("the native audio engine", () => {
  it("queues ids in playback order and starts at an index, never a URL", async () => {
    const recorded: Array<[string, Record<string, unknown>]> = [];
    const { native } = engine(recorded);

    const queue = [song("a"), song("b"), song("c")];
    native.setQueue(queue, [2, 0, 1], "all");
    native.playAt(1);

    expect(recorded.map(([command]) => command)).toEqual(["native_audio_queue", "native_audio_play"]);
    expect(recorded[0]?.[1]).toEqual({ ids: ["c", "a", "b"], repeat: "all" });
    expect(recorded[1]?.[1]).toEqual({ index: 1 });
    // Nothing that could be a stream address or a credential is in the message.
    expect(JSON.stringify(recorded)).not.toContain("://");
  });

  it("owns the queue, and says so", () => {
    const { native } = engine([]);
    expect(native.ownsQueue).toBe(true);
    expect(native.kind).toBe("native");
  });

  it("turns the player's events into engine events", () => {
    const recorded: Array<[string, Record<string, unknown>]> = [];
    const seen = events();
    const bus = channel();
    const native = new NativeAudioEngine({
      invoke: async () => undefined,
      listen: bus.listen,
      events: seen,
    });

    bus.emit(NATIVE_AUDIO_EVENT, { kind: "track", index: 3 });
    bus.emit(NATIVE_AUDIO_EVENT, { kind: "position", position: 12.5, duration: 200 });
    bus.emit(NATIVE_AUDIO_EVENT, { kind: "playing", playing: true });
    bus.emit(NATIVE_AUDIO_EVENT, { kind: "ended" });
    bus.emit(NATIVE_AUDIO_EVENT, { kind: "error", message: "the file is gone" });

    expect(seen.track).toHaveBeenCalledWith(3);
    expect(seen.time).toHaveBeenCalledWith(12.5, 200);
    expect(seen.playing).toHaveBeenCalledWith(true);
    expect(seen.ended).toHaveBeenCalled();
    expect(seen.error).toHaveBeenCalledWith("the file is gone");
    expect(recorded).toHaveLength(0);
    void native;
  });

  it("seeks in seconds, using the duration the player reported", () => {
    const recorded: Array<[string, Record<string, unknown>]> = [];
    const bus = channel();
    const native = new NativeAudioEngine({
      invoke: async (command, args) => {
        recorded.push([command, args ?? {}]);
      },
      listen: bus.listen,
      events: events(),
    });

    // A seek before any duration is known has nothing to compute with, and says nothing.
    native.seekFraction(0.5);
    expect(recorded).toHaveLength(0);

    bus.emit(NATIVE_AUDIO_EVENT, { kind: "position", position: 0, duration: 200 });
    native.seekFraction(0.25);

    expect(recorded).toEqual([["native_audio_seek", { seconds: 50 }]]);
  });

  it("asks the player to skip rather than working out the next index itself", () => {
    const recorded: Array<[string, Record<string, unknown>]> = [];
    const { native } = engine(recorded);

    native.skip(1);
    native.skip(-1);

    expect(recorded).toEqual([
      ["native_audio_skip", { direction: 1 }],
      ["native_audio_skip", { direction: -1 }],
    ]);
  });

  it("sets the repeat mode without disturbing the queue", () => {
    const recorded: Array<[string, Record<string, unknown>]> = [];
    const { native } = engine(recorded);

    native.setRepeat("one");

    expect(recorded).toEqual([["native_audio_repeat", { repeat: "one" }]]);
  });

  it("stops listening when it is destroyed", async () => {
    const bus = channel();
    const native = new NativeAudioEngine({
      invoke: async () => undefined,
      listen: bus.listen,
      events: events(),
    });
    await Promise.resolve();

    native.destroy();
    await Promise.resolve();

    expect(bus.unlisten).toHaveBeenCalled();
  });
});