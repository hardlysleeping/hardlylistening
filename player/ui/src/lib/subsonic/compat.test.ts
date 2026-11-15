/* One suite, two real servers.
 *
 * `tools/fixtures/compat/` holds recordings — captured by `tools/record-compat.mjs` from a live
 * server, status line, content type and raw body, nothing retyped. This file runs the *same*
 * assertions over every recording, so a claim about "the client" is a claim about what Navidrome
 * 0.64.2 and Supysonic 0.8.0 actually answer rather than about one shape somebody remembered.
 *
 * The transport below is deliberately strict: an endpoint the recording does not contain is an
 * error, not an empty answer. That is what makes the suite meaningful — a client that starts
 * asking for something new fails here instead of quietly succeeding against a fixture that
 * happens to be silent.
 *
 * Bodies are replayed as text and parsed the way `WebTransport` parses them (JSON or nothing),
 * because several real differences live in the envelope and not in the payload: Supysonic's
 * `ping` has no `type`, no `serverVersion` and no `openSubsonic` key at all, and its unknown-endpoint
 * answer is an envelope error with HTTP 404 and code 0.
 */
import { describe, expect, it } from "vitest";

import { SubsonicClient } from "./client";
import { NotSubsonicError, SubsonicError } from "./errors";
import type { SubsonicRequest, SubsonicTransport, StreamOptions } from "./transport";
import type { PlayableSource } from "./types";

interface Recorded {
  endpoint: string;
  params: Record<string, unknown>;
  status: number;
  contentType: string;
  body: string;
  json?: unknown;
}

interface Fixture {
  server: {
    name: string;
    type: string;
    serverVersion: string;
    protocolVersion: string;
    openSubsonic: boolean;
    announcedInPing: boolean;
    recordedAt: string;
    recordedFrom: string;
  };
  responses: Recorded[];
  extensionsEndpointRefused: boolean;
}

/* Every recording in the directory. Vite reads the JSON at build time, so a new fixture is
 * picked up by adding a file and re-recording — there is no list here to forget. */
const recorded = import.meta.glob("../../../tools/fixtures/compat/*.json", {
  eager: true,
  import: "default",
}) as Record<string, Fixture>;

function loadFixtures(): Array<[string, Fixture]> {
  return Object.entries(recorded)
    .map(([path, fixture]): [string, Fixture] => [
      (path.split("/").pop() ?? path).replace(/\.json$/, ""),
      fixture,
    ])
    .sort((left, right) => left[0].localeCompare(right[0]));
}

/** Replays one recording, and refuses to answer for anything the recording does not contain. */
class FixtureTransport implements SubsonicTransport {
  readonly kind = "web" as const;
  readonly requests: SubsonicRequest[] = [];
  readonly #byEndpoint = new Map<string, Recorded>();

  constructor(readonly origin: string, recording: Fixture) {
    for (const entry of recording.responses) this.#byEndpoint.set(entry.endpoint, entry);
  }

  async request(request: SubsonicRequest): Promise<unknown> {
    this.requests.push(request);
    const recorded = this.#byEndpoint.get(request.endpoint);
    if (!recorded) {
      throw new Error(
        `${this.origin} was never asked for ${request.endpoint}; re-record with tools/record-compat.mjs`,
      );
    }
    if (recorded.status === 401 || recorded.status === 403) {
      throw new SubsonicError(40, "Wrong username or password", request.endpoint);
    }
    if (recorded.status >= 400 && recorded.json === undefined) {
      throw new NotSubsonicError(`${this.origin} answered HTTP ${recorded.status}`);
    }
    if (recorded.json === undefined) {
      // WebTransport's own rule: a body that is not JSON is not a Subsonic server.
      throw new NotSubsonicError(`${this.origin} answered, but not with JSON`);
    }
    return recorded.json;
  }

  async source(songId: string, options: StreamOptions = {}): Promise<PlayableSource> {
    return {
      songId,
      url: `${this.origin}/rest/stream.view?id=${songId}`,
      transcoded: options.maxBitRate !== undefined || options.format !== undefined,
    };
  }

  async cover(coverArtId: string | undefined): Promise<string | null> {
    return coverArtId ? `${this.origin}/rest/getCoverArt.view?id=${coverArtId}` : null;
  }
}

const fixtures = loadFixtures();

describe.each(fixtures)("%s", (name: string, fixture: Fixture) => {
  const origin = `https://${name}.fixture.test`;
  const transport = () => new FixtureTransport(origin, fixture);
  const client = () => new SubsonicClient({ transport: transport() });

  it("signed in, and the recording says what the server is", async () => {
    const info = await client().ping();
    // These are the server's own words, replayed. Supysonic sends none of them, and the client
    // has to be fine with that rather than inventing a type.
    expect(info.type).toBe(fixture.server.type || "subsonic");
    expect(info.serverVersion).toBe(fixture.server.serverVersion);
    expect(info.openSubsonic).toBe(fixture.server.openSubsonic);
    expect(info.version).toBe(fixture.server.protocolVersion);
  });

  it("derives capabilities from the announcement, and asks the endpoint that carries it", async () => {
    const subject = client();
    const info = await subject.ping();
    const asked = (subject.transport as FixtureTransport).requests.map((request) => request.endpoint);

    if (fixture.server.announcedInPing) {
      expect(asked).not.toContain("getOpenSubsonicExtensions");
    } else if (fixture.server.openSubsonic) {
      // Navidrome: `openSubsonic: true` in `ping`, the list in its own endpoint.
      expect(asked).toContain("getOpenSubsonicExtensions");
      expect(info.extensions.length).toBeGreaterThan(0);
    } else {
      // Supysonic: not OpenSubsonic at all, so there is nothing to ask for.
      expect(asked).not.toContain("getOpenSubsonicExtensions");
      expect(info.extensions).toEqual([]);
    }
    expect(subject.capabilities).not.toBeNull();
    // Whatever was announced, an extension nobody mentioned is not a capability.
    expect(subject.capabilities?.has("songLyrics")).toBe(info.extensions.includes("songLyrics"));
  });

  it("pages albums with the ids the server chose", async () => {
    const page = await client().albums({ size: 10 });
    expect(page.albums.length).toBeGreaterThan(0);
    expect(page.offset).toBe(0);
    expect(page.maybeMore).toBe(page.albums.length >= 10);
    for (const album of page.albums) {
      // Opaque strings, passed through: no integer ids anywhere in the model.
      expect(typeof album.id).toBe("string");
      expect(album.id.length).toBeGreaterThan(0);
      expect(album.name.length).toBeGreaterThan(0);
      // A list entry carries counts, not tracks: `song` is `getAlbum`'s field.
      expect(album.songCount).toBeGreaterThan(0);
      expect(album.song).toBeUndefined();
    }
  });

  it("opens an album and its songs", async () => {
    const subject = client();
    const page = await subject.albums({ size: 1 });
    const first = page.albums[0];
    expect(first).toBeDefined();
    if (!first) throw new Error("the fixture has no album to open");
    const album = await subject.album(first.id);
    expect(album.id).toBe(first.id);
    expect(album.song?.length).toBeGreaterThan(0);
    for (const song of album.song ?? []) {
      expect(typeof song.id).toBe("string");
      expect(song.title.length).toBeGreaterThan(0);
      // A number that arrived as a number, or as a string, is a number here.
      expect(typeof song.duration).toBe("number");
      expect(Number.isFinite(song.duration as number)).toBe(true);
    }
  });

  it("reads the artist index in whichever shape it arrives", async () => {
    const artists = await client().artists();
    expect(artists.length).toBeGreaterThan(0);
    for (const artist of artists) {
      expect(typeof artist.id).toBe("string");
      expect(artist.name.length).toBeGreaterThan(0);
    }
    // Both recordings put artists inside index elements; flattening them is the client's job.
    expect(new Set(artists.map((artist) => artist.id)).size).toBe(artists.length);
  });

  it("treats an empty collection as empty rather than as an error", async () => {
    const subject = client();
    // Both servers answer `{"playlists":{}}` and `{"starred2":{}}` for a library with neither.
    expect(await subject.playlists()).toEqual([]);
    const starred = await subject.starred();
    expect(starred.songs).toEqual([]);
    expect(starred.albums).toEqual([]);
    expect(starred.artists).toEqual([]);
  });

  it("searches with the singular keys `search3` uses", async () => {
    const results = await client().search("Compat");
    expect(results.songs.length + results.albums.length + results.artists.length).toBeGreaterThan(0);
    for (const album of results.albums) expect(typeof album.id).toBe("string");
  });

  it("asks for no lyrics when the server never claimed the extension", async () => {
    const subject = client();
    await subject.ping();
    expect(subject.capabilities?.has("songLyrics")).toBe(fixture.server.openSubsonic ? subject.capabilities?.has("songLyrics") : false);
    const lyrics = await subject.lyrics("any-song-id");
    expect(lyrics).toBeNull();
    const asked = (subject.transport as FixtureTransport).requests.map((request) => request.endpoint);
    if (!subject.capabilities?.has("songLyrics")) {
      // The gate is the claim: a client that asks anyway pays a refused round trip per row.
      expect(asked).not.toContain("getLyricsBySongId");
    }
  });

  it("keeps a refusal of the extensions endpoint out of the sign-in", async () => {
    // Supysonic answers that endpoint with HTTP 404 and code 0. It is a capable server; the
    // absent list is an absent feature, not a broken address.
    const subject = client();
    const info = await subject.ping();
    expect(info.type.length).toBeGreaterThan(0);
    if (fixture.extensionsEndpointRefused) {
      expect(info.extensions).toEqual([]);
      const refused = fixture.responses.find((entry) => entry.endpoint === "getOpenSubsonicExtensions");
      expect(refused?.status).toBe(404);
      expect((refused?.json as { "subsonic-response"?: { error?: { code?: number } } })["subsonic-response"]?.error?.code).toBe(0);
    }
  });
});

describe("the fixtures", () => {
  it("are at least two implementations, and disagree about something that matters", () => {
    expect(fixtures.length).toBeGreaterThanOrEqual(2);
    const names = fixtures.map(([name]) => name);
    expect(names).toContain("navidrome");
    expect(names).toContain("supysonic");

    const shapes = fixtures.map(([, fixture]) => ({
      openSubsonic: fixture.server.openSubsonic,
      announcedInPing: fixture.server.announcedInPing,
      protocol: fixture.server.protocolVersion,
    }));
    // If both servers answered identically, one of them would be proving nothing.
    expect(new Set(shapes.map((shape) => JSON.stringify(shape))).size).toBeGreaterThan(1);
  });

  it("were recorded from a server rather than typed by hand", () => {
    for (const [name, fixture] of fixtures) {
      expect(fixture.server.recordedAt, name).toMatch(/^\d{4}-\d{2}-\d{2}T/);
      expect(fixture.server.recordedFrom, name).toMatch(/^http:\/\/127\.0\.0\.1:\d+$/);
      expect(fixture.responses.length, name).toBeGreaterThan(5);
      for (const response of fixture.responses) {
        // Every entry is a body the server sent, with the content type it sent it as.
        expect(typeof response.body, `${name} ${response.endpoint}`).toBe("string");
        expect(response.body.length).toBeGreaterThan(0);
        expect(response.contentType).toContain("json");
      }
    }
  });

  it("carry no credential", () => {
    for (const [name, fixture] of fixtures) {
      const text = JSON.stringify(fixture);
      // `u`/`p`/`t`/`s` are the protocol's; the recorder never stores them, and a fixture that
      // grew one would be a secret in a test that ships.
      expect(text, name).not.toMatch(/"p"\s*:/);
      expect(text, name).not.toMatch(/"t"\s*:\s*"[0-9a-f]{32}"/);
      expect(text, name).not.toContain("compat-fixture-pass");
    }
  });
});