/* Social is offered only when the server says it has it, and the probe has to be judged on the
 * states a server can actually be in. These tests are the four states, every degradation that
 * lands in `absent`, and the three refusals the read makes before the wire — with no socket, no
 * credential, and no assumption that the app's source knows what the server can do.
 */

import { describe, expect, it } from "vitest";

import type { FetchLike } from "../subsonic";
import {
  ACTIVITY_PATH,
  CAPABILITIES_PATH,
  SocialDenied,
  SocialError,
  SocialUnavailable,
  probeSocial,
  readActivity,
} from "./social";

const DOCUMENT = {
  server: { name: "hardlylistening", version: "1.0", api: "v1", requires_members: false },
  features: {
    social: { comments: true, votes: true, likes: true, activity: true },
  },
};

const FEED = {
  api: "v1",
  data: [
    {
      kind: "boost",
      member_id: "m1",
      member: "Alex",
      member_icon: "🎧",
      track: { id: 7, title: "Boosted", artist_name: "Someone" },
      boosted_at: 1794713333,
      expires_at: 1794799733,
    },
    {
      kind: "day",
      member_id: "m2",
      member: "Sam",
      member_icon: "",
      day: "2026-11-14",
      ts: 1794713000,
      first_ts: 1794700000,
      counts: { listen: 2, add: 1 },
      groups: {
        listen: [{ ts: 1794713000, id: 3, title: "A Song", artist_name: "Someone" }],
        playlist: [{ ts: 1794712000, kind: "created", id: 4, title: "A Playlist" }],
        other: [{ ts: 1794711000, kind: "joined", detail: "joined" }],
      },
    },
  ],
};

function json(body: unknown, status = 200): Response {
  return new Response(JSON.stringify(body), {
    status,
    headers: { "Content-Type": "application/json" },
  });
}

/** One stub for every request, recording the URL and the init it was given. */
class Stub {
  readonly calls: Array<{ url: string; init?: RequestInit }> = [];

  constructor(private readonly answer: (url: string) => Response | Error) {}

  readonly fetch: FetchLike = async (url, init) => {
    this.calls.push({ url, init });
    const result = this.answer(url);
    if (result instanceof Error) throw result;
    return result;
  };
}

function probeWith(answer: (url: string) => Response | Error) {
  const stub = new Stub(answer);
  return { stub, result: probeSocial("https://music.example.com", { fetch: stub.fetch }) };
}

describe("the probe", () => {
  it("offers the feed when the document names social.activity and needs no members", async () => {
    const { stub, result } = probeWith(() => json(DOCUMENT));
    const availability = await result;

    expect(availability.state).toBe("activity");
    expect(availability.reason).toContain("social.activity");
    expect(availability.server).toEqual({ name: "hardlylistening", version: "1.0" });
    expect(availability.requiresMembers).toBe(false);
    expect(stub.calls.map((call) => call.url)).toEqual([
      "https://music.example.com" + CAPABILITIES_PATH,
    ]);
    // The document is public: the player sends no cookie and no credential of any kind.
    expect(stub.calls[0]?.init?.credentials).toBe("omit");
    expect(new Headers(stub.calls[0]?.init?.headers).get("accept")).toBe("application/json");
  });

  it("says members when the server offers the feed but requires a member session", async () => {
    const { result } = probeWith(() =>
      json({ ...DOCUMENT, server: { ...DOCUMENT.server, requires_members: true } }),
    );
    const availability = await result;

    expect(availability.state).toBe("members");
    expect(availability.requiresMembers).toBe(true);
    // This is a fact about this player, not about the server: the feed exists, the client cannot
    // read it, and the reason has to say which.
    expect(availability.reason).toContain("no member session");
  });

  it("does not read a domain that names no activity flag", async () => {
    const { result } = probeWith(() =>
      json({
        ...DOCUMENT,
        features: { social: { comments: true, votes: true, likes: true } },
      }),
    );
    const availability = await result;

    expect(availability.state).toBe("no-activity");
    expect(availability.reason).toContain("not social.activity");
  });

  it("treats a document with no social domain as absent, not as empty", async () => {
    const { result } = probeWith(() => json({ server: DOCUMENT.server, features: {} }));
    const availability = await result;

    expect(availability.state).toBe("absent");
    expect(availability.reason).toContain("no social domain");
  });

  it("lands every degradation in absent, with the address and the status", async () => {
    const cases: Array<[string, () => Response | Error, RegExp]> = [
      ["a refused connection", () => new Error("connect ECONNREFUSED"), /did not answer/],
      ["a server error", () => json({}, 500), /HTTP 500/],
      ["html", () => new Response("<html>hello</html>", { status: 200 }), /not with JSON/],
      ["an empty document", () => json({}), /empty document/],
      ["an array", () => json([1, 2, 3]), /empty document/],
      ["a plain Subsonic 404", () => new Response("", { status: 404 }), /HTTP 404/],
    ];

    for (const [label, answer, expected] of cases) {
      const { result } = probeWith(answer);
      const availability = await result;
      expect(availability.state, label).toBe("absent");
      expect(availability.reason, label).toMatch(expected);
      expect(availability.reason, label).toContain("https://music.example.com");
    }
  });

  it("never throws, including when there is no address at all", async () => {
    const noAddress = await probeSocial("");
    expect(noAddress.state).toBe("absent");

    const refused = await probeSocial("https://music.example.com", {
      fetch: async () => {
        throw new Error("offline");
      },
    });
    expect(refused.state).toBe("absent");
  });
});

describe("reading the feed", () => {
  const available = (): Parameters<typeof readActivity>[1] => ({
    state: "activity",
    reason: "offers social.activity",
    origin: "https://music.example.com",
    server: { name: "hardlylistening", version: "1.0" },
    requiresMembers: false,
  });

  it("asks the versioned endpoint for the app's own page size", async () => {
    const stub = new Stub(() => json(FEED));
    await readActivity("https://music.example.com", available(), { fetch: stub.fetch });

    expect(stub.calls.map((call) => call.url)).toEqual([
      "https://music.example.com" + ACTIVITY_PATH + "?limit=80",
    ]);
    expect(stub.calls[0]?.init?.credentials).toBe("omit");
  });

  it("passes a limit through unchanged", async () => {
    const stub = new Stub(() => json(FEED));
    await readActivity("https://music.example.com", available(), { fetch: stub.fetch, limit: 200 });
    expect(stub.calls[0]?.url).toContain("limit=200");
  });

  it("types the two kinds the feed sends", async () => {
    const items = await readActivity("https://music.example.com", available(), {
      fetch: new Stub(() => json(FEED)).fetch,
    });

    expect(items).toHaveLength(2);

    const boost = items[0];
    expect(boost?.kind).toBe("boost");
    if (boost?.kind !== "boost") throw new Error("the first row is a boost");
    expect(boost.member).toEqual({ id: "m1", name: "Alex", icon: "🎧" });
    expect(boost.track).toEqual({ id: 7, title: "Boosted", artist: "Someone" });
    expect(boost.ts).toBe(1794713333);
    expect(boost.expiresAt).toBe(1794799733);

    const day = items[1];
    expect(day?.kind).toBe("day");
    if (day?.kind !== "day") throw new Error("the second row is a day card");
    expect(day.day).toBe("2026-11-14");
    expect(day.member).toEqual({ id: "m2", name: "Sam", icon: "" });
    expect(day.counts).toEqual({ listen: 2, add: 1 });
    expect(day.groups["listen"]?.[0]?.track).toEqual({ id: 3, title: "A Song", artist: "Someone" });
    expect(day.groups["playlist"]?.[0]?.title).toBe("A Playlist");
    expect(day.groups["other"]?.[0]?.detail).toBe("joined");
  });

  it("names an unknown kind instead of silently dropping the row", async () => {
    const stub = new Stub(() => json({ api: "v1", data: [{ kind: "shout" }] }));
    await expect(
      readActivity("https://music.example.com", available(), { fetch: stub.fetch }),
    ).rejects.toThrow(/shout/);
  });

  it("keeps the server's refusal sentence and its status", async () => {
    const stub = new Stub(() => json({ error: "slow down" }, 429));
    const failure = readActivity("https://music.example.com", available(), { fetch: stub.fetch });

    await expect(failure).rejects.toBeInstanceOf(SocialDenied);
    await failure.catch((error: SocialDenied) => {
      expect(error.status).toBe(429);
      expect(error.said).toBe("slow down");
      expect(error.message).toBe("slow down");
    });
  });

  it("refuses an answer that is not a v1 envelope", async () => {
    const stab = new Stub(() => json({ items: [] }));
    await expect(
      readActivity("https://music.example.com", available(), { fetch: stab.fetch }),
    ).rejects.toBeInstanceOf(SocialDenied);

    const wrongApi = new Stub(() => json({ api: "v2", data: [] }));
    await expect(
      readActivity("https://music.example.com", available(), { fetch: wrongApi.fetch }),
    ).rejects.toBeInstanceOf(SocialDenied);
  });

  it("refuses before the wire when the probe did not say activity", async () => {
    for (const state of ["absent", "members", "no-activity"] as const) {
      const stub = new Stub(() => json(FEED));
      const failure = readActivity(
        "https://music.example.com",
        {
          state,
          reason: "the server said so",
          origin: "https://music.example.com",
          server: null,
          requiresMembers: state === "members",
        },
        { fetch: stub.fetch },
      );

      await expect(failure).rejects.toBeInstanceOf(SocialUnavailable);
      await failure.catch((error: SocialUnavailable) => {
        expect(error.state).toBe(state);
        expect(error.message).toContain("the server said so");
      });
      // A refusal that reached the server would be indistinguishable from a lost feed.
      expect(stub.calls).toHaveLength(0);
    }
  });

  it("carries no credential and asks for no member route", async () => {
    const stub = new Stub(() => json(FEED));
    await readActivity("https://music.example.com", available(), { fetch: stub.fetch });
    const urls = stub.calls.map((call) => call.url);

    for (const url of urls) {
      const query = new URL(url).searchParams;
      expect([...query.keys()]).toEqual(["limit"]);
      for (const memberRoute of ["/api/boost", "/api/social", "/api/member"]) {
        expect(url).not.toContain(memberRoute);
      }
    }
  });

  it("keeps a wire failure typed rather than letting it escape", async () => {
    const stub = new Stub(() => new Error("offline"));
    const failure = readActivity("https://music.example.com", available(), { fetch: stub.fetch });
    await expect(failure).rejects.toBeInstanceOf(SocialError);
    await expect(failure).rejects.toBeInstanceOf(SocialDenied);
  });
});