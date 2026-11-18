import { describe, expect, it } from "vitest";
import { SubsonicClient } from "./client";
import { SubsonicError, NotSubsonicError } from "./errors";
import type { SubsonicRequest, SubsonicTransport, StreamOptions } from "./transport";
import type { PlayableSource } from "./types";

/* A transport that answers from a table and records what it was asked. No sockets, no clock. */
class FakeTransport implements SubsonicTransport {
  readonly kind = "web" as const;
  readonly origin = "https://music.example.com";
  readonly calls: SubsonicRequest[] = [];

  constructor(private readonly answers: Record<string, unknown> = {}) {}

  async request(request: SubsonicRequest): Promise<unknown> {
    this.calls.push(request);
    const answer = this.answers[request.endpoint];
    if (answer instanceof Error) throw answer;
    if (answer !== undefined) return answer;
    return { "subsonic-response": { status: "ok", version: "1.16.1" } };
  }

  async source(songId: string, _options?: StreamOptions): Promise<PlayableSource> {
    return { songId, url: `https://music.example.com/stream/${songId}`, transcoded: false };
  }

  async cover(coverArtId: string | undefined): Promise<string | null> {
    return coverArtId ? `https://music.example.com/art/${coverArtId}` : null;
  }
}

const envelope = (payload: Record<string, unknown>, extra: Record<string, unknown> = {}) => ({
  "subsonic-response": { status: "ok", version: "1.16.1", ...extra, ...payload },
});

describe("ping and capabilities", () => {
  it("reads the server's own announcement", async () => {
    const transport = new FakeTransport({
      ping: envelope({
        type: "navidrome",
        serverVersion: "0.53.3",
        openSubsonic: true,
        openSubsonicExtensions: [{ name: "songLyrics", versions: [1] }, { name: "formPost", versions: [1] }],
      }),
    });
    const client = new SubsonicClient({ transport });
    const info = await client.ping();

    expect(info.type).toBe("navidrome");
    expect(info.serverVersion).toBe("0.53.3");
    expect(info.openSubsonic).toBe(true);
    expect(info.extensions).toEqual(["songLyrics", "formPost"]);
    expect(client.capabilities?.has("songLyrics")).toBe(true);
    expect(client.capabilities?.has("transcodeOffset")).toBe(false);
  });

  it("is decided by what the server says, never by its name", async () => {
    // A server calling itself "subsonic" can still announce the OpenSubsonic extensions, and a
    // server calling itself anything at all is not special-cased anywhere in the client.
    const transport = new FakeTransport({
      ping: envelope({ type: "subsonic", openSubsonicExtensions: [{ name: "songLyrics" }] }),
    });
    const client = new SubsonicClient({ transport });
    await client.ping();
    expect(client.capabilities?.has("songLyrics")).toBe(true);
    expect(client.capabilities?.openSubsonic).toBe(false);
  });
});

describe("refusals", () => {
  it("turns a Subsonic error code into a typed error with its code", async () => {
    const transport = new FakeTransport({
      ping: envelope({ error: { code: 40, message: "Wrong username or password" } }, { status: "failed" }),
    });
    const client = new SubsonicClient({ transport });

    const failure = await client.ping().catch((error: unknown) => error);
    expect(failure).toBeInstanceOf(SubsonicError);
    expect((failure as SubsonicError).code).toBe(40);
    expect((failure as SubsonicError).isAuthFailure).toBe(true);
    expect((failure as SubsonicError).isProtocolFailure).toBe(false);
  });

  it("keeps the server's own words when they add something", async () => {
    const transport = new FakeTransport({
      ping: envelope({ error: { code: 30, message: "Version 1.16.1 not supported" } }, { status: "failed" }),
    });
    const client = new SubsonicClient({ transport });
    const failure = await client.ping().catch((error: unknown) => error);

    expect((failure as SubsonicError).code).toBe(30);
    expect((failure as SubsonicError).isProtocolFailure).toBe(true);
    expect((failure as SubsonicError).message).toContain("Version 1.16.1 not supported");
  });

  it("refuses a JSON document that is not a Subsonic envelope", async () => {
    const transport = new FakeTransport({ ping: { hello: "world" } });
    const client = new SubsonicClient({ transport });
    await expect(client.ping()).rejects.toBeInstanceOf(NotSubsonicError);
  });
});

describe("lazy loading", () => {
  it("asks for one page, not for every album's tracks", async () => {
    const transport = new FakeTransport({
      ping: envelope({}),
      getAlbumList2: envelope({
        albumList2: {
          album: [
            { id: "al-1", name: "One", songCount: 9 },
            { id: "al-2", name: "Two", songCount: 4 },
          ],
        },
      }),
    });
    const client = new SubsonicClient({ transport });
    const page = await client.albums({ offset: 0, size: 2 });

    expect(page.albums.map((album) => album.id)).toEqual(["al-1", "al-2"]);
    expect(page.maybeMore).toBe(true);

    // Exactly two requests: the ping that discovers capabilities, and the page itself. A
    // `getAlbum` per album is the old bridge's behaviour and must not come back.
    expect(transport.calls.map((call) => call.endpoint)).toEqual(["ping", "getAlbumList2"]);
  });

  it("stops asking when a page comes back short", async () => {
    const transport = new FakeTransport({
      ping: envelope({}),
      getAlbumList2: envelope({ albumList2: { album: [{ id: "al-1", name: "One" }] } }),
    });
    const client = new SubsonicClient({ transport });
    const page = await client.albums({ size: 50 });
    expect(page.maybeMore).toBe(false);
  });

  it("treats an empty album list as an empty list, not a failure", async () => {
    const transport = new FakeTransport({ ping: envelope({}), getAlbumList2: envelope({}) });
    const client = new SubsonicClient({ transport });
    const page = await client.albums();
    expect(page.albums).toEqual([]);
    expect(page.maybeMore).toBe(false);
  });

  it("loads one album's tracks only when that album is asked for", async () => {
    const transport = new FakeTransport({
      ping: envelope({}),
      getAlbum: envelope({ album: { id: "al-1", name: "One", song: [{ id: "s-1", title: "Track" }] } }),
    });
    const client = new SubsonicClient({ transport });
    const album = await client.album("al-1");
    expect(album.song).toEqual([expect.objectContaining({ id: "s-1", title: "Track" })]);
    expect(transport.calls.at(-1)).toEqual({ endpoint: "getAlbum", params: { id: "al-1" } });
  });
});

describe("the servers' disagreements", () => {
  it("reads a single object where a list was expected", async () => {
    const transport = new FakeTransport({
      ping: envelope({}),
      getAlbumList2: envelope({ albumList2: { album: { id: "al-9", name: "Only one" } } }),
    });
    const client = new SubsonicClient({ transport });
    const page = await client.albums();
    expect(page.albums).toHaveLength(1);
    expect(page.albums[0]?.name).toBe("Only one");
  });

  it("flattens artist indexes, and takes artists that arrive without one", async () => {
    const transport = new FakeTransport({
      ping: envelope({}),
      getArtists: envelope({
        artists: {
          index: [
            { name: "A", artist: [{ id: "ar-1", name: "Alpha", albumCount: 2 }] },
            { name: "B", artist: { id: "ar-2", name: "Beta" } },
          ],
        },
      }),
    });
    const client = new SubsonicClient({ transport });
    const artists = await client.artists();
    expect(artists.map((artist) => artist.id)).toEqual(["ar-1", "ar-2"]);
  });

  it("reads search3's singular keys", async () => {
    const transport = new FakeTransport({
      ping: envelope({}),
      search3: envelope({
        searchResult3: {
          artist: { id: "ar-1", name: "Alpha" },
          album: [{ id: "al-1", name: "One" }],
          song: [{ id: "s-1", title: "Track" }],
        },
      }),
    });
    const client = new SubsonicClient({ transport });
    const results = await client.search("a");
    expect(results.artists).toHaveLength(1);
    expect(results.albums).toHaveLength(1);
    expect(results.songs).toHaveLength(1);
  });

  it("keeps a string id exactly as the server stated it", async () => {
    const transport = new FakeTransport({
      ping: envelope({}),
      getAlbum: envelope({ album: { id: "3f9c-88-b0", name: "Hex", song: [{ id: "00ff", title: "T" }] } }),
    });
    const client = new SubsonicClient({ transport });
    const album = await client.album("3f9c-88-b0");
    expect(album.id).toBe("3f9c-88-b0");
    expect(album.song?.[0]?.id).toBe("00ff");
  });

  it("keeps a numeric id as the string the server sent", async () => {
    const transport = new FakeTransport({
      ping: envelope({}),
      getAlbum: envelope({ album: { id: 42, name: "Numbered", song: [{ id: 7, title: "T" }] } }),
    });
    const client = new SubsonicClient({ transport });
    const album = await client.album("42");
    expect(album.id).toBe("42");
    expect(album.song?.[0]?.id).toBe("7");
  });

  it("drops a duration that is not a number instead of becoming NaN", async () => {
    const transport = new FakeTransport({
      ping: envelope({}),
      getAlbum: envelope({
        album: {
          id: "al-1",
          name: "One",
          song: [
            { id: "s-1", title: "Good", duration: "251" },
            { id: "s-2", title: "Bad", duration: "soon" },
          ],
        },
      }),
    });
    const client = new SubsonicClient({ transport });
    const album = await client.album("al-1");
    expect(album.song?.[0]?.duration).toBe(251);
    expect(album.song?.[1]?.duration).toBeUndefined();
  });
});

describe("favourites", () => {
  it("stars and unstars by the server's id", async () => {
    const transport = new FakeTransport({ ping: envelope({}) });
    const client = new SubsonicClient({ transport });
    await client.star("s-1");
    await client.unstar("s-1");
    expect(transport.calls.slice(-2)).toEqual([
      { endpoint: "star", params: { id: "s-1" } },
      { endpoint: "unstar", params: { id: "s-1" } },
    ]);
  });
});
describe("lyrics", () => {
  it("asks only when the server says it has them", async () => {
    // A server that does not announce `songLyrics` is not asked and does not 404 for us.
    const transport = new FakeTransport({ ping: envelope({}) });
    const client = new SubsonicClient({ transport });
    expect(await client.lyrics("s-1")).toBeNull();
    expect(transport.calls.map((call) => call.endpoint)).not.toContain("getLyricsBySongId");
  });

  it("reads line timing, keeps the server's order, and drops lines with no words", async () => {
    const transport = new FakeTransport({
      ping: envelope({ openSubsonicExtensions: [{ name: "songLyrics", versions: [1] }] }),
      getLyricsBySongId: envelope({
        lyricsList: {
          structuredLyrics: [
            {
              artist: "Artist",
              title: "Song",
              synced: true,
              offset: -100,
              line: [
                // Milliseconds, as the spec says: 4500 ms is the line four and a half seconds in.
                { start: 0, value: "first" },
                { start: 4500, value: "second" },
                { start: 9000, value: "" },
                { value: "untimed" },
              ],
            },
          ],
        },
      }),
    });
    const client = new SubsonicClient({ transport });

    const lyrics = await client.lyrics("s-1");

    expect(transport.calls.at(-1)).toEqual({ endpoint: "getLyricsBySongId", params: { id: "s-1" } });
    expect(lyrics?.artist).toBe("Artist");
    expect(lyrics?.synced).toBe(true);
    // The empty line is not a line; the untimed one is kept without a time. Times are seconds
    // because every clock in this app is; the server's milliseconds stop at this boundary.
    expect(lyrics?.line).toEqual([
      { start: 0, value: "first" },
      { start: 4.5, value: "second" },
      { value: "untimed" },
    ]);
    expect(lyrics?.offset).toBe(-0.1);
  });

  it("answers null rather than an empty overlay when the server sends none", async () => {
    const transport = new FakeTransport({
      ping: envelope({ openSubsonicExtensions: [{ name: "songLyrics" }] }),
      getLyricsBySongId: envelope({ lyricsList: { structuredLyrics: [] } }),
    });
    const client = new SubsonicClient({ transport });
    expect(await client.lyrics("s-1")).toBeNull();
  });
});
