/* Kept copies: songs this device holds so a bad connection cannot stop the music.
 *
 * The rule this store exists to keep: **a kept song plays from this device first.** The bytes
 * live in the app's own folder (Rust, `keep.rs`) and are served to the `<audio>` element from a
 * loopback address, so the page never learns a path and the CSP needs nothing new.
 *
 * Everything here is a wrapper: the folder, the index and the byte arithmetic are Rust's, and
 * this file only remembers what the page is allowed to know — which songs are held, how far a
 * fetch has got, and whether the copy on disk is the one to play.
 */

import type { Song } from "../subsonic";

/** One song this device holds, as the index describes it. */
export interface KeptSong {
  id: string;
  title: string;
  artist: string;
  album: string;
  suffix: string;
  bytes: number;
  keptAt: number;
}

/** How far one fetch has got. `total` is absent when the server did not say. */
export interface KeepProgress {
  received: number;
  total: number | null;
}

type Invoke = (command: string, args?: Record<string, unknown>) => Promise<unknown>;

type Listen = (event: string, handler: (payload: unknown) => void) => Promise<unknown>;

/** The event Rust sends while a song is being fetched. One name, both sides. */
export const KEEP_EVENT = "hl:keep";

export class KeepStore {
  rows = $state<KeptSong[]>([]);
  /** Fetches in flight, by song id. A key present means "being kept right now". */
  progress = $state<Record<string, KeepProgress>>({});
  error = $state<string | null>(null);

  #invoke: Invoke | null = null;
  #accountId = "";
  #subscribed = false;

  /** Bind to a signed-in account. Only the transport's `invoke` and the account id are needed. */
  attach(invoke: Invoke, accountId: string): void {
    this.#invoke = invoke;
    this.#accountId = accountId;
    this.#listen();
    void this.refresh();
  }

  detach(): void {
    this.#invoke = null;
    this.#accountId = "";
    this.rows = [];
    this.progress = {};
  }

  get ready(): boolean {
    return this.#invoke !== null;
  }

  get bytes(): number {
    return this.rows.reduce((sum, row) => sum + row.bytes, 0);
  }

  has(id: string): boolean {
    return this.rows.some((row) => row.id === id);
  }

  /** True while this song is being fetched, so the row can say so instead of offering it twice. */
  keeping(id: string): boolean {
    return this.progress[id] !== undefined;
  }

  /** Re-read the folder's index. Cheap: one file, no network. */
  async refresh(): Promise<void> {
    const invoke = this.#invoke;
    if (!invoke) return;
    try {
      const rows = await invoke("keep_list");
      this.rows = Array.isArray(rows) ? (rows as KeptSong[]) : [];
      this.error = null;
    } catch (error) {
      // A build without the keep commands (an older shell) says so once, quietly: the app
      // still plays from the server, which is what it did before this existed.
      this.error = error instanceof Error ? error.message : "This device cannot keep songs";
    }
  }

  /**
   * Keep one song. Resolves true when the copy is on disk.
   *
   * The song's own fields travel with the request so the folder can be read without the
   * server: a phone in a tunnel has no catalogue, and a list of ids would be a list of numbers.
   */
  async keep(song: Song): Promise<boolean> {
    const invoke = this.#invoke;
    if (!invoke) return false;
    if (!song.id || this.has(song.id) || this.keeping(song.id)) return this.has(song.id);
    this.progress = { ...this.progress, [song.id]: { received: 0, total: null } };
    try {
      await invoke("keep_add", {
        accountId: this.#accountId,
        song: {
          id: song.id,
          title: song.title ?? "",
          artist: song.artist ?? "",
          album: song.album ?? "",
          suffix: song.suffix ?? "",
        },
      });
      await this.refresh();
      return this.has(song.id);
    } catch (error) {
      this.error = error instanceof Error ? error.message : "That song could not be kept";
      return false;
    } finally {
      const { [song.id]: _gone, ...rest } = this.progress;
      this.progress = rest;
    }
  }

  /** Keep everything in a list, one at a time: a queue of fetches is Rust's business, not the
   * page's, and two at once on a bad connection is how neither finishes. */
  async keepAll(songs: readonly Song[]): Promise<void> {
    for (const song of songs) {
      if (this.has(song.id)) continue;
      const kept = await this.keep(song);
      if (!kept) return; // One refusal stops the run: the next would fail the same way.
    }
  }

  async forget(id: string): Promise<void> {
    const invoke = this.#invoke;
    if (!invoke) return;
    try {
      await invoke("keep_remove", { songId: id });
      this.rows = this.rows.filter((row) => row.id !== id);
    } catch (error) {
      this.error = error instanceof Error ? error.message : "That song could not be removed";
    }
  }

  async clear(): Promise<void> {
    const invoke = this.#invoke;
    if (!invoke) return;
    try {
      await invoke("keep_clear");
      this.rows = [];
    } catch (error) {
      this.error = error instanceof Error ? error.message : "The kept songs could not be removed";
    }
  }

  /** The address to play a kept song from, or null when it is not held here. */
  async sourceFor(id: string): Promise<string | null> {
    const invoke = this.#invoke;
    if (!invoke || !this.has(id)) return null;
    try {
      const url = await invoke("keep_source", { songId: id });
      return typeof url === "string" && url ? url : null;
    } catch {
      return null; // Play from the server instead; a keep that cannot be read is not an error.
    }
  }

  #listen(): void {
    if (this.#subscribed) return;
    const listen = tauriListen();
    if (!listen) return;
    this.#subscribed = true;
    void listen(KEEP_EVENT, (payload) => {
      const event = (payload ?? {}) as { id?: unknown; received?: unknown; total?: unknown };
      const id = typeof event.id === "string" ? event.id : "";
      if (!id) return;
      const received = typeof event.received === "number" ? event.received : 0;
      const total = typeof event.total === "number" ? event.total : null;
      if (!(id in this.progress)) return; // A straggler from a fetch already finished.
      this.progress = { ...this.progress, [id]: { received, total } };
    });
  }
}

/** The shell's event bridge, when there is one. Absent in a browser and in tests. */
function tauriListen(): Listen | null {
  const candidate = (globalThis as { __TAURI__?: { event?: { listen?: Listen } } }).__TAURI__;
  const listen = candidate?.event?.listen;
  return typeof listen === "function" ? listen.bind(candidate?.event) : null;
}