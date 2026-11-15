/* Turning what a server actually sent into the model in `types.ts`.
 *
 * Two disagreements between real servers are handled here rather than in every caller:
 *
 * - **One object or a list.** `getArtists` nests artists inside `index` elements, `search3`
 *   uses singular keys (`artist`, not `artists`), and an empty result is sometimes `[]`,
 *   sometimes an object, sometimes missing. `list()` makes all three the same thing.
 * - **A number that arrived as a string.** Some servers send `"duration": "251"` and some send
 *   `251`. Both become a number, and a value that is neither is dropped rather than becoming
 *   `NaN`, because `NaN` propagates silently into arithmetic and into the UI.
 */

import type { Album, Artist, Id, Playlist, ServerInfo, Song } from "./types";

type Bag = Record<string, unknown>;

export function isBag(value: unknown): value is Bag {
  return typeof value === "object" && value !== null && !Array.isArray(value);
}

/** A single object, a list, or nothing — always a list. */
export function list<T>(value: unknown): T[] {
  if (Array.isArray(value)) return value as T[];
  if (isBag(value) || typeof value === "object") {
    if (value === null) return [];
    const single = value as unknown;
    // A single object arrives when the server has exactly one; anything with no keys is empty.
    if (isBag(single)) return Object.keys(single).length ? [single as T] : [];
  }
  return [];
}

export function text(value: unknown, fallback = ""): string {
  if (typeof value === "string") return value;
  if (typeof value === "number" && Number.isFinite(value)) return String(value);
  return fallback;
}

export function num(value: unknown): number | undefined {
  if (typeof value === "number") return Number.isFinite(value) ? value : undefined;
  if (typeof value === "string" && value.trim() !== "") {
    const parsed = Number(value);
    return Number.isFinite(parsed) ? parsed : undefined;
  }
  return undefined;
}

export function bool(value: unknown): boolean {
  return value === true || value === "true";
}

/** Keep a field out of the object entirely when the server did not send it. */
function optional<V>(value: V | undefined): V | undefined {
  return value === undefined || value === "" ? undefined : value;
}

export function toSong(raw: unknown): Song | null {
  if (!isBag(raw)) return null;
  const id = text(raw["id"]);
  if (!id) return null;
  return {
    id,
    title: text(raw["title"], text(raw["name"], "Untitled")),
    album: optional(text(raw["album"])),
    albumId: optional(text(raw["albumId"])),
    artist: optional(text(raw["artist"])),
    artistId: optional(text(raw["artistId"])),
    parent: optional(text(raw["parent"])),
    coverArt: optional(text(raw["coverArt"])),
    duration: num(raw["duration"]),
    track: num(raw["track"]),
    discNumber: num(raw["discNumber"]),
    year: num(raw["year"]),
    genre: optional(text(raw["genre"])),
    suffix: optional(text(raw["suffix"])),
    contentType: optional(text(raw["contentType"])),
    bitRate: num(raw["bitRate"]),
    size: num(raw["size"]),
    path: optional(text(raw["path"])),
    created: optional(text(raw["created"])),
    starred: optional(text(raw["starred"])),
    userRating: num(raw["userRating"]),
    averageRating: num(raw["averageRating"]),
    playCount: num(raw["playCount"]),
    musicBrainzId: optional(text(raw["musicBrainzId"])),
    sortName: optional(text(raw["sortName"])),
  };
}

export function toAlbum(raw: unknown): Album | null {
  if (!isBag(raw)) return null;
  const id = text(raw["id"]);
  if (!id) return null;
  return {
    id,
    name: text(raw["name"], text(raw["title"], "Untitled album")),
    artist: optional(text(raw["artist"])),
    artistId: optional(text(raw["artistId"])),
    coverArt: optional(text(raw["coverArt"])),
    songCount: num(raw["songCount"]),
    duration: num(raw["duration"]),
    playCount: num(raw["playCount"]),
    created: optional(text(raw["created"])),
    starred: optional(text(raw["starred"])),
    year: num(raw["year"]),
    genre: optional(text(raw["genre"])),
    ...(raw["song"] === undefined ? {} : { song: toSongs(raw["song"]) }),
  };
}

export function toArtist(raw: unknown): Artist | null {
  if (!isBag(raw)) return null;
  const id = text(raw["id"]);
  if (!id) return null;
  return {
    id,
    name: text(raw["name"], "Unknown artist"),
    coverArt: optional(text(raw["coverArt"])),
    albumCount: num(raw["albumCount"]),
    starred: optional(text(raw["starred"])),
    ...(raw["album"] === undefined ? {} : { album: toAlbums(raw["album"]) }),
  };
}

export function toPlaylist(raw: unknown): Playlist | null {
  if (!isBag(raw)) return null;
  const id = text(raw["id"]);
  if (!id) return null;
  return {
    id,
    name: text(raw["name"], "Untitled playlist"),
    comment: optional(text(raw["comment"])),
    owner: optional(text(raw["owner"])),
    public: raw["public"] === undefined ? undefined : bool(raw["public"]),
    songCount: num(raw["songCount"]),
    duration: num(raw["duration"]),
    created: optional(text(raw["created"])),
    changed: optional(text(raw["changed"])),
    coverArt: optional(text(raw["coverArt"])),
    ...(raw["entry"] === undefined ? {} : { entry: toSongs(raw["entry"]) }),
  };
}

export function toSongs(value: unknown): Song[] {
  return list<unknown>(value)
    .map(toSong)
    .filter((song): song is Song => song !== null);
}

export function toAlbums(value: unknown): Album[] {
  return list<unknown>(value)
    .map(toAlbum)
    .filter((album): album is Album => album !== null);
}

export function toArtists(value: unknown): Artist[] {
  return list<unknown>(value)
    .map(toArtist)
    .filter((artist): artist is Artist => artist !== null);
}

export function toPlaylists(value: unknown): Playlist[] {
  return list<unknown>(value)
    .map(toPlaylist)
    .filter((playlist): playlist is Playlist => playlist !== null);
}

/**
 * `getArtists` groups into `index` elements. Some servers put artists straight into the
 * response instead. Both become one flat list, in the order the server chose.
 */
export function flattenArtistIndexes(value: unknown): Artist[] {
  const indexes = list<Bag>(value);
  if (!indexes.length) return toArtists(value);

  const artists: Artist[] = [];
  for (const index of indexes) {
    if (!isBag(index)) continue;
    // An artist can appear directly in the list (no index wrapper at all).
    if (typeof index["id"] === "string" && Array.isArray(index["album"]) === false) {
      const direct = toArtist(index);
      if (direct) artists.push(direct);
      continue;
    }
    artists.push(...toArtists(index["artist"]));
  }
  return artists;
}

export function toServerInfo(envelope: Bag): ServerInfo {
  return {
    version: text(envelope["version"]),
    type: text(envelope["type"], "subsonic"),
    serverVersion: text(envelope["serverVersion"]),
    openSubsonic: envelope["openSubsonic"] === true,
    extensions: extensionsFrom(envelope["openSubsonicExtensions"]),
  };
}

/**
 * Extension ids from an `openSubsonicExtensions` value.
 *
 * `openSubsonicExtensions` is a list of `{name, versions}` objects; a server that sends bare
 * strings is read too rather than losing its extensions. The list arrives either inside `ping`
 * or from `getOpenSubsonicExtensions`, which is why it is a function rather than two copies —
 * Navidrome 0.64 announces `openSubsonic: true` in `ping` and puts the list only in the second.
 */
export function extensionsFrom(value: unknown): string[] {
  return list<unknown>(value)
    .map((entry) => {
      if (typeof entry === "string") return entry;
      if (isBag(entry)) return text(entry["name"]);
      return "";
    })
    .filter((id): id is string => id !== "");
}

/** How a person reads a list of ids in a message. Only used for diagnostics. */
export function idList(ids: readonly Id[]): string {
  return ids.join(", ");
}