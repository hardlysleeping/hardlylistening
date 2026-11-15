/* The native engine: the platform's player, reached over the bridge.
 *
 * The difference from `WebAudioEngine` is not where the sound comes from — it is what the page is
 * allowed to say. This engine is handed **ids and an order**; Rust turns each id into a stream
 * address when it builds the platform queue, so on a phone the credential-in-the-page that
 * `<audio>` needs never exists at all.
 *
 * The other difference is who advances. With the screen off the platform keeps playing and the
 * page may not be running, so the player moves through the order itself and reports `track`.
 *
 * Rust command names are the contract with `src-tauri/src/native_audio.rs`; the platform player
 * behind it is described in `docs/player/native-audio.md` and is not on this machine.
 */

import type { InvokeLike, Song } from "../subsonic";
import type { AudioEngine, EngineEvents, Repeat } from "./engine";

/** One channel for everything the player has to say; `kind` says which. */
export const NATIVE_AUDIO_EVENT = "hl:native-audio";

/** Tauri's `listen`, as far as this module needs it. */
export type ListenLike = (
  event: string,
  handler: (payload: unknown) => void,
) => Promise<() => void>;

type NativeEvent =
  | { kind: "track"; index: number }
  | { kind: "ended" }
  | { kind: "playing"; playing: boolean }
  | { kind: "position"; position: number; duration: number }
  | { kind: "error"; message: string };

export interface NativeAudioEngineOptions {
  invoke: InvokeLike;
  /** `null` when the shell has no event channel; the engine still plays, the page just cannot see. */
  listen?: ListenLike | null;
  events: EngineEvents;
}

function number(value: unknown): number {
  return typeof value === "number" && Number.isFinite(value) ? value : 0;
}

export class NativeAudioEngine implements AudioEngine {
  readonly kind = "native" as const;
  readonly ownsQueue = true;

  #invoke: InvokeLike;
  #events: EngineEvents;
  #duration = 0;
  #unlisten: (() => void) | null = null;

  constructor(options: NativeAudioEngineOptions) {
    this.#invoke = options.invoke;
    this.#events = options.events;
    if (options.listen) {
      void options
        .listen(NATIVE_AUDIO_EVENT, (payload) => this.#receive(payload))
        .then((unlisten) => {
          this.#unlisten = unlisten;
        })
        .catch(() => {
          /* No channel is a quiet page, not a broken player. */
        });
    }
  }

  /** The queue, as ids in the order they will play. A repeat change does not pass through here. */
  setQueue(songs: readonly Song[], order: readonly number[], repeat: Repeat): void {
    const ids = order.map((at) => songs[at]?.id).filter((id): id is string => typeof id === "string");
    void this.#invoke("native_audio_queue", { ids, repeat });
  }

  playAt(index: number): void {
    void this.#invoke("native_audio_play", { index });
  }

  skip(direction: 1 | -1): void {
    void this.#invoke("native_audio_skip", { direction });
  }

  setRepeat(repeat: Repeat): void {
    void this.#invoke("native_audio_repeat", { repeat });
  }

  /**
   * Not part of this engine's job, and kept loud on purpose: a URL reaching here would mean the
   * page built one, which is the property P3 exists to remove.
   */
  setSource(url: string | null): void {
    if (url !== null) {
      throw new Error("A native engine is given ids, never stream addresses");
    }
  }

  play(): void {
    void this.#invoke("native_audio_resume");
  }

  pause(): void {
    void this.#invoke("native_audio_pause");
  }

  seekFraction(fraction: number): void {
    if (!this.#duration) return;
    const seconds = Math.max(0, Math.min(1, fraction)) * this.#duration;
    void this.#invoke("native_audio_seek", { seconds });
  }

  setVolume(volume: number): void {
    void this.#invoke("native_audio_volume", { volume: Math.max(0, Math.min(1, volume)) });
  }

  setMuted(muted: boolean): void {
    void this.#invoke("native_audio_mute", { muted });
  }

  destroy(): void {
    void this.#invoke("native_audio_stop");
    this.#unlisten?.();
    this.#unlisten = null;
  }

  #receive(payload: unknown): void {
    if (typeof payload !== "object" || payload === null) return;
    const message = payload as NativeEvent & { [key: string]: unknown };
    switch (message.kind) {
      case "track":
        this.#events.track?.(number(message.index));
        return;
      case "position":
        this.#duration = number(message.duration);
        this.#events.time(number(message.position), this.#duration);
        return;
      case "playing":
        this.#events.playing(message.playing === true);
        return;
      case "ended":
        this.#events.ended();
        return;
      case "error":
        this.#events.error(typeof message.message === "string" ? message.message : "Playback stopped");
        return;
      default:
        return;
    }
  }
}

/** The shell's event channel, when the window has one. */
export function nativeListen(): ListenLike | null {
  const candidate = (
    globalThis as {
      __TAURI__?: { event?: { listen?: ListenLike } };
    }
  ).__TAURI__;
  const listen = candidate?.event?.listen;
  return typeof listen === "function" ? listen : null;
}