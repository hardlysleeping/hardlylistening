/* What this particular server can do — decided by what it said and what it answered, never by
 * what it is called.
 *
 * The old bridge grew a list of server names with per-name exceptions. That is the thing this
 * file exists to replace: a Navidrome, a Gonic, an Airsonic and a stock Subsonic all answer the
 * same envelope, and the differences that matter are observable — an announced OpenSubsonic
 * extension, a list type that comes back as an error, a page size the server clamps. A client
 * that asks "does this server announce `songLyrics`?" behaves correctly on a server nobody has
 * heard of; a client that asks "is this Navidrome?" does not.
 */

import type { ServerInfo } from "./types";

/** Extension ids a server can announce through `openSubsonicExtensions`. */
export const EXTENSIONS = {
  songLyrics: "songLyrics",
  formPost: "formPost",
  playlists: "playlists",
  apiKeyAuthentication: "apiKeyAuthentication",
  tokenInfo: "tokenInfo",
  indexing: "indexBasedQueue",
  transcode: "transcodeOffset",
} as const;

/** How many albums one `getAlbumList2` page asks for. Servers cap this; it is a request, not a promise. */
export const DEFAULT_PAGE_SIZE = 100;
export const MAX_PAGE_SIZE = 500;

export interface Capabilities {
  openSubsonic: boolean;
  extensions: ReadonlySet<string>;
  /** `getAlbumList2` answered at least once. Cleared when the server refuses it. */
  albumListPaging: boolean;
  /** Flipped when the server refuses `getArtists`; the library then goes through search. */
  artistIndex: boolean;
  /** The largest album page this client will ask for. */
  pageSize: number;
  has(extension: string): boolean;
}

class ServerCapabilities implements Capabilities {
  readonly openSubsonic: boolean;
  readonly extensions: ReadonlySet<string>;
  albumListPaging = true;
  artistIndex = true;
  pageSize = DEFAULT_PAGE_SIZE;

  constructor(info: ServerInfo) {
    this.openSubsonic = info.openSubsonic;
    this.extensions = new Set(info.extensions);
  }

  has(extension: string): boolean {
    return this.extensions.has(extension);
  }
}

export function capabilitiesFrom(info: ServerInfo): Capabilities {
  return new ServerCapabilities(info);
}

/**
 * The album page to ask for. `offset` and `size` are the server's own paging parameters, so a
 * caller that follows `AlbumPage.maybeMore` walks the library one page at a time instead of
 * asking for all of it.
 */
export function pageRequest(capabilities: Capabilities, offset: number, size?: number) {
  const wanted = size ?? capabilities.pageSize;
  const capped = Math.max(1, Math.min(wanted, MAX_PAGE_SIZE));
  return { offset: Math.max(0, Math.floor(offset)), size: capped };
}

/**
 * Codes that mean "this server does not implement that endpoint" rather than "that request was
 * wrong". Subsonic has no dedicated code for it: servers answer 0 with a message, 10 for a
 * missing parameter, or 70 when the endpoint itself is unknown to them.
 */
export function looksLikeUnsupported(capabilities: Capabilities, code: number): boolean {
  void capabilities;
  return code === 0 || code === 10 || code === 70;
}