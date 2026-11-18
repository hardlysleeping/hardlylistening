/* The seam between the probe and the window's policy.
 *
 * `social.ts` builds two addresses and calls `fetch`. In a packaged player that `fetch` is this
 * file: it must recognise exactly those two addresses, ask Rust for the document by name, and
 * refuse everything else *before* IPC — because the point of the command is that the page never
 * names a host. These tests hold that line, and the shaping of the answer, without a socket.
 */

import { describe, expect, it } from "vitest";

import type { InvokeLike } from "../subsonic";
import { ACTIVITY_PATH, CAPABILITIES_PATH, probeSocial } from "./social";
import { NATIVE_PUBLIC_COMMAND, nativePublicFetch, publicRead } from "./native";

const ORIGIN = "https://music.example.com";

const DOCUMENT = {
  server: { name: "hardlylistening", version: "1.0", api: "v1", requires_members: false },
  features: { social: { comments: true, votes: true, likes: true, activity: true } },
};

interface Call {
  command: string;
  args: Record<string, unknown> | undefined;
}

/** An `invoke` that records what it was asked and answers with one value. */
function recorder(answer: unknown): { invoke: InvokeLike; calls: Call[] } {
  const calls: Call[] = [];
  const invoke: InvokeLike = async (command, args) => {
    calls.push({ command, args });
    return answer;
  };
  return { invoke, calls };
}

/** An `invoke` that refuses the way Tauri does — by rejecting with the value Rust returned. */
function refusing(answer: unknown): { invoke: InvokeLike; calls: Call[] } {
  const calls: Call[] = [];
  const invoke: InvokeLike = async (command, args) => {
    calls.push({ command, args });
    throw answer;
  };
  return { invoke, calls };
}

describe("which reads this build will make", () => {
  it("recognises the capability document and the feed, and nothing else", () => {
    expect(publicRead(ORIGIN + CAPABILITIES_PATH, ORIGIN)).toEqual({
      resource: "capabilities",
      limit: null,
    });
    expect(publicRead(ORIGIN + ACTIVITY_PATH, ORIGIN)).toEqual({
      resource: "activity",
      limit: null,
    });
    expect(publicRead(`${ORIGIN}${ACTIVITY_PATH}?limit=25`, ORIGIN)).toEqual({
      resource: "activity",
      limit: 25,
    });
  });

  it("tolerates the trailing slash a person typed and nothing more", () => {
    expect(publicRead(`${ORIGIN}${CAPABILITIES_PATH}`, `${ORIGIN}/`)).toEqual({
      resource: "capabilities",
      limit: null,
    });
    // A path that merely starts the same way is not the same document.
    expect(publicRead(`${ORIGIN}${CAPABILITIES_PATH}/extra`, ORIGIN)).toBeNull();
  });

  it("refuses another host, another scheme, another port", () => {
    for (const asked of [
      "https://evil.example.com" + CAPABILITIES_PATH,
      "http://music.example.com" + CAPABILITIES_PATH,
      "https://music.example.com:8443" + CAPABILITIES_PATH,
      "http://127.0.0.1:8470" + ACTIVITY_PATH,
    ]) {
      expect(publicRead(asked, ORIGIN)).toBeNull();
    }
  });

  it("refuses a limit that is not a positive whole number", () => {
    for (const query of ["?limit=0", "?limit=-3", "?limit=abc", "?limit=2.5", "?other=1"]) {
      expect(publicRead(`${ORIGIN}${ACTIVITY_PATH}${query}`, ORIGIN)).toBeNull();
    }
  });

  it("refuses to read anything without an origin to match", () => {
    expect(publicRead(CAPABILITIES_PATH, "")).toBeNull();
    expect(publicRead(CAPABILITIES_PATH, null as unknown as string)).toBeNull();
  });
});

describe("a read that has to travel through Rust", () => {
  it("names the document and the account, never the address", async () => {
    const { invoke, calls } = recorder(DOCUMENT);
    const read = nativePublicFetch({ invoke, accountId: "acc-1", origin: ORIGIN });

    await read(ORIGIN + CAPABILITIES_PATH);

    expect(calls).toEqual([
      {
        command: NATIVE_PUBLIC_COMMAND,
        args: { accountId: "acc-1", resource: "capabilities", limit: null },
      },
    ]);
    // The address is in no argument: the page cannot point this at a server of its choosing.
    expect(JSON.stringify(calls[0]?.args ?? {})).not.toContain("example.com");
  });

  it("passes the feed's size through, and its absence as null", async () => {
    const { invoke, calls } = recorder({ api: "v1", data: [] });
    const read = nativePublicFetch({ invoke, accountId: "acc-1", origin: ORIGIN });

    await read(`${ORIGIN}${ACTIVITY_PATH}?limit=25`);
    await read(ORIGIN + ACTIVITY_PATH);

    expect(calls.map((call) => call.args?.limit)).toEqual([25, null]);
    expect(calls.every((call) => call.args?.resource === "activity")).toBe(true);
  });

  it("answers with a Response the probe can read, unchanged", async () => {
    const { invoke } = recorder(DOCUMENT);
    const read = nativePublicFetch({ invoke, accountId: "acc-1", origin: ORIGIN });

    const response = await read(ORIGIN + CAPABILITIES_PATH);

    expect(response.ok).toBe(true);
    expect(response.status).toBe(200);
    expect(await response.json()).toEqual(DOCUMENT);
  });

  it("refuses an address it does not recognise without touching the native side", async () => {
    const { invoke, calls } = recorder(DOCUMENT);
    const read = nativePublicFetch({ invoke, accountId: "acc-1", origin: ORIGIN });

    await expect(read("https://evil.example.com/api/v1/capabilities")).rejects.toThrow(
      /capability document and its feed, and nothing else/,
    );
    expect(calls).toEqual([]);
  });

  it("hands Rust's refusal up as a sentence, not as an object", async () => {
    const failure = { kind: "http", status: 500, message: "https://music.example.com answered HTTP 500" };
    const { invoke } = refusing(failure);
    const read = nativePublicFetch({ invoke, accountId: "acc-1", origin: ORIGIN });

    await expect(read(ORIGIN + CAPABILITIES_PATH)).rejects.toThrow(
      "https://music.example.com answered HTTP 500",
    );
  });
});

describe("the probe over the native read", () => {
  it("reports the feed when the document offers one", async () => {
    const { invoke } = recorder(DOCUMENT);
    const read = nativePublicFetch({ invoke, accountId: "acc-1", origin: ORIGIN });

    const state = await probeSocial(ORIGIN, { fetch: read });

    expect(state.state).toBe("activity");
    expect(state.requiresMembers).toBe(false);
    expect(state.server).toEqual({ name: "hardlylistening", version: "1.0" });
  });

  it("reports absent with the refusal as its reason, never throwing", async () => {
    const { invoke } = refusing({ kind: "transport", status: null, message: "Could not reach https://music.example.com" });
    const read = nativePublicFetch({ invoke, accountId: "acc-1", origin: ORIGIN });

    const state = await probeSocial(ORIGIN, { fetch: read });

    expect(state.state).toBe("absent");
    expect(state.reason).toContain("Could not reach https://music.example.com");
  });
});