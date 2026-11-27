/* The engine seam.
 *
 * Two engines implement it: the browser's `<audio>`, and — on a phone — the platform's own player
 * behind Rust. The interface is what makes that a swap rather than a rewrite: the store owns the
 * queue and the decisions, the engine owns bytes and a playhead.
 *
 * One property is not symmetric and is the reason `ownsQueue` exists. A page can call
 * `advance()` when a track ends, because a page is running. On a locked phone it is not: iOS's
 * `AVQueuePlayer` and Android's Media3 session keep playing with the app suspended, and the next
 * track has to come from the player. So an engine that owns the queue is handed the playback
 * order once and reports back which item it moved to; an engine that does not is driven track by
 * track, and the store asks the server for each source as it goes.
 */

import type { Song } from "../subsonic";

export type Repeat = "off" | "all" | "one";

export interface EngineEvents {
  /** Fired while playing, roughly four times a second. */
  time(positionSeconds: number, durationSeconds: number): void;
  /** The current source finished. The *store* decides what plays next, unless it owns the queue. */
  ended(): void;
  playing(playing: boolean): void;
  loading(loading: boolean): void;
  error(message: string): void;
  /**
   * A queue-owning engine moved to another item in the order it was given, by itself or because
   * it was asked to. The store updates what it shows; it does not start anything.
   */
  track?(index: number): void;
}

export interface AudioEngine {
  readonly kind: "web" | "native";
  /** True when the engine advances through the queue itself and reports where it is. */
  readonly ownsQueue: boolean;

  setSource(url: string | null): void;
  play(): void;
  pause(): void;
  /** Jump to a fraction of the track, 0..1. */
  seekFraction(fraction: number): void;
  setVolume(volume: number): void;
  setMuted(muted: boolean): void;
  destroy(): void;

  /**
   * Fetch a source that is expected next, without playing it.
   *
   * A press must not be the first thing that asks for bytes: that made the gap between the tap
   * and the sound the whole of the network round trip plus the media pipeline's start-up. An
   * engine that can hold a second buffer warms it and is then asked to play an address it
   * already has. Optional, because a queue-owning player does this itself.
   */
  preload?(url: string | null): void;

  /* The queue-owning half. A web engine has none of these, because the store does their job. */

  /** The playback order, as positions in `songs`, and what to do at the end. No URLs. */
  setQueue?(songs: readonly Song[], order: readonly number[], repeat: Repeat): void;
  /** Start the item at `index` in that order. */
  playAt?(index: number): void;
  /** Move one item forward or back. The player applies the "previous restarts after 3 s" rule. */
  skip?(direction: 1 | -1): void;
  /** Change the repeat mode without replacing the order. */
  setRepeat?(repeat: Repeat): void;
}

/** What `preload` leaves behind: enough buffered to start without asking the network again. */
const HAVE_CURRENT_DATA = 2;

export class WebAudioEngine implements AudioEngine {
  readonly kind = "web" as const;
  readonly ownsQueue = false;

  /** The element the person is hearing. */
  #active: HTMLAudioElement;
  /** The second buffer. Its bytes were fetched ahead of the press; it is silent until it swaps. */
  #standby: HTMLAudioElement;
  #events: EngineEvents;
  #lastDuration = 0;

  /** The factory is injectable so a test can drive two fake elements instead of a browser's. */
  constructor(events: EngineEvents, make: () => HTMLAudioElement = () => new Audio()) {
    this.#events = events;
    this.#active = this.#element(make);
    this.#standby = this.#element(make);
  }

  /**
   * One element, wired to the shared events.
   *
   * Both elements are wired, and every handler asks whether its element is the active one: a
   * standby finishing its fetch (`canplay`) or being unloaded must not move the playhead, the
   * loading state or the play button.
   */
  #element(make: () => HTMLAudioElement): HTMLAudioElement {
    const audio = make();
    audio.preload = "metadata";
    const mine = () => audio === this.#active;

    audio.addEventListener("timeupdate", () => {
      if (mine()) this.#events.time(audio.currentTime || 0, this.duration);
    });
    audio.addEventListener("durationchange", () => {
      if (!mine()) return;
      if (this.duration !== this.#lastDuration) {
        this.#lastDuration = this.duration;
        this.#events.time(audio.currentTime || 0, this.duration);
      }
    });
    audio.addEventListener("ended", () => {
      if (mine()) this.#events.ended();
    });
    audio.addEventListener("play", () => {
      if (mine()) this.#events.playing(true);
    });
    audio.addEventListener("pause", () => {
      if (mine()) this.#events.playing(false);
    });
    audio.addEventListener("waiting", () => {
      if (mine()) this.#events.loading(true);
    });
    audio.addEventListener("playing", () => {
      if (mine()) this.#events.loading(false);
    });
    audio.addEventListener("canplay", () => {
      if (mine()) this.#events.loading(false);
    });
    audio.addEventListener("error", () => {
      if (!mine()) return;
      this.#events.loading(false);
      this.#events.error("This track could not be played");
    });
    return audio;
  }

  /** Silence an element and let its buffer go. Called only after it has stopped being active. */
  #drop(audio: HTMLAudioElement): void {
    audio.pause();
    audio.removeAttribute("src");
    audio.load();
  }

  #level(audio: HTMLAudioElement, volume: number, muted: boolean): void {
    audio.volume = Math.max(0, Math.min(1, volume));
    audio.muted = muted;
  }

  /** The element itself, for a caller that needs the platform's own hooks. */
  get element(): HTMLAudioElement {
    return this.#active;
  }

  get duration(): number {
    const value = this.#active.duration;
    return Number.isFinite(value) && value > 0 ? value : 0;
  }

  get position(): number {
    return this.#active.currentTime || 0;
  }

  setSource(url: string | null): void {
    if (url === null) {
      this.#drop(this.#active);
      this.#drop(this.#standby);
      return;
    }
    if (this.#active.src === url) return;
    // The warmed copy: swap rather than load. The element the press lands on has already got
    // its bytes, so `play()` starts from the buffer instead of from a request.
    if (this.#standby.src === url && this.#standby.readyState >= HAVE_CURRENT_DATA) {
      const spent = this.#active;
      this.#active = this.#standby;
      this.#standby = spent;
      // Dropped while it is no longer active, so its own `pause` cannot reach the store and
      // flicker the button between the two tracks.
      this.#drop(spent);
      return;
    }
    this.#events.loading(true);
    this.#active.src = url;
    this.#active.load();
  }

  preload(url: string | null): void {
    if (url === null) {
      this.#drop(this.#standby);
      return;
    }
    if (this.#standby.src === url || this.#active.src === url) return;
    // `auto` here and nowhere else: this element exists to fetch ahead, and its bytes are ones
    // the queue would have asked for at the next press anyway.
    this.#standby.preload = "auto";
    this.#standby.src = url;
    this.#standby.load();
  }

  play(): void {
    // A rejected `play()` is the autoplay policy, not a broken track: the store keeps the queue
    // and the button stays a play button, which is the honest state.
    void this.#active.play().catch(() => this.#events.playing(false));
  }

  pause(): void {
    this.#active.pause();
  }

  seekFraction(fraction: number): void {
    const duration = this.duration;
    if (!duration) return;
    const target = Math.max(0, Math.min(1, fraction)) * duration;
    this.#active.currentTime = target;
    this.#events.time(target, duration);
  }

  setVolume(volume: number): void {
    // Both, because a swap must not land at the default level.
    this.#level(this.#active, volume, this.#active.muted);
    this.#level(this.#standby, volume, this.#standby.muted);
  }

  setMuted(muted: boolean): void {
    this.#active.muted = muted;
    this.#standby.muted = muted;
  }

  destroy(): void {
    this.#drop(this.#active);
    this.#drop(this.#standby);
  }
}