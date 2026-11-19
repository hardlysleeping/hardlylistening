/* What the native transport is allowed to say to Rust.
 *
 * The point of this path is the shape of the message: an account, an endpoint name and that
 * endpoint's parameters — never an address, and never a secret. These tests use a fake `invoke`
 * that records every argument, because the property is about the arguments rather than about an
 * HTTP answer: a URL or a password appearing in them is the failure, whatever the server replies.
 */

import { describe, expect, it } from "vitest";
import { NativeTransport, WebTransport, type FetchLike, type InvokeLike } from "./transport";
import { ClientError, SubsonicError, TransportError } from "./errors";

/** Every argument a call was given, flattened to the strings it may contain. */
function argumentStrings(args: Record<string, unknown> | undefined): string[] {
  const out: string[] = [];
  const walk = (value: unknown): void => {
    if (typeof value === "string") out.push(value);
    else if (typeof value === "number" || typeof value === "boolean") out.push(String(value));
    else if (Array.isArray(value)) value.forEach(walk);
    else if (value && typeof value === "object") Object.values(value).forEach(walk);
  };
  walk(args ?? {});
  return out;
}

function recorder(answer: unknown = {}): { invoke: InvokeLike; calls: Array<[string, Record<string, unknown>]> } {
  const calls: Array<[string, Record<string, unknown>]> = [];
  const invoke: InvokeLike = async (command, args) => {
    calls.push([command, args ?? {}]);
    return answer;
  };
  return { invoke, calls };
}

function transport(invoke: InvokeLike): NativeTransport {
  return new NativeTransport({
    accountId: "acc-1",
    origin: "https://music.example.com",
    username: "alex",
    form: "token",
    invoke,
  });
}

describe("the native transport", () => {
  it("refuses an endpoint that is a path or an address", async () => {
    const { invoke, calls } = recorder();
    const native = transport(invoke);

    for (const endpoint of ["../admin", "getAlbumList2.view?a=1", "https://elsewhere.example/rest/ping", "", "get/../../x"]) {
      await expect(native.request({ endpoint })).rejects.toBeInstanceOf(ClientError);
    }
    // A refused endpoint never becomes a request.
    expect(calls).toHaveLength(0);
  });

  it("refuses to let a caller set the protocol's own parameters", async () => {
    const { invoke, calls } = recorder();
    const native = transport(invoke);

    for (const key of ["u", "p", "t", "s", "v", "c", "f"]) {
      await expect(native.request({ endpoint: "getAlbumList2", params: { [key]: "x" } })).rejects.toBeInstanceOf(
        ClientError,
      );
    }
    expect(calls).toHaveLength(0);
  });

  it("binds the account by its public record, and sends no secret with it", async () => {
    const { invoke, calls } = recorder();
    const native = transport(invoke);

    await native.bind();

    expect(calls).toEqual([
      [
        "subsonic_bind",
        {
          accountId: "acc-1",
          origin: "https://music.example.com",
          username: "alex",
          form: "token",
        },
      ],
    ]);
    // The secret is the keychain's; the page has nothing to send because it never reads it.
    expect(argumentStrings(calls[0]?.[1])).not.toContain("password");
  });

  it("binds again with the other form without ever naming the secret", async () => {
    const { invoke, calls } = recorder();
    const native = transport(invoke);

    await native.bind("password");

    expect(calls[0]?.[1]?.["form"]).toBe("password");
    expect(Object.keys(calls[0]?.[1] ?? {}).sort()).toEqual(["accountId", "form", "origin", "username"]);
  });

  it("says plainly when the account cannot be bound", async () => {
    const native = transport(async () => {
      throw { kind: "config", status: null, message: "no secret is stored for that account" };
    });

    const error = await native.bind().catch((caught: unknown) => caught);
    expect(error).toBeInstanceOf(ClientError);
    expect((error as ClientError).message).toContain("no secret");
  });

  it("maps the server's refusal onto the protocol's own code", async () => {
    const deny = async () => {
      throw { kind: "http", status: 401, message: "HTTP 401" };
    };
    const native = transport(deny);

    const error = await native.request({ endpoint: "ping" }).catch((caught: unknown) => caught);
    expect(error).toBeInstanceOf(SubsonicError);
    expect((error as SubsonicError).code).toBe(40);
  });

  it("reports an unreachable server as a transport error, keeping the native message", async () => {
    const fail = async () => {
      throw { kind: "transport", status: null, message: "dns error: failed to lookup address" };
    };
    const native = transport(fail);

    const error = await native.request({ endpoint: "ping" }).catch((caught: unknown) => caught);
    expect(error).toBeInstanceOf(TransportError);
    expect((error as TransportError).message).toContain("failed to lookup address");
  });

  it("asks for a stream and for artwork by id, never by address", async () => {
    const calls: Array<[string, Record<string, unknown>]> = [];
    const invoke: InvokeLike = async (command, args) => {
      calls.push([command, args ?? {}]);
      // The native side answers with an address; the page only ever sent an id.
      return command === "subsonic_source"
        ? { url: "https://music.example.com/rest/stream.view?id=42&t=abc", transcoded: false }
        : "https://music.example.com/rest/getCoverArt.view?id=77&size=300&t=abc";
    };
    const native = transport(invoke);

    await native.source("42");
    await native.cover("77", 300);

    expect(calls.map(([command]) => command)).toEqual(["subsonic_source", "subsonic_cover"]);
    for (const [, args] of calls) {
      expect(argumentStrings(args).some((value) => value.includes("/rest/") || value.includes("://"))).toBe(false);
    }
  });
});
/* The web path's failure line, which is the only thing the connect screen has to say when a
 * server is not there. The test is about the words: an engine's own phrase for "no answer" is
 * not something a person can act on, and it is not this client's voice. */
describe("a request that got no answer", () => {
  const origin = "http://127.0.0.1:9";

  function refusing(message: string): WebTransport {
    const fetch: FetchLike = async () => {
      throw new TypeError(message);
    };
    return new WebTransport({ origin, credentials: { username: "u", secret: "s", form: "password" }, fetch });
  }

  it("never repeats the engine's phrase for a network failure", async () => {
    /* Chromium, Firefox, WebKit and the React Native polyfill each word it differently, and a
     * player build is the same code on all four. */
    const phrases = [
      "Failed to fetch",
      "NetworkError when attempting to fetch resource.",
      "Load failed",
      "fetch failed",
      "Network request failed",
    ];

    for (const phrase of phrases) {
      const error = await refusing(phrase).request({ endpoint: "ping" }).catch((caught: unknown) => caught);
      expect(error).toBeInstanceOf(TransportError);
      const message = (error as TransportError).message;
      expect(message).toBe(`Could not reach ${origin} — the request got no answer`);
      expect(message).not.toContain(phrase);
    }
  });

  it("keeps a failure that says something the person can use", async () => {
    const error = await refusing("CORS header 'Access-Control-Allow-Origin' missing")
      .request({ endpoint: "ping" })
      .catch((caught: unknown) => caught);
    expect((error as TransportError).message).toBe(
      `Could not reach ${origin} — CORS header 'Access-Control-Allow-Origin' missing`,
    );
  });

  it("still names the address, so a mistyped host reads as itself", async () => {
    const error = await refusing("Failed to fetch").request({ endpoint: "ping" }).catch((caught: unknown) => caught);
    expect((error as TransportError).message).toContain(origin);
  });
});
