/* P1's acceptance: opening the app asks for one page of albums and nothing else.
 *
 * These are assertions about the store against a counted fake, not about the markup. The
 * interesting failures in the old bridge were all of the same shape — a boot that walked the
 * whole library before the first screen — so the fake counts every call and each test states
 * exactly which calls the action is allowed to make.
 */

import { describe, expect, it, vi } from "vitest";
import { Library } from "./store.svelte";
import type { Album, Artist, MusicServer, Playlist, SearchResults } from "../subsonic";

function album(id: string): Album {
  return { id, name: `Album ${id}` };
}

function page(ids: string[], offset = 0, maybeMore = false) {
  return { albums: ids.map(album), offset, maybeMore };
}

const EMPTY_RESULTS: SearchResults = { artists: [], albums: [], songs: [] };

/** Every method counted, nothing answered from a real server. */
function fake(overrides: Partial<MusicServer> = {}): MusicServer & Record<string, ReturnType<typeof vi.fn>> {
  return {
    ping: vi.fn(async () => ({ version: "1.16.1", type: "navidrome", openSubsonic: true })),
    artists: vi.fn(async () => [] as Artist[]),
    artist: vi.fn(async (id: string) => ({ id, name: `Artist ${id}` }) as Artist),
    albums: vi.fn(async () => page([])),
    album: vi.fn(async (id: string) => ({ ...album(id), song: [] }) as Album),
    playlists: vi.fn(async () => [] as Playlist[]),
    playlist: vi.fn(async (id: string) => ({ id, name: `List ${id}`, entry: [] }) as Playlist),
    search: vi.fn(async () => EMPTY_RESULTS),
    star: vi.fn(async () => {}),
    unstar: vi.fn(async () => {}),
    starred: vi.fn(async () => EMPTY_RESULTS),
    source: vi.fn(async (id: string) => ({ url: `stream/${id}`, format: "mp3" })),
    cover: vi.fn(async () => null),
    ...overrides,
  } as never;
}

const CALLS = ["ping", "artists", "artist", "albums", "album", "playlists", "playlist", "search", "starred", "cover"];

function assertOnly(server: MusicServer & Record<string, ReturnType<typeof vi.fn>>, allowed: string[]): void {
  const used = CALLS.filter((name) => (server[name]?.mock.calls.length ?? 0) > 0);
  expect(used.sort()).toEqual(allowed.slice().sort());
}

describe("the library loads lazily", () => {
  it("asks for nothing when it is merely attached", () => {
    const server = fake();
    new Library().attach(server);
    assertOnly(server, []);
  });

  it("asks for one page of albums, and only that page", async () => {
    const server = fake({ albums: vi.fn(async (options = {}) => page(["a", "b"], options.offset ?? 0, true)) });
    const library = new Library();
    library.attach(server);

    await library.loadAlbums({ reset: true });

    expect(server.albums).toHaveBeenCalledTimes(1);
    expect(server.albums).toHaveBeenCalledWith({ type: "alphabeticalByName", offset: 0, size: 50 });
    assertOnly(server, ["albums"]);
    expect(library.albums.map((entry) => entry.id)).toEqual(["a", "b"]);
    // A full page means another page *may* exist; it does not mean one is fetched.
    expect(library.albumsDone).toBe(false);
  });

  it("walks pages only when asked, and keeps the server's ids", async () => {
    let offset = 0;
    const server = fake({
      albums: vi.fn(async (options = {}) => {
        offset = options.offset ?? 0;
        return page(offset === 0 ? ["a"] : ["b"], offset, offset === 0);
      }),
    });
    const library = new Library();
    library.attach(server);

    await library.loadAlbums({ reset: true });
    expect(library.albums.map((entry) => entry.id)).toEqual(["a"]);

    await library.loadAlbums();
    expect(server.albums).toHaveBeenCalledTimes(2);
    // The second request continues from where the first page ended, not from a new numbering.
    expect(server.albums).toHaveBeenLastCalledWith({ type: "alphabeticalByName", offset: 1, size: 50 });
    expect(library.albums.map((entry) => entry.id)).toEqual(["a", "b"]);
    expect(library.albumsDone).toBe(true);
  });

  it("treats a repeated page as the end, for servers that ignore offset", async () => {
    const server = fake({ albums: vi.fn(async () => page(["a", "b"], 0, true)) });
    const library = new Library();
    library.attach(server);

    await library.loadAlbums({ reset: true });
    await library.loadAlbums();

    expect(library.albumsDone).toBe(true);
    expect(library.albums.map((entry) => entry.id)).toEqual(["a", "b"]);
    // Once it is done, asking again does not reach the server at all.
    await library.loadAlbums();
    expect(server.albums).toHaveBeenCalledTimes(2);
  });

  it("fetches an album's songs only when the album is opened", async () => {
    const server = fake();
    const library = new Library();
    library.attach(server);
    await library.loadAlbums({ reset: true });

    expect(server.album).not.toHaveBeenCalled();

    await library.openAlbum("a1");
    expect(server.album).toHaveBeenCalledWith("a1");
    expect(library.album?.id).toBe("a1");
  });

  it("fetches an artist's albums and a playlist's entries only when opened", async () => {
    const server = fake();
    const library = new Library();
    library.attach(server);

    await library.loadArtists();
    expect(server.artists).toHaveBeenCalledTimes(1);
    expect(server.artist).not.toHaveBeenCalled();

    await library.openArtist("ar1");
    expect(server.artist).toHaveBeenCalledWith("ar1");

    await library.loadPlaylists();
    expect(server.playlists).toHaveBeenCalledTimes(1);
    expect(server.playlist).not.toHaveBeenCalled();

    await library.openPlaylist("p1");
    expect(server.playlist).toHaveBeenCalledWith("p1");
  });

  it("asks for favourites and search only when they are used", async () => {
    const server = fake();
    const library = new Library();
    library.attach(server);

    expect(server.starred).not.toHaveBeenCalled();
    await library.loadStarred();
    expect(server.starred).toHaveBeenCalledTimes(1);

    expect(server.search).not.toHaveBeenCalled();
    await library.search("  nevermind  ");
    expect(server.search).toHaveBeenCalledWith("nevermind", { artistCount: 20, albumCount: 20, songCount: 50 });
  });

  it("reports a failed page without wedging the list", async () => {
    const server = fake({ albums: vi.fn(async () => Promise.reject(new Error("gateway timeout"))) });
    const library = new Library();
    library.attach(server);

    await library.loadAlbums({ reset: true });

    expect(library.error).toBe("gateway timeout");
    expect(library.albumsDone).toBe(true);
    expect(library.loadingAlbums).toBe(false);
  });
});