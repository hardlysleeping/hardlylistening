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

export class WebAudioEngine implements AudioEngine {
  readonly kind = "web" as const;
  readonly ownsQueue = false;

  #audio: HTMLAudioElement;
  #events: EngineEvents;
  #lastDuration = 0;

  constructor(events: EngineEvents) {
    this.#events = events;
    this.#audio = new Audio();
    this.#audio.preload = "metadata";

    this.#audio.addEventListener("timeupdate", () => {
      this.#events.time(this.#audio.currentTime || 0, this.duration);
    });
    this.#audio.addEventListener("durationchange", () => {
      if (this.duration !== this.#lastDuration) {
        this.#lastDuration = this.duration;
        this.#events.time(this.#audio.currentTime || 0, this.duration);
      }
    });
    this.#audio.addEventListener("ended", () => this.#events.ended());
    this.#audio.addEventListener("play", () => this.#events.playing(true));
    this.#audio.addEventListener("pause", () => this.#events.playing(false));
    this.#audio.addEventListener("waiting", () => this.#events.loading(true));
    this.#audio.addEventListener("playing", () => this.#events.loading(false));
    this.#audio.addEventListener("canplay", () => this.#events.loading(false));
    this.#audio.addEventListener("error", () => {
      this.#events.loading(false);
      this.#events.error("This track could not be played");
    });
  }

  /** The element itself, for a caller that needs the platform's own hooks. */
  get element(): HTMLAudioElement {
    return this.#audio;
  }

  get duration(): number {
    const value = this.#audio.duration;
    return Number.isFinite(value) && value > 0 ? value : 0;
  }

  get position(): number {
    return this.#audio.currentTime || 0;
  }

  setSource(url: string | null): void {
    if (url === null) {
      this.#audio.removeAttribute("src");
      this.#audio.load();
      return;
    }
    if (this.#audio.src !== url) {
      this.#events.loading(true);
      this.#audio.src = url;
      this.#audio.load();
    }
  }

  play(): void {
    // A rejected `play()` is the autoplay policy, not a broken track: the store keeps the queue
    // and the button stays a play button, which is the honest state.
    void this.#audio.play().catch(() => this.#events.playing(false));
  }

  pause(): void {
    this.#audio.pause();
  }

  seekFraction(fraction: number): void {
    const duration = this.duration;
    if (!duration) return;
    const target = Math.max(0, Math.min(1, fraction)) * duration;
    this.#audio.currentTime = target;
    this.#events.time(target, duration);
  }

  setVolume(volume: number): void {
    this.#audio.volume = Math.max(0, Math.min(1, volume));
  }

  setMuted(muted: boolean): void {
    this.#audio.muted = muted;
  }

  destroy(): void {
    this.#audio.pause();
    this.#audio.removeAttribute("src");
    this.#audio.load();
  }
}