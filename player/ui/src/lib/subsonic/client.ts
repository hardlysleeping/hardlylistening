/* One typed Subsonic client.
 *
 * Everything the UI knows about a server comes through here. It owns three decisions the old
 * bridge repeated in four places: how an envelope is unwrapped, which error a refusal becomes,
 * and what a "page" means. It owns no state of its own beyond the server's capabilities, so
 * there is exactly one place to look when the library shows the wrong thing.
 *
 * Ids are opaque strings, passed through unchanged.
 */

import { API_VERSION, CLIENT_NAME } from "./auth";
import {
  capabilitiesFrom,
  looksLikeUnsupported,
  pageRequest,
  DEFAULT_PAGE_SIZE,
  type Capabilities,
} from "./capabilities";
import { NotSubsonicError, SubsonicError, SUBSONIC_ERRORS as KNOWN_ERRORS } from "./errors";
import { flattenArtistIndexes, extensionsFrom, isBag, list, text, toAlbum, toArtist, toPlaylist, toServerInfo, toSongs } from "./normalize";
import type { SubsonicTransport, StreamOptions } from "./transport";
import type {
  Album,
  AlbumListOptions,
  AlbumPage,
  Artist,
  Id,
  Lyrics,
  PlayableSource,
  Playlist,
  PlaylistChange,
  PlayQueue,
  SearchOptions,
  SearchResults,
  ServerInfo,
  Song,
} from "./types";

type Bag = Record<string, unknown>;

/** The seam the app talks to. A test can satisfy it with a fixture; nothing else may. */
export interface MusicServer {
  ping(): Promise<ServerInfo>;

  artists(): Promise<Artist[]>;
  artist(id: Id): Promise<Artist>;
  albums(options?: AlbumListOptions): Promise<AlbumPage>;
  album(id: Id): Promise<Album>;
  playlists(): Promise<Playlist[]>;
  playlist(id: Id): Promise<Playlist>;
  /** Make a playlist on the server, owned by this account and saved into this account's scope. */
  createPlaylist(name: string): Promise<Playlist>;
  /** Rename a playlist, add one song to its end, or remove the song at a position. */
  updatePlaylist(id: Id, change: PlaylistChange): Promise<void>;

  search(query: string, options?: SearchOptions): Promise<SearchResults>;

  star(id: Id): Promise<void>;
  unstar(id: Id): Promise<void>;
  starred(): Promise<SearchResults>;

  /** Structured lyrics for one song, or `null` when the server has none. */
  lyrics(songId: Id): Promise<Lyrics | null>;

  source(id: Id, options?: StreamOptions): Promise<PlayableSource>;
  cover(coverArtId: string | undefined, size?: number): Promise<string | null>;
}

export interface SubsonicClientOptions {
  transport: SubsonicTransport;
  apiVersion?: string;
  clientName?: string;
  pageSize?: number;
}

/** The four ways a Subsonic server wraps a refusal. */
function unwrap(raw: unknown, endpoint: string): Bag {
  if (!isBag(raw)) {
    throw new NotSubsonicError("The server's answer was not a JSON document");
  }
  const envelope = raw["subsonic-response"];
  if (!isBag(envelope)) {
    throw new NotSubsonicError(
      "The server answered, but not as a Subsonic server (there is no subsonic-response in the reply)",
    );
  }

  const error = envelope["error"];
  if (isBag(error)) {
    const code = Number(error["code"]);
    const said = text(error["message"]).trim();
    const known = KNOWN_ERRORS[code];
    const message = known && said && said !== known ? `${known} — ${said}` : known || said || "The server refused the request";
    throw new SubsonicError(Number.isFinite(code) ? code : 0, message, endpoint);
  }

  if (text(envelope["status"]) !== "ok") {
    throw new SubsonicError(0, `The server answered with status “${text(envelope["status"])}” instead of ok`, endpoint);
  }

  return envelope;
}

export class SubsonicClient implements MusicServer {
  readonly transport: SubsonicTransport;

  #apiVersion: string;
  #clientName: string;
  #pageSize: number;
  #info: ServerInfo | null = null;
  #capabilities: Capabilities | null = null;

  constructor(options: SubsonicClientOptions) {
    this.transport = options.transport;
    this.#apiVersion = options.apiVersion ?? API_VERSION;
    this.#clientName = options.clientName ?? CLIENT_NAME;
    this.#pageSize = options.pageSize ?? DEFAULT_PAGE_SIZE;
  }

  /** What the last `ping` found. `null` before the first successful call. */
  get capabilities(): Capabilities | null {
    return this.#capabilities;
  }

  async ping(): Promise<ServerInfo> {
    const envelope = await this.#call("ping", {});
    let info = toServerInfo(envelope);
    // A server can announce OpenSubsonic in `ping` and keep the extension list for its own
    // endpoint — Navidrome 0.64 does exactly that. Asking once is what turns "this server speaks
    // OpenSubsonic" into capabilities the client can act on; a server without the endpoint
    // refuses, and a refusal here is not a failed sign-in.
    if (info.openSubsonic && info.extensions.length === 0) {
      info = { ...info, extensions: await this.#announcedExtensions() };
    }
    this.#info = info;
    this.#capabilities = capabilitiesFrom(info);
    this.#capabilities.pageSize = this.#pageSize;
    return info;
  }

  /** `getOpenSubsonicExtensions`, or nothing at all when the server has never heard of it. */
  async #announcedExtensions(): Promise<string[]> {
    try {
      const envelope = await this.#call("getOpenSubsonicExtensions", {});
      return extensionsFrom(envelope["openSubsonicExtensions"]);
    } catch {
      // Code 0 ("Unknown method"), 70, an XML page, a 404: all of them mean "no list", and none
      // of them mean the server is unusable.
      return [];
    }
  }

  /** `ping` if it has not happened yet. */
  async ensureCapabilities(): Promise<Capabilities> {
    if (this.#capabilities) return this.#capabilities;
    await this.ping();
    return this.#capabilities!;
  }

  /* ---- library ------------------------------------------------------------------------- */

  async albums(options: AlbumListOptions = {}): Promise<AlbumPage> {
    const capabilities = await this.ensureCapabilities();
    const { offset, size } = pageRequest(capabilities, options.offset ?? 0, options.size);
    const type = options.type ?? "alphabeticalByName";

    let envelope: Bag;
    try {
      envelope = await this.#call("getAlbumList2", {
        type,
        offset,
        size,
        genre: options.genre,
        fromYear: options.fromYear,
        toYear: options.toYear,
        musicFolderId: options.musicFolderId,
      });
    } catch (error) {
      // A server that cannot page albums is a fact about the server, not a broken request.
      if (error instanceof SubsonicError && looksLikeUnsupported(capabilities, error.code)) {
        capabilities.albumListPaging = false;
      }
      throw error;
    }

    capabilities.albumListPaging = true;
    const albums = list<unknown>(isBag(envelope["albumList2"]) ? envelope["albumList2"]["album"] : undefined)
      .map(toAlbum)
      .filter((album): album is Album => album !== null);

    return { albums, offset, maybeMore: albums.length >= size };
  }

  async album(id: Id): Promise<Album> {
    const envelope = await this.#call("getAlbum", { id });
    const album = toAlbum(envelope["album"]);
    if (!album) throw new SubsonicError(70, "The server could not find that album", "getAlbum");
    return album;
  }

  async artists(): Promise<Artist[]> {
    const envelope = await this.#call("getArtists", {});
    const container = envelope["artists"];
    const artists = isBag(container) ? flattenArtistIndexes(container["index"]) : [];
    // Some servers answer `getArtists` with nothing but an empty index. That is not a failure.
    return artists;
  }

  async artist(id: Id): Promise<Artist> {
    const envelope = await this.#call("getArtist", { id });
    const artist = toArtist(envelope["artist"]);
    if (!artist) throw new SubsonicError(70, "The server could not find that artist", "getArtist");
    return artist;
  }

  /* ---- playlists ----------------------------------------------------------------------- */

  async playlists(): Promise<Playlist[]> {
    const envelope = await this.#call("getPlaylists", {});
    const container = envelope["playlists"];
    return isBag(container)
      ? list<unknown>(container["playlist"])
          .map(toPlaylist)
          .filter((playlist): playlist is Playlist => playlist !== null)
      : [];
  }

  async playlist(id: Id): Promise<Playlist> {
    const envelope = await this.#call("getPlaylist", { id });
    const playlist = toPlaylist(envelope["playlist"]);
    if (!playlist) throw new SubsonicError(70, "The server could not find that playlist", "getPlaylist");
    return playlist;
  }

  async createPlaylist(name: string): Promise<Playlist> {
    const envelope = await this.#call("createPlaylist", { name });
    const playlist = toPlaylist(envelope["playlist"]);
    // Every server that implements this answers with the playlist it made. A `status: ok` with
    // no row means the request landed and the result is unknown, which is not something to
    // invent a list entry from.
    if (!playlist) throw new SubsonicError(70, "The server did not return the playlist it made", "createPlaylist");
    return playlist;
  }

  async updatePlaylist(id: Id, change: PlaylistChange): Promise<void> {
    const params =
      "name" in change
        ? { playlistId: id, name: change.name }
        : "add" in change
          ? { playlistId: id, songIdToAdd: change.add }
          : { playlistId: id, songIndexToRemove: change.removeIndex };
    await this.#call("updatePlaylist", params);
  }

  /* ---- search -------------------------------------------------------------------------- */

  async search(query: string, options: SearchOptions = {}): Promise<SearchResults> {
    const trimmed = query.trim();
    if (!trimmed) return { artists: [], albums: [], songs: [] };

    const envelope = await this.#call("search3", {
      query: trimmed,
      artistCount: options.artistCount,
      artistOffset: options.artistOffset,
      albumCount: options.albumCount,
      albumOffset: options.albumOffset,
      songCount: options.songCount,
      songOffset: options.songOffset,
      musicFolderId: options.musicFolderId,
    });

    const container = envelope["searchResult3"] ?? envelope["searchResult2"];
    if (!isBag(container)) return { artists: [], albums: [], songs: [] };

    return {
      artists: list<unknown>(container["artist"])
        .map(toArtist)
        .filter((artist): artist is Artist => artist !== null),
      albums: list<unknown>(container["album"])
        .map(toAlbum)
        .filter((album): album is Album => album !== null),
      songs: toSongs(container["song"]),
    };
  }

  /* ---- favourites ---------------------------------------------------------------------- */

  async star(id: Id): Promise<void> {
    await this.#call("star", { id });
  }

  async unstar(id: Id): Promise<void> {
    await this.#call("unstar", { id });
  }

  async starred(): Promise<SearchResults> {
    const envelope = await this.#call("getStarred2", {});
    const container = envelope["starred2"];
    if (!isBag(container)) return { artists: [], albums: [], songs: [] };
    return {
      artists: list<unknown>(container["artist"])
        .map(toArtist)
        .filter((artist): artist is Artist => artist !== null),
      albums: list<unknown>(container["album"])
        .map(toAlbum)
        .filter((album): album is Album => album !== null),
      songs: toSongs(container["song"]),
    };
  }

  /* ---- playback ------------------------------------------------------------------------ */

  async source(id: Id, options?: StreamOptions): Promise<PlayableSource> {
    return this.transport.source(id, options);
  }

  async cover(coverArtId: string | undefined, size?: number): Promise<string | null> {
    return this.transport.cover(coverArtId, size);
  }

  /** A play tells the server something was played. `submission` is the "this counted" flag. */
  async scrobble(id: Id, options: { time?: number; submission?: boolean } = {}): Promise<void> {
    await this.#call("scrobble", {
      id,
      time: options.time,
      submission: options.submission === false ? "false" : undefined,
    });
  }

  async lyrics(songId: Id): Promise<Lyrics | null> {
    const capabilities = await this.ensureCapabilities();
    if (!capabilities.has("songLyrics")) return null;

    const envelope = await this.#call("getLyricsBySongId", { id: songId });
    const container = envelope["lyricsList"];
    const rows = isBag(container) ? list<Bag>(container["structuredLyrics"]) : [];
    const first = rows[0];
    if (!first) return null;

    /* OpenSubsonic times lyrics in milliseconds (`line.start`, `offset`); every clock this app
       reads — `audio.currentTime`, the engine's `time` event, the scrubber — is in seconds. The
       conversion belongs here, at the boundary, so nothing downstream has to remember which of
       the two numbers it is holding. Left undone, a line at 2 s reads as 2000 s and the highlight
       never leaves the first line of the song. */
    const lines = list<Bag>(first["line"])
      .map((line) => {
        const value = text(line["value"]);
        const start = Number(line["start"]) / 1000;
        return Number.isFinite(start) ? { start, value } : { value };
      })
      .filter((line) => line.value !== "");

    const offset = Number(first["offset"]) / 1000;

    return {
      artist: text(first["artist"]) || undefined,
      title: text(first["title"]) || undefined,
      synced: first["synced"] === true,
      offset: Number.isFinite(offset) && offset !== 0 ? offset : undefined,
      line: lines,
    };
  }

  /* ---- cross-device resume -------------------------------------------------------------- */

  async playQueue(): Promise<PlayQueue | null> {
    const envelope = await this.#call("getPlayQueue", {});
    const container = envelope["playQueue"];
    if (!isBag(container)) return null;
    return {
      current: text(container["current"]) || undefined,
      position: Number(container["position"]) || undefined,
      username: text(container["username"]) || undefined,
      changed: text(container["changed"]) || undefined,
      changedBy: text(container["changedBy"]) || undefined,
      entry: toSongs(container["entry"]),
    };
  }

  async savePlayQueue(songIds: readonly Id[], current?: Id, positionMs?: number): Promise<void> {
    if (!songIds.length) return;
    await this.#call("savePlayQueue", {
      // `savePlayQueue` takes a repeated `id`; the protocol's own answer for a list is a
      // comma-separated `id`, which is what every server reads.
      id: songIds.join(","),
      current: current ?? undefined,
      position: positionMs === undefined ? undefined : Math.max(0, Math.floor(positionMs)),
    });
  }

  /* ---- the one place a request is made -------------------------------------------------- */

  async #call(endpoint: string, params: Record<string, string | number | undefined>): Promise<Bag> {
    const raw = await this.transport.request({ endpoint, params });
    return unwrap(raw, endpoint);
  }

  /** The protocol version and client name this instance reports. Used by diagnostics. */
  get identity(): { apiVersion: string; clientName: string } {
    return { apiVersion: this.#apiVersion, clientName: this.#clientName };
  }

  /** The last `ping`, if any. */
  get server(): ServerInfo | null {
    return this.#info;
  }
}

/** Alias the app uses when it does not care which implementation it has. */
export type { Song };