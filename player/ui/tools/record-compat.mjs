#!/usr/bin/env node
/* Record what two real Subsonic servers actually answer, so the client is tested against them.
 *
 * `src/lib/subsonic/compat.test.ts` runs one suite over every fixture in `tools/fixtures/compat/`.
 * A fixture is only evidence if it came from a server, so this script is the only thing that
 * writes one: it walks a fixed list of endpoints, records the status, the content type and the
 * **raw body** (unparsed, because some servers answer XML even when `f=json` was asked), and
 * follows the first album and song it was given so the recordings describe a real library.
 *
 * Usage — one invocation per server:
 *
 *   node tools/record-compat.mjs \
 *     --origin http://127.0.0.1:4533 --user compat --password … \
 *     --name navidrome --out tools/fixtures/compat/navidrome.json
 *
 * Nothing here is mocked: a wrong password produces a fixture full of code-40 errors, which is
 * the point — the recording is what happened, not what should have.
 */
import { mkdir, writeFile } from "node:fs/promises";
import { dirname, resolve } from "node:path";

const API_VERSION = "1.16.1";
const CLIENT_NAME = "hardlylistening-player";

function parseArgs(argv) {
  const out = {};
  for (let i = 0; i < argv.length; i += 1) {
    const key = argv[i];
    if (!key.startsWith("--")) continue;
    out[key.slice(2)] = argv[i + 1];
    i += 1;
  }
  for (const required of ["origin", "user", "password", "name", "out"]) {
    if (!out[required]) {
      console.error(`missing --${required}`);
      process.exit(2);
    }
  }
  return out;
}

const options = parseArgs(process.argv.slice(2));
const origin = options.origin.replace(/\/+$/, "");

/** The URL `WebTransport` would build, minus the salt: the recording keeps no credential at all. */
function url(endpoint, params = {}) {
  const query = new URLSearchParams({
    u: options.user,
    p: options.password,
    v: API_VERSION,
    c: CLIENT_NAME,
    f: "json",
  });
  for (const [key, value] of Object.entries(params)) {
    if (value !== undefined && value !== null && value !== "") query.set(key, String(value));
  }
  return `${origin}/rest/${endpoint}.view?${query.toString()}`;
}

const recordings = [];

/** One request, recorded verbatim. The credential is in the URL, never in the record. */
async function record(endpoint, params = {}) {
  const response = await fetch(url(endpoint, params), {
    headers: { Accept: "application/json" },
    redirect: "follow",
  });
  const body = await response.text();
  let parsed;
  try {
    parsed = JSON.parse(body);
  } catch {
    parsed = undefined;
  }
  const entry = {
    endpoint,
    params,
    status: response.status,
    contentType: response.headers.get("content-type") ?? "",
    body,
    ...(parsed === undefined ? {} : { json: parsed }),
  };
  recordings.push(entry);
  return entry;
}

function envelopeOf(parsed) {
  return parsed && typeof parsed === "object" ? parsed["subsonic-response"] : undefined;
}

function firstAlbumId(parsed) {
  const envelope = envelopeOf(parsed);
  const container = envelope && envelope["albumList2"];
  const albums = container && container["album"];
  const first = Array.isArray(albums) ? albums[0] : albums;
  return first && first["id"];
}

function firstSongId(parsed) {
  const envelope = envelopeOf(parsed);
  const album = envelope && envelope["album"];
  const songs = album && album["song"];
  const first = Array.isArray(songs) ? songs[0] : songs;
  return first && first["id"];
}

async function main() {
  const ping = await record("ping");
  const extensions = await record("getOpenSubsonicExtensions");
  const albums = await record("getAlbumList2", { type: "alphabeticalByName", offset: 0, size: 10 });
  await record("getArtists");
  await record("getPlaylists");
  await record("getStarred2");
  await record("search3", { query: "Compat", songCount: 10, albumCount: 10, artistCount: 10 });

  const albumId = firstAlbumId(albums.json);
  if (albumId) {
    const album = await record("getAlbum", { id: albumId });
    const songId = firstSongId(album.json);
    if (songId) await record("getLyricsBySongId", { id: songId });
  } else {
    console.error("no album in getAlbumList2: the library is empty, and the recording says so");
  }

  const envelope = envelopeOf(ping.json) ?? {};
  const path = resolve(options.out);
  await mkdir(dirname(path), { recursive: true });
  await writeFile(
    path,
    JSON.stringify(
      {
        server: {
          name: options.name,
          type: envelope["type"] ?? "",
          serverVersion: envelope["serverVersion"] ?? "",
          protocolVersion: envelope["version"] ?? "",
          openSubsonic: envelope["openSubsonic"] === true,
          announcedInPing: Array.isArray(envelope["openSubsonicExtensions"]),
          recordedAt: new Date().toISOString(),
          // The origin is a local fixture server, not a person's host: it is the port the
          // recorder was pointed at, recorded so the fixture is traceable.
          recordedFrom: origin,
        },
        responses: recordings.map(({ endpoint, params, status, contentType, body, json }) => ({
          endpoint,
          params,
          status,
          contentType,
          // Bodies are kept raw; `json` is a convenience the test never trusts over `body`.
          body,
          ...(json === undefined ? {} : { json }),
        })),
        // Whether the server has the endpoint at all: a 404 or an envelope error is a refusal,
        // which is a fact about the server rather than a failure of the recording.
        extensionsEndpointRefused:
          extensions.status !== 200 ||
          envelopeOf(extensions.json)?.error !== undefined ||
          extensions.json === undefined,
      },
      null,
      2,
    ) + "\n",
    "utf8",
  );
  console.log(`${options.name}: ${recordings.length} endpoints recorded → ${path}`);
}

await main();