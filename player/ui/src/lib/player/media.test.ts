/* The lock screen has no test rig, so the rules that make it correct are pinned here instead:
 * which actions are claimed and which are refused, what a tag says, when it is re-published, and
 * the two timeline rules (a throttle, and never backwards without a seek).
 *
 * Measured on a device is a different card (HL-220). What these cases prove is that the page
 * asks the platform for the right things, with fakes standing in for `navigator.mediaSession`.
 */

import { describe, expect, it, vi } from "vitest";
import {
  NowPlaying,
  POSITION_INTERVAL_MS,
  REFUSED_ACTIONS,
  TRANSPORT_ACTIONS,
  artworkType,
  type MediaSessionLike,
  type MediaSnapshot,
  type MediaTarget,
} from "./media";
import type { Song } from "../subsonic";

const SONG: Song = { id: "song-1", title: "A song", artist: "An artist", album: "An album", duration: 200 };
const NEXT: Song = { id: "song-2", title: "Another", artist: "An artist", album: "An album", duration: 180 };

/** A platform session that only records what it was told. */
function fakeSession(overrides: Partial<MediaSessionLike> = {}) {
  const handlers = new Map<string, ((details: unknown) => void) | null>();
  const session: MediaSessionLike = {
    metadata: undefined,
    playbackState: "none",
    setActionHandler: vi.fn((action: string, handler: ((details: unknown) => void) | null) => {
      handlers.set(action, handler);
    }),
    setPositionState: vi.fn(),
    ...overrides,
  };
  return { session, handlers };
}

function fakeTarget(): MediaTarget {
  return {
    play: vi.fn(),
    pause: vi.fn(),
    next: vi.fn(),
    previous: vi.fn(),
    seekTo: vi.fn(),
  };
}

function snapshot(overrides: Partial<MediaSnapshot> = {}) {
  let current: MediaSnapshot = {
    track: SONG,
    playing: true,
    position: 10,
    duration: 200,
    artwork: "https://server/rest/getCoverArt.view?id=art-1",
    ...overrides,
  };
  return {
    source: () => current,
    set(patch: Partial<MediaSnapshot>) {
      current = { ...current, ...patch };
    },
  };
}

/** A clock a test can move by hand. */
function clock() {
  let at = 0;
  return {
    now: () => at,
    advance(ms: number) {
      at += ms;
    },
  };
}

describe("artworkType", () => {
  it("reads the served type and defaults to the JPEG a cover route returns", () => {
    expect(artworkType("https://server/a/cover.png")).toBe("image/png");
    expect(artworkType("https://server/a/cover.PNG?size=512")).toBe("image/png");
    expect(artworkType("https://server/a/cover.webp#x")).toBe("image/webp");
    expect(artworkType("https://server/rest/getCoverArt.view?id=1")).toBe("image/jpeg");
  });
});

describe("NowPlaying", () => {
  it("claims the transport and refuses the pair that would replace the skips", () => {
    const { session, handlers } = fakeSession();
    const now = new NowPlaying({ source: snapshot().source, target: fakeTarget(), session, metadata: null });

    now.claim();

    for (const action of TRANSPORT_ACTIONS) {
      expect(handlers.get(action), action).toBeTypeOf("function");
    }
    for (const action of REFUSED_ACTIONS) {
      expect(handlers.get(action), action).toBeNull();
    }
  });

  it("publishes a tag with artwork, and the song's own album", () => {
    const { session } = fakeSession();
    const tags: unknown[] = [];
    class Metadata {
      constructor(init: unknown) {
        tags.push(init);
      }
    }
    const now = new NowPlaying({ source: snapshot().source, target: fakeTarget(), session, metadata: Metadata });

    now.publish();

    expect(tags).toHaveLength(1);
    expect(tags[0]).toEqual({
      title: "A song",
      artist: "An artist",
      album: "An album",
      artwork: [
        { src: "https://server/rest/getCoverArt.view?id=art-1", sizes: "512x512", type: "image/jpeg" },
      ],
    });
    expect(session.metadata).toBeInstanceOf(Metadata);
  });

  it("keeps a plain tag on a platform without MediaMetadata", () => {
    const { session } = fakeSession();
    const now = new NowPlaying({ source: snapshot({ artwork: null }).source, target: fakeTarget(), session, metadata: null });

    now.publish();

    expect(session.metadata).toEqual({
      title: "A song",
      artist: "An artist",
      album: "An album",
      artwork: [],
    });
  });

  it("does not re-tag the same song, and re-tags when the cover lands", () => {
    const { session } = fakeSession();
    const tag = vi.fn();
    class Metadata {
      constructor(init: unknown) {
        tag(init);
      }
    }
    const { source, set } = snapshot({ artwork: null });
    const now = new NowPlaying({ source, target: fakeTarget(), session, metadata: Metadata });

    now.publish();
    now.publish();
    now.publish();
    expect(tag).toHaveBeenCalledTimes(1);

    // The cover is asked for in the background and lands after the song has started.
    set({ artwork: "https://server/art/1.png" });
    now.publish();
    expect(tag).toHaveBeenCalledTimes(2);
    expect((tag.mock.calls[1]![0] as { artwork: unknown[] }).artwork).toEqual([
      { src: "https://server/art/1.png", sizes: "512x512", type: "image/png" },
    ]);
  });

  it("re-claims the transport on a track change, because a session drops its handlers", () => {
    const { session } = fakeSession();
    const { source, set } = snapshot();
    const now = new NowPlaying({ source, target: fakeTarget(), session, metadata: null });

    now.publish();
    const claimed = (session.setActionHandler as ReturnType<typeof vi.fn>).mock.calls.length;

    set({ track: NEXT, position: 0, duration: 180 });
    now.publish();

    expect((session.setActionHandler as ReturnType<typeof vi.fn>).mock.calls.length).toBeGreaterThan(claimed);
  });

  it("tells the platform whether it is playing, paused, or empty", () => {
    const { session } = fakeSession();
    const { source, set } = snapshot();
    const now = new NowPlaying({ source, target: fakeTarget(), session, metadata: null });

    now.publish();
    expect(session.playbackState).toBe("playing");

    set({ playing: false });
    now.publish();
    expect(session.playbackState).toBe("paused");

    set({ track: null });
    now.publish();
    expect(session.playbackState).toBe("none");
    expect(session.metadata).toBeNull();
  });

  it("throttles the timeline, and never reports it moving backwards on jitter", () => {
    const { session } = fakeSession();
    const time = clock();
    const { source, set } = snapshot({ position: 10 });
    const now = new NowPlaying({ source, target: fakeTarget(), session, metadata: null, now: time.now });

    now.publish();
    expect(session.setPositionState).toHaveBeenLastCalledWith({ duration: 200, playbackRate: 1, position: 10 });

    // Four `timeupdate`s inside a second are one push, not four.
    time.advance(250);
    set({ position: 10.25 });
    now.publish();
    expect(session.setPositionState).toHaveBeenCalledTimes(1);

    time.advance(POSITION_INTERVAL_MS);
    set({ position: 9.8 });
    now.publish();
    expect(session.setPositionState).toHaveBeenLastCalledWith({ duration: 200, playbackRate: 1, position: 10 });
  });

  it("follows the listener moving the playhead back", () => {
    const { session } = fakeSession();
    const time = clock();
    const { source, set } = snapshot({ position: 60 });
    const now = new NowPlaying({ source, target: fakeTarget(), session, metadata: null, now: time.now });

    now.publish();
    time.advance(POSITION_INTERVAL_MS);
    set({ position: 5 });
    now.publish();

    expect(session.setPositionState).toHaveBeenLastCalledWith({ duration: 200, playbackRate: 1, position: 5 });
  });

  it("starts the timeline again from zero on a new song", () => {
    const { session } = fakeSession();
    const time = clock();
    const { source, set } = snapshot({ position: 150 });
    const now = new NowPlaying({ source, target: fakeTarget(), session, metadata: null, now: time.now });

    now.publish();
    time.advance(POSITION_INTERVAL_MS);
    set({ track: NEXT, position: 0, duration: 180 });
    now.publish();

    expect(session.setPositionState).toHaveBeenLastCalledWith({ duration: 180, playbackRate: 1, position: 0 });
  });

  it("turns a lock-screen seek into a fraction of the track", () => {
    const { session, handlers } = fakeSession();
    const target = fakeTarget();
    const now = new NowPlaying({ source: snapshot({ duration: 200 }).source, target, session, metadata: null });

    now.claim();
    handlers.get("seekto")?.({ seekTime: 50 });

    expect(target.seekTo).toHaveBeenCalledWith(0.25);
  });

  it("ignores a seek it could not place, rather than seeking to nothing", () => {
    const { session, handlers } = fakeSession();
    const target = fakeTarget();
    const now = new NowPlaying({ source: snapshot({ duration: 0 }).source, target, session, metadata: null });

    now.claim();
    handlers.get("seekto")?.({ seekTime: 50 });
    handlers.get("seekto")?.({});

    expect(target.seekTo).not.toHaveBeenCalled();
  });

  it("hands the transport to the store's own methods", () => {
    const { session, handlers } = fakeSession();
    const target = fakeTarget();
    const now = new NowPlaying({ source: snapshot().source, target, session, metadata: null });

    now.claim();
    handlers.get("play")?.({});
    handlers.get("pause")?.({});
    handlers.get("nexttrack")?.({});
    handlers.get("previoustrack")?.({});

    expect(target.play).toHaveBeenCalledTimes(1);
    expect(target.pause).toHaveBeenCalledTimes(1);
    expect(target.next).toHaveBeenCalledTimes(1);
    expect(target.previous).toHaveBeenCalledTimes(1);
  });

  it("is inert on a platform with no media session", () => {
    const target = fakeTarget();
    const now = new NowPlaying({ source: snapshot().source, target, session: null, metadata: null });

    expect(now.available).toBe(false);
    expect(() => now.claim()).not.toThrow();
    expect(() => now.publish()).not.toThrow();
    expect(() => now.clear()).not.toThrow();
    expect(target.play).not.toHaveBeenCalled();
  });

  it("clears the tag, the timeline and the transport on sign-out", () => {
    const { session, handlers } = fakeSession();
    const now = new NowPlaying({ source: snapshot().source, target: fakeTarget(), session, metadata: null });

    now.publish();
    now.clear();

    expect(session.metadata).toBeNull();
    expect(session.playbackState).toBe("none");
    for (const action of [...TRANSPORT_ACTIONS, ...REFUSED_ACTIONS]) {
      expect(handlers.get(action), action).toBeNull();
    }
  });

  it("keeps going when the platform throws instead of refusing", () => {
    const { session } = fakeSession({
      setPositionState: vi.fn(() => {
        throw new Error("not supported");
      }),
    });
    const now = new NowPlaying({ source: snapshot().source, target: fakeTarget(), session, metadata: null });

    expect(() => now.publish()).not.toThrow();
    expect(session.playbackState).toBe("playing");
  });
});