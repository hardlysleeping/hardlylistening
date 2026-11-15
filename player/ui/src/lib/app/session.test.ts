/* Which transport a session uses, and what crosses to the native side.
 *
 * The property worth pinning is not "the native path works" — that needs a real build — but the
 * shape of the conversation: a native session stores the secret and never asks for it back, a
 * server that refuses token authentication is rebound with the password form rather than re-sent
 * the secret from the page, and a shell without the commands still talks to the server through
 * `fetch`.
 */

import { describe, expect, it, vi } from "vitest";
import type { AudioEngine } from "../player/engine";
import type { Vault } from "../accounts/vault";
import type { InvokeLike } from "../subsonic/transport";

// The account list is the browser's; a node test has none.
(globalThis as unknown as { localStorage: unknown }).localStorage = {
  getItem: () => null,
  setItem: () => {},
};

const engine: AudioEngine = {
  kind: "web",
  ownsQueue: false,
  setSource: () => {},
  play: () => {},
  pause: () => {},
  seekFraction: () => {},
  setVolume: () => {},
  setMuted: () => {},
  destroy: () => {},
};

const { PlayerStore } = await import("../player/store.svelte");
const { Session } = await import("./session.svelte");

const OK = { "subsonic-response": { status: "ok", version: "1.16.1", type: "navidrome" } };

function refused(code: number) {
  return {
    "subsonic-response": {
      status: "failed",
      version: "1.16.1",
      error: { code, message: "the server said no" },
    },
  };
}

type Recorded = [string, Record<string, unknown>];

function fakeVault() {
  const cells = new Map<string, string>();
  const get = vi.fn(async (account: string) => cells.get(account) ?? null);
  const put = vi.fn(async (account: string, secret: string) => {
    cells.set(account, secret);
    return "keychain" as const;
  });
  const forget = vi.fn(async (account: string) => {
    cells.delete(account);
  });
  const vault: Vault = { mode: "keychain", get, put, forget };
  return { vault, get, put, forget };
}

function fakeInvoke(answer: (command: string, args: Record<string, unknown>) => unknown) {
  const calls: Recorded[] = [];
  const invoke: InvokeLike = async (command, args) => {
    calls.push([command, args ?? {}]);
    return answer(command, args ?? {});
  };
  return { invoke, calls };
}

const SIGN_IN = { server: "https://music.example.com", username: "alex", password: "hunter2" };

/** The account list a previous session would have left in the window. */
function storedAccount(document: unknown): void {
  (globalThis as unknown as { localStorage: unknown }).localStorage = {
    getItem: () => JSON.stringify(document),
    setItem: () => {},
  };
}

describe("a session with native networking", () => {
  it("leaves an already saved account's secret alone when a fresh sign-in fails", async () => {
    // A person retypes their password and gets it wrong. The account that was working must not
    // be erased by the attempt.
    storedAccount({
      version: 1,
      // A saved account nobody is signed in to: the state after closing the window.
      activeId: null,
      accounts: [
        {
          id: "acc-old",
          server: "https://music.example.com",
          username: "alex",
          label: "music.example.com",
          form: "token",
          added: "2026-11-01T00:00:00.000Z",
        },
      ],
    });

    const store = fakeVault();
    const { invoke } = fakeInvoke((command) => (command === "subsonic_request" ? refused(40) : undefined));
    const session = new Session(new PlayerStore(engine), { vault: store.vault, invoke });

    expect(await session.connect(SIGN_IN)).toBe(false);

    expect(store.forget).not.toHaveBeenCalled();
    expect(session.records.map((record) => record.id)).toEqual(["acc-old"]);
    // Back to the browser's own store for the rest of the file.
    (globalThis as unknown as { localStorage: unknown }).localStorage = {
      getItem: () => null,
      setItem: () => {},
    };
  });

  it("binds the account and never reads the secret back", async () => {
    const store = fakeVault();
    const { invoke, calls } = fakeInvoke(() => OK);
    const session = new Session(new PlayerStore(engine), { vault: store.vault, invoke });

    expect(await session.connect(SIGN_IN)).toBe(true);

    expect(calls.map(([command]) => command)).toEqual(["subsonic_bind", "subsonic_request"]);
    // The password went into the vault and no further.
    expect(store.put).toHaveBeenCalled();
    expect(store.get).not.toHaveBeenCalled();
    expect(JSON.stringify(calls)).not.toContain("hunter2");
    expect(session.client?.transport.kind).toBe("native");
  });

  it("reports a native refusal as a failed sign-in instead of throwing at the caller", async () => {
    // The keychain refused, or the account vanished between launches. The connect screen shows
    // the reason; it does not get an unhandled rejection.
    const store = fakeVault();
    const invoke: InvokeLike = async () => {
      throw { kind: "config", status: null, message: "The keychain has no password for that account" };
    };
    const session = new Session(new PlayerStore(engine), { vault: store.vault, invoke });

    expect(await session.connect(SIGN_IN)).toBe(false);
    expect(session.status).toBe("error");
    expect(session.error).toContain("keychain");
  });

  it("binds again with the password form when the server refuses a token", async () => {
    const store = fakeVault();
    let pings = 0;
    const { invoke, calls } = fakeInvoke((command) => {
      if (command !== "subsonic_request") return undefined;
      pings += 1;
      return pings === 1 ? refused(41) : OK;
    });
    const session = new Session(new PlayerStore(engine), { vault: store.vault, invoke });

    expect(await session.connect(SIGN_IN)).toBe(true);

    const forms = calls.filter(([command]) => command === "subsonic_bind").map(([, args]) => args["form"]);
    expect(forms).toEqual(["token", "password"]);
    expect(session.active?.form).toBe("password");
  });

  it("reconnects to a saved account without the page holding the secret", async () => {
    const store = fakeVault();
    const { invoke } = fakeInvoke(() => OK);
    const session = new Session(new PlayerStore(engine), { vault: store.vault, invoke });
    await session.connect(SIGN_IN);
    const id = session.activeId!;

    await session.signOut();
    store.get.mockClear();
    expect(await session.activate(id)).toBe(true);

    expect(store.get).not.toHaveBeenCalled();
    expect(session.client?.transport.kind).toBe("native");
  });
});

describe("a session without the native commands", () => {
  it("falls back to fetch rather than refusing to play", async () => {
    const store = fakeVault();
    // A build that predates the commands answers with Tauri's "command not found".
    const invoke: InvokeLike = async () => {
      throw new Error("Command subsonic_bind not found");
    };
    const fetch = vi.fn(async () => new Response(JSON.stringify(OK), { status: 200 }));

    const session = new Session(new PlayerStore(engine), { vault: store.vault, invoke, fetch });

    expect(await session.connect(SIGN_IN)).toBe(true);
    expect(session.client?.transport.kind).toBe("web");
    expect(fetch).toHaveBeenCalled();

    // Restoring is where this path's cost shows: with no native side to read the vault, the page
    // has to ask for the secret itself.
    const id = session.activeId!;
    await session.signOut();
    store.get.mockClear();
    expect(await session.activate(id)).toBe(true);
    expect(store.get).toHaveBeenCalledWith(id);
  });
});