/* What the operating system is told about the song that is playing, and what it may do about it.
 *
 * The player's own surfaces are not the only place a person presses play. A locked phone, a
 * notification, a Bluetooth head unit and a car dash all draw from the platform's media session:
 * a tag (artwork, title, artist, album), a transport (play, pause, next, previous, seek) and a
 * timeline. None of that exists unless the page claims it, and until this file existed the
 * player claimed none of it — the assembled web app had a media session (`web/js/playback.js`)
 * and the player, which shares its styles but not its scripts, silently had no lock screen.
 *
 * Two rules are taken from that implementation because they were learned the hard way.
 *
 * The first is that `seekbackward` and `seekforward` are *cleared*, not claimed. Registering
 * either replaces the platform's skip pair with a ±10 s pair, which is the one thing that loses
 * next/previous on a car dash.
 *
 * The second is that a timeline never moves backwards without a real seek. `timeupdate` fires
 * about four times a second and the reported position wobbles; a published clock that follows
 * that wobble makes a head unit's elapsed time jump back a second at a time. The published value
 * keeps a high-water mark, and only a jump larger than `POSITION_JITTER` is read as the listener
 * having moved the playhead.
 *
 * What this cannot do: keep the audio alive. Background playback belongs to the shell's audio
 * session and the media element, not to a script — a suspended page runs no callbacks. This file
 * never schedules a timer for the same reason: every decision is taken when the store says
 * something changed, so a frozen and resumed page reports the truth rather than replaying a
 * queue of stale decisions.
 *
 * The platform is injected (`session`, `metadata`) rather than reached for, so the rules above
 * are testable outside a browser — the same seam `web/js/playback.js` uses.
 */

import type { Song } from "../subsonic";

/** How often the timeline may be pushed. A car draws seconds; `timeupdate` fires at ~4 Hz. */
export const POSITION_INTERVAL_MS = 1000;

/** Inside this window a smaller value is jitter, not a seek. */
export const POSITION_JITTER_SECONDS = 0.5;

const ARTWORK_SIZES = "512x512";

/** Offered to the platform, in this order. */
export const TRANSPORT_ACTIONS = ["play", "pause", "previoustrack", "nexttrack", "seekto"] as const;

/** Offered nowhere: claiming either turns the skip pair into ±10 s. */
export const REFUSED_ACTIONS = ["seekbackward", "seekforward"] as const;

/** The part of the page's state the session is a picture of. */
export interface MediaSnapshot {
  track: Song | null;
  playing: boolean;
  position: number;
  duration: number;
  /** The resolved cover address for `track`, when the server has answered one. */
  artwork: string | null;
}

/** What a transport press does. Every method is the store's own, never a second player. */
export interface MediaTarget {
  play(): void;
  pause(): void;
  next(): void;
  previous(): void;
  /** Jump to a fraction of the track, 0..1 — the player's only seek. */
  seekTo(fraction: number): void;
}

/** The slice of `navigator.mediaSession` this file uses. Absent on a platform without one. */
export interface MediaSessionLike {
  metadata: unknown;
  playbackState: "none" | "paused" | "playing";
  setActionHandler(action: string, handler: ((details: MediaSessionActionDetails) => void) | null): void;
  setPositionState?(state?: { duration: number; playbackRate: number; position: number }): void;
}

/** `MediaMetadata`, when the platform has it. */
export type MetadataConstructor = new (init: {
  title?: string;
  artist?: string;
  album?: string;
  artwork?: Array<{ src: string; sizes?: string; type?: string }>;
}) => unknown;

export interface NowPlayingOptions {
  /** Where the truth is read from, on every publish. */
  source(): MediaSnapshot;
  target: MediaTarget;
  /** Defaults to `navigator.mediaSession`; `null` is a platform without one. */
  session?: MediaSessionLike | null;
  /** Defaults to `MediaMetadata`; `null` keeps the plain tag object. */
  metadata?: MetadataConstructor | null;
  /** A clock, for the throttle. `performance.now` by default. */
  now?: () => number;
}

/* WebKit draws no artwork entry without a declared type, so the type is read from the address the
   cover route actually served; anything unrecognised is the JPEG a Subsonic cover route returns. */
export function artworkType(url: string): string {
  const clean = url.split("#")[0]!.split("?")[0]!;
  const found = clean.match(/\.([a-z0-9]{2,4})$/i);
  const extension = found ? found[1]!.toLowerCase() : "";
  if (extension === "png") return "image/png";
  if (extension === "webp") return "image/webp";
  return "image/jpeg";
}

function clampPosition(at: number, duration: number): number {
  const safe = Number.isFinite(at) && at > 0 ? at : 0;
  return Number.isFinite(duration) && duration > 0 ? Math.min(safe, duration) : safe;
}

function text(value: unknown): string {
  return value == null ? "" : String(value);
}

/**
 * One media session, kept in step with the store.
 *
 * `publish()` is idempotent and cheap: it diffs the snapshot against what the platform was last
 * told, so a caller may run it on every `timeupdate` without re-tagging the lock screen four
 * times a second.
 */
export class NowPlaying {
  #source: () => MediaSnapshot;
  #target: MediaTarget;
  #session: MediaSessionLike | null;
  #metadata: MetadataConstructor | null;
  #now: () => number;

  /** The last tag published, so an unchanged song is not re-published. */
  #tag: { id: string; artwork: string } | null = null;
  #state: "none" | "paused" | "playing" | null = null;
  #publishedAt = -POSITION_INTERVAL_MS;
  #publishedValue = 0;
  #claimed = false;

  constructor(options: NowPlayingOptions) {
    this.#source = options.source;
    this.#target = options.target;
    this.#session = options.session === undefined ? defaultSession() : options.session;
    this.#metadata = options.metadata === undefined ? defaultMetadata() : options.metadata;
    this.#now = options.now ?? (() => (typeof performance !== "undefined" ? performance.now() : Date.now()));
  }

  /** Whether this build has a platform session to talk to. */
  get available(): boolean {
    return this.#session !== null;
  }

  /**
   * Claim the transport. Called once, and again after a track change, because WebKit drops the
   * handlers when a session goes inactive (a pause, a background, a PWA resume) and the lock
   * screen then falls back to the platform's own layout.
   */
  claim(): void {
    const session = this.#session;
    if (!session || typeof session.setActionHandler !== "function") return;
    for (const action of REFUSED_ACTIONS) set(session, action, null);
    const handlers: Record<(typeof TRANSPORT_ACTIONS)[number], (details: MediaSessionActionDetails) => void> = {
      play: () => this.#target.play(),
      pause: () => this.#target.pause(),
      previoustrack: () => this.#target.previous(),
      nexttrack: () => this.#target.next(),
      seekto: (details) => {
        const at = Number(details?.seekTime);
        const { duration } = this.#source();
        if (!Number.isFinite(at) || at < 0 || !Number.isFinite(duration) || duration <= 0) return;
        this.#target.seekTo(Math.max(0, Math.min(1, at / duration)));
      },
    };
    for (const action of TRANSPORT_ACTIONS) set(session, action, handlers[action]);
    this.#claimed = true;
  }

  /** Publish whatever has changed. Safe to call on every store update. */
  publish(): void {
    const session = this.#session;
    if (!session) return;
    if (!this.#claimed) this.claim();

    const state = this.#source();
    this.#publishTag(state);
    this.#publishState(state);
    this.#publishPosition(state);
  }

  /** Nothing is playing here any more: no tag, no timeline, no transport. */
  clear(): void {
    const session = this.#session;
    this.#tag = null;
    this.#state = null;
    this.#publishedAt = -POSITION_INTERVAL_MS;
    this.#publishedValue = 0;
    this.#claimed = false;
    if (!session) return;
    try {
      session.metadata = null;
      session.playbackState = "none";
    } catch {
      /* A platform that refuses the clear keeps its last tag rather than throwing. */
    }
    if (typeof session.setActionHandler === "function") {
      for (const action of TRANSPORT_ACTIONS) set(session, action, null);
      for (const action of REFUSED_ACTIONS) set(session, action, null);
    }
  }

  #publishTag(state: MediaSnapshot): void {
    const song = state.track;
    if (!song) {
      if (this.#tag !== null) {
        this.#tag = null;
        try {
          this.#session!.metadata = null;
        } catch {
          /* keep the previous tag */
        }
      }
      return;
    }
    const artwork = text(state.artwork);
    if (this.#tag && this.#tag.id === song.id && this.#tag.artwork === artwork) return;

    // A track change re-claims the transport: the session may have gone inactive since.
    const changedTrack = !this.#tag || this.#tag.id !== song.id;
    this.#tag = { id: song.id, artwork };
    if (changedTrack) {
      this.#publishedAt = -POSITION_INTERVAL_MS;
      this.#publishedValue = 0;
      this.claim();
    }

    const tag = {
      title: text(song.title) || "Untitled",
      artist: text(song.artist),
      album: text(song.album),
      artwork: artwork
        ? [{ src: artwork, sizes: ARTWORK_SIZES, type: artworkType(artwork) }]
        : [],
    };
    try {
      this.#session!.metadata = this.#metadata ? new this.#metadata(tag) : tag;
    } catch {
      /* A platform with no usable MediaMetadata keeps the previous tag rather than showing
         nothing — the same fallback the assembled app uses. */
    }
  }

  #publishState(state: MediaSnapshot): void {
    const wanted = state.track ? (state.playing ? "playing" : "paused") : "none";
    if (wanted === this.#state) return;
    this.#state = wanted;
    try {
      this.#session!.playbackState = wanted;
    } catch {
      /* the tag still carries the song */
    }
  }

  #publishPosition(state: MediaSnapshot): void {
    const session = this.#session;
    if (!session || typeof session.setPositionState !== "function") return;
    const duration = Number(state.duration);
    if (!state.track || !Number.isFinite(duration) || duration <= 0) return;

    const now = this.#now();
    if (now - this.#publishedAt < POSITION_INTERVAL_MS) return;

    let value = clampPosition(Number(state.position), duration);
    // A backwards jump bigger than the jitter window is the listener moving the playhead; a
    // smaller one is the element's own rounding and keeps the high-water mark.
    const seeked = value < this.#publishedValue - POSITION_JITTER_SECONDS;
    if (!seeked && value < this.#publishedValue) value = this.#publishedValue;
    value = clampPosition(value, duration);

    try {
      session.setPositionState({ duration, playbackRate: 1, position: value });
    } catch {
      return;
    }
    this.#publishedAt = now;
    this.#publishedValue = value;
  }
}

function set(session: MediaSessionLike, action: string, handler: ((details: MediaSessionActionDetails) => void) | null): void {
  try {
    session.setActionHandler(action, handler);
  } catch {
    /* An action the platform does not offer is not an error. */
  }
}

function defaultSession(): MediaSessionLike | null {
  if (typeof navigator === "undefined") return null;
  const session = (navigator as Navigator & { mediaSession?: MediaSessionLike }).mediaSession;
  return session ?? null;
}

function defaultMetadata(): MetadataConstructor | null {
  const ctor = (globalThis as { MediaMetadata?: MetadataConstructor }).MediaMetadata;
  return ctor ?? null;
}