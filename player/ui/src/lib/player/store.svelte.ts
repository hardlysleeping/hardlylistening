/* The player: one queue, one playhead, and the decisions about what plays next.
 *
 * What lives here and what does not is deliberate. This store owns the queue, the current
 * song, shuffle and repeat, and the rule for "the track ended". The engine owns bytes. The
 * client owns requests. A view paints what this says and calls these methods; no view holds a
 * copy of the playing song, because two copies of the truth is how the old page ended up
 * painting the same button from three places.
 *
 * Two things are not the store's to decide once the engine is native.
 *
 * The first is the bytes. A queue-owning engine is handed ids and an order; Rust resolves each
 * id to a stream address when it builds the platform queue, so `source()` is never called on
 * that path and no credential-in-a-URL reaches the page.
 *
 * The second is the advance. A page can call `advance()` when a track ends because a page is
 * running; with the screen off it is not, so the platform player moves through the order itself
 * and reports where it got to. The store follows (`track`) rather than leads. `advance()` still
 * owns the browser's rule, where a page is always running when a track ends.
 */

import type { Lyrics, MusicServer, Song } from "../subsonic";
import { WebAudioEngine, type AudioEngine, type EngineEvents } from "./engine";

export type RepeatMode = "off" | "all" | "one";

const VOLUME_KEY = "hl.player.volume";

function shuffled(count: number, first: number): number[] {
  const order = Array.from({ length: count }, (_, i) => i);
  for (let i = order.length - 1; i > 0; i--) {
    const j = Math.floor(Math.random() * (i + 1));
    [order[i], order[j]] = [order[j]!, order[i]!];
  }
  // The song that is already playing stays first: pressing shuffle is not a track change.
  if (first >= 0) {
    const at = order.indexOf(first);
    if (at > 0) [order[0], order[at]] = [order[at]!, order[0]!];
  }
  return order;
}

export class PlayerStore {
  track = $state<Song | null>(null);
  queue = $state<Song[]>([]);
  /** Position in `#order`, not in `queue`. */
  index = $state(-1);
  playing = $state(false);
  loading = $state(false);
  position = $state(0);
  duration = $state(0);
  shuffle = $state(false);
  repeat = $state<RepeatMode>("off");
  liked = $state(false);
  volume = $state(0.85);
  muted = $state(false);
  cover = $state<string | null>(null);
  /** The current song's lyrics, once the server has answered. `null` means none, as usual. */
  lyrics = $state<Lyrics | null>(null);
  /** Whether the overlay is showing. It lives here because both surfaces can open it. */
  lyricsOpen = $state(false);
  error = $state<string | null>(null);

  #engine: AudioEngine;
  #events: EngineEvents;
  #server: MusicServer | null = null;
  /* Reactive, because it is state: `upNext` and `upcoming` are readings of this list, and every
     edit used to change it *alongside* the queue, so a view was re-drawn by the queue's change
     rather than by the order's. The first edit that touches only the order — moving a row — was
     therefore applied and never drawn: the same list on screen, the new order playing. */
  #order = $state<number[]>([]);

  constructor(engine?: AudioEngine) {
    // One event object, made here, so every engine reports into the same place: the default web
    // engine is handed it, and a natively built engine is given `player.engineEvents`.
    this.#events = {
      time: (position, duration) => {
        this.position = position;
        this.duration = duration;
      },
      ended: () => void this.advance(),
      playing: (playing) => {
        this.playing = playing;
      },
      loading: (loading) => {
        this.loading = loading;
      },
      error: (message) => {
        this.error = message;
      },
      track: (index) => this.#follow(index),
    };

    this.#engine = engine ?? new WebAudioEngine(this.#events);
    const stored = Number(localStorage.getItem(VOLUME_KEY) ?? "0.85");
    this.volume = Number.isFinite(stored) ? Math.max(0, Math.min(1, stored)) : 0.85;
    this.#engine.setVolume(this.volume);
  }

  /** The events a natively built engine reports into. One seam, whoever builds the engine. */
  get engineEvents(): EngineEvents {
    return this.#events;
  }

  get engine(): AudioEngine {
    return this.#engine;
  }

  /** The engine a native build passes in, once it has one. */
  useEngine(engine: AudioEngine): void {
    this.#engine.destroy();
    this.#engine = engine;
    this.#engine.setVolume(this.volume);
    this.#engine.setMuted(this.muted);
    // A queue that exists is handed over rather than dropped: an engine swapped in after something
    // started would otherwise play the current song and then stop.
    if (this.queue.length) {
      this.#pushQueue();
      if (this.#engine.ownsQueue && this.index >= 0) this.#engine.playAt?.(this.index);
    }
  }

  attach(server: MusicServer): void {
    this.#server = server;
  }

  /**
   * The account is gone: nothing queued, nothing playing, nothing to explain.
   *
   * A queue belongs to the server it came from — a song id only means something to the server that
   * gave it out — so carrying one across a sign-out would leave the next connection holding ids it
   * has never heard of. The error line belongs to that account for the same reason: after signing
   * out of one server and in to another, "This track could not be played" read as a verdict on the
   * new one, over a library that had just loaded perfectly.
   *
   * The engine is stopped as well as emptied, because a signed-out server's audio must not keep
   * sounding and a native queue must not keep its ids.
   */
  detach(): void {
    this.#engine.pause();
    this.#engine.setSource(null);
    this.#server = null;
    this.#order = [];
    this.queue = [];
    this.index = -1;
    this.track = null;
    this.playing = false;
    this.loading = false;
    this.position = 0;
    this.duration = 0;
    this.cover = null;
    this.lyrics = null;
    this.lyricsOpen = false;
    this.liked = false;
    this.error = null;
    this.#pushQueue();
  }

  /** The song that plays next, for Up Next and for prefetch. */
  get upcoming(): Song[] {
    if (this.index < 0) return this.queue.slice(0, 5);
    return this.#order
      .slice(this.index + 1, this.index + 6)
      .map((at) => this.queue[at])
      .filter((song): song is Song => song !== undefined);
  }

  get progress(): number {
    return this.duration > 0 ? Math.max(0, Math.min(1, this.position / this.duration)) : 0;
  }

  /** Replace the queue and start at `startIndex`. */
  async playQueue(songs: readonly Song[], startIndex = 0): Promise<void> {
    const list = songs.slice();
    if (!list.length) return;
    this.queue = list;
    this.#order = this.shuffle ? shuffled(list.length, startIndex) : list.map((_, i) => i);
    const index = this.shuffle ? 0 : Math.max(0, Math.min(startIndex, list.length - 1));
    this.#pushQueue();
    await this.#start(index);
  }

  async playNow(song: Song): Promise<void> {
    this.queue = [song];
    this.#order = [0];
    this.#pushQueue();
    await this.#start(0);
  }

  /** Add to the end without disturbing what is playing. */
  enqueue(songs: readonly Song[], options: { next?: boolean } = {}): void {
    if (!songs.length) return;
    if (!this.track) {
      void this.playQueue(songs, 0);
      return;
    }
    if (options.next) {
      const insertAt = this.index < 0 ? this.queue.length : this.#order[this.index]! + 1;
      this.queue.splice(insertAt, 0, ...songs);
      /* Shift by *position*, not by membership. Every entry at or after the insertion point moves
       * up, including one that happens to land on a number the new songs also take — asking
       * `added.includes(at)` cannot tell a new entry from an old one at the same number, so the
       * entry that was going to play next kept its old index, played the inserted song a second
       * time, and disappeared from the queue. */
      const shifted = this.#order.map((at) => (at >= insertAt ? at + songs.length : at));
      const added = songs.map((_, i) => insertAt + i);
      shifted.splice(this.index < 0 ? 0 : this.index + 1, 0, ...added);
      this.#order = shifted;
    } else {
      const base = this.queue.length;
      this.queue.push(...songs);
      this.#order.push(...songs.map((_, i) => base + i));
    }
    // A queue-owning player keeps its own copy of the order; an edit it is not told about is an
    // edit it will not play.
    this.#pushQueue();
  }

  /** The songs after the current one, with their position in the up-next list. */
  get upNext(): Song[] {
    if (this.index < 0) return this.queue.slice();
    return this.#order
      .slice(this.index + 1)
      .map((at) => this.queue[at])
      .filter((song): song is Song => song !== undefined);
  }

  /* ---- editing the queue ---------------------------------------------------------------- */

  async playUpNext(position: number): Promise<void> {
    const orderAt = this.index + 1 + position;
    if (this.#order[orderAt] === undefined) return;
    // One path for both engines: `#start` is where "the order is the player's" is decided.
    await this.#start(orderAt);
  }

  /**
   * Move an up-next song to another position in the up-next list, 0 being the song after the
   * playhead.
   *
   * Only `#order` changes: the queue keeps every song, and the playhead is a position in the
   * order that is never crossed, so nothing needs renumbering and the current audio cannot be
   * touched by a reorder. That is what makes this safe to do mid-playback on a queue-owning
   * player too — `#pushQueue` hands over the new order, and its contract keeps the item that is
   * playing when that item is still in the order.
   */
  moveUpNext(from: number, to: number): void {
    const start = Math.max(0, this.index + 1);
    const count = this.#order.length - start;
    if (from === to) return;
    if (from < 0 || to < 0 || from >= count || to >= count) return;
    const [moved] = this.#order.splice(start + from, 1);
    if (moved === undefined) return;
    this.#order.splice(start + to, 0, moved);
    this.#pushQueue();
  }

  removeUpNext(position: number): void {
    const orderAt = this.index + 1 + position;
    if (orderAt <= this.index || orderAt >= this.#order.length) return;
    const queueAt = this.#order[orderAt]!;
    this.#order.splice(orderAt, 1);
    this.queue.splice(queueAt, 1);
    // Every index past the removed song shifts down by one, in both lists.
    this.#order = this.#order.map((at) => (at > queueAt ? at - 1 : at));
    this.#pushQueue();
  }

  clearUpcoming(): void {
    const kept = this.index >= 0 ? this.#order.slice(0, this.index + 1) : [];
    const songs = kept.map((at) => this.queue[at]).filter((song): song is Song => song !== undefined);
    this.queue = songs;
    this.#order = songs.map((_, i) => i);
    this.index = songs.length ? Math.min(this.index, songs.length - 1) : -1;
    this.#pushQueue();
  }

  async toggle(): Promise<void> {
    if (!this.track) return;
    if (this.playing) this.#engine.pause();
    else this.#engine.play();
  }

  async next(): Promise<void> {
    if (this.#engine.ownsQueue) {
      // The player holds the order and knows where it is; asking it to skip is the whole job.
      // The index moves when it reports `track`, so the button cannot disagree with the sound.
      this.#engine.skip?.(1);
      return;
    }
    const nextAt = this.index + 1;
    if (nextAt < this.#order.length) {
      await this.#start(nextAt);
      return;
    }
    if (this.repeat === "all" && this.#order.length) {
      await this.#start(0);
      return;
    }
    this.#engine.pause();
  }

  async previous(): Promise<void> {
    if (this.#engine.ownsQueue) {
      // The three-second rule lives with the playhead it is a rule about.
      this.#engine.skip?.(-1);
      return;
    }
    // A press after three seconds restarts the song, which is what every player does and what a
    // press meant by accident should not undo.
    if (this.position > 3) {
      this.#engine.seekFraction(0);
      return;
    }
    const previousAt = this.index - 1;
    if (previousAt >= 0) {
      await this.#start(previousAt);
    } else {
      this.#engine.seekFraction(0);
    }
  }

  /**
   * What happens when a track ends.
   *
   * A queue-owning player has already moved on by the time it says `ended` — that word means the
   * order ran out. There is nothing to start, and starting something would make the page and the
   * player disagree about what is playing.
   */
  async advance(): Promise<void> {
    if (this.#engine.ownsQueue) {
      this.playing = false;
      return;
    }
    if (this.repeat === "one") {
      this.#engine.seekFraction(0);
      this.#engine.play();
      return;
    }
    await this.next();
  }

  seekTo(fraction: number): void {
    this.#engine.seekFraction(fraction);
    this.position = fraction * this.duration;
  }

  setVolume(volume: number): void {
    const clamped = Math.max(0, Math.min(1, volume));
    this.volume = clamped;
    this.muted = clamped === 0;
    this.#engine.setVolume(clamped);
    this.#engine.setMuted(this.muted);
    try {
      localStorage.setItem(VOLUME_KEY, String(clamped));
    } catch {
      /* A private window refuses storage; the fader still works for this session. */
    }
  }

  toggleMute(): void {
    this.muted = !this.muted;
    this.#engine.setMuted(this.muted);
  }

  cycleRepeat(): void {
    this.repeat = this.repeat === "off" ? "all" : this.repeat === "all" ? "one" : "off";
    // Told, not re-pushed: replacing the order to change a mode would restart the track.
    this.#engine.setRepeat?.(this.repeat);
  }

  toggleShuffle(): void {
    this.shuffle = !this.shuffle;
    const currentQueueSpot = this.index >= 0 ? this.#order[this.index]! : 0;
    this.#order = this.shuffle
      ? shuffled(this.queue.length, currentQueueSpot)
      : this.queue.map((_, i) => i);
    this.index = this.shuffle ? 0 : currentQueueSpot;
    // A queue-owning player has its own copy of the order, so the new one is handed over and
    // playback resumes at the song the person is on. That restarts the track — the cost of
    // changing the order underneath a player that is holding it.
    this.#pushQueue();
    if (this.#engine.ownsQueue && this.index >= 0 && this.queue.length) {
      this.#engine.playAt?.(this.index);
    }
  }

  /** The line being sung, as an index into `lyrics.line`. `-1` when nothing is timed. */
  get lyricsLine(): number {
    const lines = this.lyrics?.line;
    if (!lines || !this.lyrics?.synced) return -1;
    let index = -1;
    for (let i = 0; i < lines.length; i += 1) {
      const start = lines[i]?.start;
      if (typeof start === "number" && start <= this.position) index = i;
      else if (typeof start === "number") break;
    }
    return index;
  }

  toggleLyrics(): void {
    if (!this.lyrics || this.lyrics.line?.length === 0 && !this.lyrics.value) return;
    this.lyricsOpen = !this.lyricsOpen;
  }

  closeLyrics(): void {
    this.lyricsOpen = false;
  }

  async toggleLike(): Promise<void> {
    const song = this.track;
    if (!song || !this.#server) return;
    const nextLiked = !this.liked;
    this.liked = nextLiked;
    try {
      if (nextLiked) await this.#server.star(song.id);
      else await this.#server.unstar(song.id);
    } catch (error) {
      this.liked = !nextLiked;
      this.error = error instanceof Error ? error.message : "That favourite did not save";
    }
  }

  /* ---- what a song looks like, and what starts it ------------------------------------------ */

  /**
   * Everything a surface paints about a song, plus the two decoration requests that may be slow.
   *
   * Nothing here plays. Cover and lyrics are asked for in the background and land only if this is
   * still the current song, so a slow answer for track seven cannot paint over track eight.
   */
  #present(song: Song): void {
    this.error = null;
    this.track = song;
    this.position = 0;
    this.duration = song.duration ?? 0;
    this.liked = Boolean(song.starred);
    // A new track has new words; the old ones must not be read against the new clock, and the
    // reading closes with them. The host hides its overlay on `loadstart` for the same reason:
    // left open over a track the server has nothing for, it is an empty panel with a close
    // button and no explanation — words that belong to the song before, or none at all.
    this.lyrics = null;
    this.lyricsOpen = false;

    const server = this.#server;
    if (!server) return;

    void server
      .cover(song.coverArt, 300)
      .then((url) => {
        if (this.track?.id === song.id) this.cover = url;
      })
      .catch(() => {
        if (this.track?.id === song.id) this.cover = null;
      });

    // Lyrics are not part of playing: a server that will not answer still plays the song.
    void server
      .lyrics(song.id)
      .then((lyrics) => {
        if (this.track?.id === song.id) this.lyrics = lyrics;
      })
      .catch(() => {
        if (this.track?.id === song.id) this.lyrics = null;
      });
  }

  /**
   * Start the song at `index` in the order.
   *
   * On a queue-owning player this is one instruction and no request: the order and the addresses
   * are already the player's. On the browser path this is the one place a song becomes bytes.
   */
  async #start(index: number): Promise<void> {
    const at = this.#order[index];
    const song = at === undefined ? undefined : this.queue[at];
    if (!song) return;

    this.index = index;
    this.#present(song);

    if (this.#engine.ownsQueue) {
      this.#engine.playAt?.(index);
      return;
    }

    const server = this.#server;
    if (!server) return;
    try {
      const source = await server.source(song.id);
      if (this.track?.id !== song.id) return;
      this.#engine.setSource(source.url);
      this.#engine.play();
    } catch (error) {
      this.error = error instanceof Error ? error.message : "This track could not be played";
    }
  }

  /**
   * Follow a queue-owning player to the item it moved to.
   *
   * State only: the player is already playing it. `#start` would issue a `playAt` and restart the
   * track the person is hearing.
   */
  #follow(index: number): void {
    const at = this.#order[index];
    const song = at === undefined ? undefined : this.queue[at];
    if (!song) return;
    this.index = index;
    this.#present(song);
  }

  /**
   * Hand a queue-owning player the order it should be playing.
   *
   * The contract, and the reason the queue edits above can call this mid-playback: the player
   * keeps the current item if it is still in the new order, and starts from the beginning
   * otherwise. `playAt` is the explicit "start this one".
   */
  #pushQueue(): void {
    this.#engine.setQueue?.(this.queue, this.#order, this.repeat);
  }
}
