/* The Subsonic/OpenSubsonic data model, as this client uses it.
 *
 * Two rules run through the whole file:
 *
 * 1. **An id is an opaque string.** Servers hand out `"al-1234"`, `"3f9c…"`, `"1042"` and
 *    everything between. Nothing here parses an id, sorts by one, or makes one up. The old
 *    host-UI bridge had to invent integers because the host page believed ids were numbers;
 *    that belief is not part of a Subsonic client, so the mapping is gone.
 *
 * 2. **A field the server did not send is absent, not falsified.** Almost everything except
 *    `id` is optional, because a Navidrome, a Gonic and a Subsonic 4.2 server disagree about
 *    which fields exist. A caller that needs a value checks for it; nothing is defaulted to
 *    `0` or `""` behind the caller's back.
 */

export type Id = string;

/** What `ping` tells us about the server we are talking to. */
export interface ServerInfo {
  /** The version *this* server implements, e.g. `"1.16.1"`. */
  version: string;
  /** `"subsonic"`, `"navidrome"`, `"gonic"`, `"opensubsonic"`, … */
  type: string;
  /** The product version, e.g. `"0.18.0"`. */
  serverVersion: string;
  /** True when the server announces the OpenSubsonic extension set. */
  openSubsonic: boolean;
  /** Extension ids the server announced (`apiKeyAuthentication`, `songLyrics`, …). */
  extensions: string[];
}

/** A track. `getAlbum`, `getPlaylist`, `search3` and `getAlbumList2` all carry these. */
export interface Song {
  id: Id;
  title: string;
  album?: string;
  albumId?: Id;
  artist?: string;
  artistId?: Id;
  parent?: Id;
  coverArt?: Id;
  duration?: number;
  track?: number;
  discNumber?: number;
  year?: number;
  genre?: string;
  suffix?: string;
  contentType?: string;
  bitRate?: number;
  size?: number;
  path?: string;
  created?: string;
  starred?: string;
  userRating?: number;
  averageRating?: number;
  playCount?: number;
  musicBrainzId?: string;
  /** OpenSubsonic: the canonical sort title, when the server sends one. */
  sortName?: string;
}

/** An album, as a list entry (`getAlbumList2`) or with its tracks (`getAlbum`). */
export interface Album {
  id: Id;
  name: string;
  artist?: string;
  artistId?: Id;
  coverArt?: Id;
  songCount?: number;
  duration?: number;
  playCount?: number;
  created?: string;
  starred?: string;
  year?: number;
  genre?: string;
  /** Present only on `getAlbum`. A list entry does not carry its tracks. */
  song?: Song[];
}

/** An artist entry from `getArtists`. Albums are absent until `getArtist` is called. */
export interface Artist {
  id: Id;
  name: string;
  coverArt?: Id;
  albumCount?: number;
  starred?: string;
  /** Present only on `getArtist`. */
  album?: Album[];
}

/** A playlist entry from `getPlaylists`; `entry` only appears on `getPlaylist`. */
export interface Playlist {
  id: Id;
  name: string;
  comment?: string;
  owner?: string;
  public?: boolean;
  songCount?: number;
  duration?: number;
  created?: string;
  changed?: string;
  coverArt?: Id;
  entry?: Song[];
}

export interface SearchResults {
  artists: Artist[];
  albums: Album[];
  songs: Song[];
}

/** A source an audio engine can play. `url` is resolved by the transport, never by a view. */
export interface PlayableSource {
  songId: Id;
  url: string;
  contentType?: string;
  /** Set when the server transcoded rather than sending the original bytes. */
  transcoded: boolean;
}

/** Ordered list kinds `getAlbumList2` accepts. */
export type AlbumListType =
  | "random"
  | "newest"
  | "highest"
  | "frequent"
  | "recent"
  | "alphabeticalByName"
  | "alphabeticalByArtist"
  | "starred"
  | "byYear"
  | "byGenre";

export interface AlbumListOptions {
  type?: AlbumListType;
  /** `getAlbumList2` offset. The server caps `size`; callers page with this. */
  offset?: number;
  size?: number;
  genre?: string;
  fromYear?: number;
  toYear?: number;
  /** Only meaningful for the `starred` list type on some servers. */
  musicFolderId?: Id;
}

/** One page of albums, plus whether the server has more behind it. */
export interface AlbumPage {
  albums: Album[];
  offset: number;
  /** True when the page came back full, so another request may return more. */
  maybeMore: boolean;
}

export interface SearchOptions {
  artistCount?: number;
  albumCount?: number;
  songCount?: number;
  artistOffset?: number;
  albumOffset?: number;
  songOffset?: number;
  musicFolderId?: Id;
}

export interface Lyrics {
  artist?: string;
  title?: string;
  /** Plain text when the server has it. */
  value?: string;
  /** OpenSubsonic structured lyrics, when the server sends them. */
  synced?: boolean;
  offset?: number;
  line?: Array<{ start?: number; value: string }>;
}

export interface PlayQueue {
  current?: Id;
  position?: number;
  username?: string;
  changed?: string;
  changedBy?: string;
  entry?: Song[];
}