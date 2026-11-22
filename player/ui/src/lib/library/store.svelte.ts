/* The library, loaded lazily.
 *
 * The rule this file exists to enforce: **opening the app asks for one page of albums and
 * nothing else.** The old bridge asked for every album, then `getAlbum` for each one, then
 * every playlist and `getPlaylist` for each — a whole library before the first screen. Here a
 * page is a page, an album's tracks arrive when the album is opened, and an artist's albums
 * arrive when the artist is opened.
 *
 * Results are kept by the server's own id. There is no synthetic id, no second numbering, and
 * no snapshot shaped like the old `/api/library`.
 */

import type { Album, AlbumListType, Artist, MusicServer, Playlist, PlaylistChange, SearchResults, Song } from "../subsonic";

const PAGE_SIZE = 50;

export type LibraryView = "albums" | "artists" | "playlists" | "favorites" | "search";

export class Library {
  albums = $state<Album[]>([]);
  artists = $state<Artist[]>([]);
  playlists = $state<Playlist[]>([]);

  /** Set when the server has no more album pages. */
  albumsDone = $state(false);
  loadingAlbums = $state(false);
  loadingArtists = $state(false);
  loadingPlaylists = $state(false);
  loadingDetail = $state(false);
  error = $state<string | null>(null);

  /** The open album / artist / playlist, with its contents. `null` when nothing is open. */
  album = $state<Album | null>(null);
  artist = $state<Artist | null>(null);
  playlist = $state<Playlist | null>(null);

  results = $state<SearchResults>({ artists: [], albums: [], songs: [] });
  /** The term those results answer. The box can be edited after a search without re-running it,
   * so the screen names what it is showing rather than echoing what is being typed. */
  lastQuery = $state("");
  starred = $state<SearchResults>({ artists: [], albums: [], songs: [] });

  #server: MusicServer | null = null;
  #offset = 0;
  #listType: AlbumListType = "alphabeticalByName";
  #lastPageIds = "";

  attach(server: MusicServer): void {
    this.#server = server;
    this.#reset();
  }

  get ready(): boolean {
    return this.#server !== null;
  }

  /** Sign-out. `attach` is the only way in, so this is the only way back out. */
  detach(): void {
    this.#server = null;
    this.#reset();
    this.error = null;
  }

  #reset(): void {
    this.albums = [];
    this.artists = [];
    this.playlists = [];
    this.albumsDone = false;
    this.#offset = 0;
    this.#lastPageIds = "";
    this.album = null;
    this.artist = null;
    this.playlist = null;
    this.lastQuery = "";
    this.results = { artists: [], albums: [], songs: [] };
    this.error = null;
  }

  /* ---- album pages ---------------------------------------------------------------------- */

  async loadAlbums(options: { reset?: boolean; type?: AlbumListType } = {}): Promise<void> {
    if (!this.#server || this.loadingAlbums) return;
    if (options.reset) {
      this.albums = [];
      this.#offset = 0;
      this.albumsDone = false;
      this.#lastPageIds = "";
    }
    if (this.albumsDone) return;
    if (options.type) this.#listType = options.type;

    this.loadingAlbums = true;
    try {
      const page = await this.#server.albums({
        type: this.#listType,
        offset: this.#offset,
        size: PAGE_SIZE,
      });

      // Some servers ignore `offset`; a page of the same ids is the end of the list, not more
      // of it. Without this the wall repeats itself forever.
      const ids = page.albums.map((album) => album.id).join(",");
      if (ids && ids === this.#lastPageIds) {
        this.albumsDone = true;
        return;
      }
      this.#lastPageIds = ids;

      this.albums = [...this.albums, ...page.albums];
      this.#offset = page.offset + page.albums.length;
      this.albumsDone = !page.maybeMore || page.albums.length === 0;
    } catch (error) {
      this.error = error instanceof Error ? error.message : "The album list did not load";
      this.albumsDone = true;
    } finally {
      this.loadingAlbums = false;
    }
  }

  /* ---- artists and playlists ------------------------------------------------------------ */

  async loadArtists(): Promise<void> {
    if (!this.#server || this.loadingArtists || this.artists.length) return;
    this.loadingArtists = true;
    try {
      this.artists = await this.#server.artists();
    } catch (error) {
      this.error = error instanceof Error ? error.message : "The artist list did not load";
    } finally {
      this.loadingArtists = false;
    }
  }

  async loadPlaylists(): Promise<void> {
    if (!this.#server || this.loadingPlaylists || this.playlists.length) return;
    this.loadingPlaylists = true;
    try {
      this.playlists = await this.#server.playlists();
    } catch (error) {
      this.error = error instanceof Error ? error.message : "The playlists did not load";
    } finally {
      this.loadingPlaylists = false;
    }
  }

  /* ---- opening one thing ---------------------------------------------------------------- */

  async openAlbum(id: string): Promise<Album | null> {
    if (!this.#server) return null;
    this.loadingDetail = true;
    this.error = null;
    try {
      const album = await this.#server.album(id);
      this.album = album;
      this.artist = null;
      this.playlist = null;
      return album;
    } catch (error) {
      this.error = error instanceof Error ? error.message : "That album did not open";
      return null;
    } finally {
      this.loadingDetail = false;
    }
  }

  async openArtist(id: string): Promise<Artist | null> {
    if (!this.#server) return null;
    this.loadingDetail = true;
    this.error = null;
    try {
      const artist = await this.#server.artist(id);
      this.artist = artist;
      this.album = null;
      this.playlist = null;
      return artist;
    } catch (error) {
      this.error = error instanceof Error ? error.message : "That artist did not open";
      return null;
    } finally {
      this.loadingDetail = false;
    }
  }

  async openPlaylist(id: string): Promise<Playlist | null> {
    if (!this.#server) return null;
    this.loadingDetail = true;
    this.error = null;
    try {
      const playlist = await this.#server.playlist(id);
      this.playlist = playlist;
      this.album = null;
      this.artist = null;
      return playlist;
    } catch (error) {
      this.error = error instanceof Error ? error.message : "That playlist did not open";
      return null;
    } finally {
      this.loadingDetail = false;
    }
  }

  closeDetail(): void {
    this.album = null;
    this.artist = null;
    this.playlist = null;
  }

  /* ---- writing a playlist --------------------------------------------------------------- */

  /** Make one, then open it: a new playlist with no screen behind the tap reads as a no-op. */
  async createPlaylist(name: string): Promise<Playlist | null> {
    if (!this.#server) return null;
    const trimmed = name.trim();
    if (!trimmed) {
      this.error = "A playlist needs a name";
      return null;
    }
    this.error = null;
    try {
      const playlist = await this.#server.createPlaylist(trimmed);
      if (!this.playlists.some((known) => known.id === playlist.id)) {
        this.playlists = [...this.playlists, playlist];
      }
      await this.openPlaylist(playlist.id);
      return playlist;
    } catch (error) {
      this.error = error instanceof Error ? error.message : "The playlist was not made";
      return null;
    }
  }

  /** Rename the open playlist, add one song to it, or remove one position from it. */
  async editPlaylist(change: PlaylistChange): Promise<boolean> {
    if (!this.#server || !this.playlist) return false;
    const id = this.playlist.id;
    this.error = null;
    try {
      await this.#server.updatePlaylist(id, change);
    } catch (error) {
      this.error = error instanceof Error ? error.message : "The playlist was not changed";
      return false;
    }
    // Both screens re-read from the server after a write. A locally patched membership would
    // disagree with the server the first time it refused something, and the member would be
    // looking at a list they do not have.
    await this.#reloadPlaylist(id);
    return true;
  }

  async #reloadPlaylist(id: string): Promise<void> {
    if (!this.#server) return;
    try {
      this.playlist = await this.#server.playlist(id);
      this.playlists = await this.#server.playlists();
    } catch (error) {
      this.error = error instanceof Error ? error.message : "That playlist did not reload";
    }
  }

  /* ---- search and favourites ------------------------------------------------------------ */

  async search(query: string): Promise<void> {
    if (!this.#server) return;
    this.error = null;
    this.lastQuery = query.trim();
    try {
      this.results = await this.#server.search(query.trim(), {
        artistCount: 20,
        albumCount: 20,
        songCount: 50,
      });
    } catch (error) {
      this.error = error instanceof Error ? error.message : "The search failed";
    }
  }

  async loadStarred(): Promise<void> {
    if (!this.#server) return;
    try {
      this.starred = await this.#server.starred();
    } catch (error) {
      this.error = error instanceof Error ? error.message : "Favourites did not load";
    }
  }

  /** A cover URL for one `coverArt` id, resolved through the transport. */
  async serverCover(coverArt: string | undefined, size = 300): Promise<string | null> {
    if (!this.#server) return null;
    return this.#server.cover(coverArt, size);
  }

  /** The cover URL for an album. */
  async cover(album: Album, size = 300): Promise<string | null> {
    return this.serverCover(album.coverArt, size);
  }
}

/** The songs of whatever is open, as one list — what "play this" means. */
export function songsOf(album: Album | null, artist: Artist | null, playlist: Playlist | null): Song[] {
  if (album?.song) return album.song;
  if (playlist?.entry) return playlist.entry;
  if (artist?.album) return artist.album.flatMap((entry) => entry.song ?? []);
  return [];
}