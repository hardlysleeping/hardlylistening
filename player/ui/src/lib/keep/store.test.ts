/* What the page is allowed to know about kept copies.
 *
 * Nothing here touches a disk: Rust owns the folder. These tests pin the half that decides
 * behaviour — which song is held, which address is played, that a held song is never fetched
 * twice, and that one refusal stops a run instead of fetching the rest and failing the same way.
 */

import { afterEach, describe, expect, it, vi } from "vitest";
import { KeepStore, type KeptSong } from "./store.svelte";
import type { Song } from "../subsonic";

const KEPT: KeptSong = {
  id: "42",
  title: "A song",
  artist: "Someone",
  album: "Somewhere",
  suffix: "mp3",
  bytes: 1_000,
  keptAt: 1,
};

function song(id: string, title = "A song"): Song {
  return { id, title, artist: "Someone", album: "Somewhere", suffix: "mp3" };
}

/** An invoke that answers each command from a table, and records what it was asked. */
function harness(answers: Record<string, unknown> = {}) {
  const calls: Array<{ command: string; args?: Record<string, unknown> }> = [];
  const invoke = vi.fn(async (command: string, args?: Record<string, unknown>) => {
    calls.push({ command, args });
    if (command in answers) {
      const answer = answers[command];
      return typeof answer === "function" ? (answer as (a?: unknown) => unknown)(args) : answer;
    }
    return undefined;
  });
  return { invoke, calls };
}

afterEach(() => {
  delete (globalThis as { __TAURI__?: unknown }).__TAURI__;
});

describe("the kept list", () => {
  it("reads the folder's index when it is attached", async () => {
    const { invoke, calls } = harness({ keep_list: [KEPT], keep_clear: null });
    const keep = new KeepStore();
    keep.attach(invoke, "acct");
    await vi.waitFor(() => expect(keep.rows).toHaveLength(1));
    expect(calls[0]?.command).toBe("keep_list");
    expect(keep.has("42")).toBe(true);
    expect(keep.bytes).toBe(1_000);
  });

  it("replaces its list from the index rather than merging into it", async () => {
    const { invoke } = harness({ keep_list: [KEPT] });
    const keep = new KeepStore();
    keep.attach(invoke, "acct");
    await vi.waitFor(() => expect(keep.rows).toHaveLength(1));
    keep.rows = [];
    await keep.refresh();
    expect(keep.rows).toHaveLength(1);
  });

  it("says it cannot keep anything when the shell has no keep commands", async () => {
    const invoke = vi.fn(async () => {
      throw new Error("keep_list is not a command");
    });
    const keep = new KeepStore();
    keep.attach(invoke, "acct");
    await vi.waitFor(() => expect(keep.error).toContain("keep_list"));
  });
});

describe("keeping a song", () => {
  it("sends the song's own fields with the account, then re-reads the index", async () => {
    // The folder's index is the truth: it is empty until the fetch has written the row.
    const held: KeptSong[] = [];
    const { invoke, calls } = harness({
      keep_list: () => held,
      keep_add: () => {
        held.push(KEPT);
        return KEPT;
      },
    });
    const keep = new KeepStore();
    keep.attach(invoke, "acct");
    await vi.waitFor(() => expect(keep.ready).toBe(true));

    const added = await keep.keep(song("42"));

    const add = calls.find((call) => call.command === "keep_add");
    expect(add?.args).toEqual({
      accountId: "acct",
      song: { id: "42", title: "A song", artist: "Someone", album: "Somewhere", suffix: "mp3" },
    });
    expect(added).toBe(true);
    expect(calls.filter((call) => call.command === "keep_list").length).toBeGreaterThan(1);
  });

  it("never fetches a song it already holds", async () => {
    const { invoke, calls } = harness({ keep_list: [KEPT] });
    const keep = new KeepStore();
    keep.attach(invoke, "acct");
    await vi.waitFor(() => expect(keep.rows).toHaveLength(1));

    expect(await keep.keep(song("42"))).toBe(true);
    expect(calls.some((call) => call.command === "keep_add")).toBe(false);
  });

  it("stops a run at the first refusal instead of fetching the rest", async () => {
    const { invoke, calls } = harness({
      keep_list: [],
      keep_add: () => {
        throw new Error("The song stopped early");
      },
    });
    const keep = new KeepStore();
    keep.attach(invoke, "acct");
    await vi.waitFor(() => expect(keep.ready).toBe(true));

    await keep.keepAll([song("1"), song("2"), song("3")]);

    expect(calls.filter((call) => call.command === "keep_add")).toHaveLength(1);
    expect(keep.error).toContain("stopped early");
  });

  it("reports a refusal instead of pretending the song is held", async () => {
    const { invoke } = harness({
      keep_list: [],
      keep_add: () => {
        throw new Error("The server sent no audio for that song");
      },
    });
    const keep = new KeepStore();
    keep.attach(invoke, "acct");
    await vi.waitFor(() => expect(keep.ready).toBe(true));

    expect(await keep.keep(song("9"))).toBe(false);
    expect(keep.has("9")).toBe(false);
    expect(keep.error).toContain("no audio");
  });
});

describe("playing a kept copy", () => {
  it("answers the loopback address for a song it holds", async () => {
    const { invoke } = harness({ keep_list: [KEPT], keep_source: "http://127.0.0.1:9/42" });
    const keep = new KeepStore();
    keep.attach(invoke, "acct");
    await vi.waitFor(() => expect(keep.rows).toHaveLength(1));

    expect(await keep.sourceFor("42")).toBe("http://127.0.0.1:9/42");
  });

  it("does not ask the shell at all about a song it does not hold", async () => {
    const { invoke, calls } = harness({ keep_list: [KEPT], keep_source: "http://127.0.0.1:9/42" });
    const keep = new KeepStore();
    keep.attach(invoke, "acct");
    await vi.waitFor(() => expect(keep.rows).toHaveLength(1));

    expect(await keep.sourceFor("99")).toBeNull();
    expect(calls.some((call) => call.command === "keep_source")).toBe(false);
  });

  it("falls back to the server when the held copy cannot be read", async () => {
    const { invoke } = harness({
      keep_list: [KEPT],
      keep_source: () => {
        throw new Error("the file is gone");
      },
    });
    const keep = new KeepStore();
    keep.attach(invoke, "acct");
    await vi.waitFor(() => expect(keep.rows).toHaveLength(1));

    expect(await keep.sourceFor("42")).toBeNull();
  });
});

describe("forgetting", () => {
  it("drops the row without re-reading the index", async () => {
    const { invoke, calls } = harness({ keep_list: [KEPT], keep_remove: null });
    const keep = new KeepStore();
    keep.attach(invoke, "acct");
    await vi.waitFor(() => expect(keep.rows).toHaveLength(1));

    await keep.forget("42");

    expect(calls.filter((call) => call.command === "keep_list")).toHaveLength(1);
    expect(calls.find((call) => call.command === "keep_remove")?.args).toEqual({ songId: "42" });
    expect(keep.rows).toHaveLength(0);
  });

  it("empties the folder and the list together", async () => {
    const { invoke } = harness({ keep_list: [KEPT], keep_clear: null });
    const keep = new KeepStore();
    keep.attach(invoke, "acct");
    await vi.waitFor(() => expect(keep.rows).toHaveLength(1));

    await keep.clear();

    expect(keep.rows).toHaveLength(0);
    expect(keep.bytes).toBe(0);
  });
});

describe("the progress event", () => {
  it("moves the bar for a fetch that is in flight", async () => {
    // A holder rather than a bare `let`: the handler is assigned inside a callback, and a
    // closure assignment is exactly what control-flow narrowing cannot see.
    const bridge: { handle: ((payload: unknown) => void) | null } = { handle: null };
    const listen = vi.fn(async (_event: string, handler: (payload: unknown) => void) => {
      bridge.handle = handler;
    });
    (globalThis as { __TAURI__?: unknown }).__TAURI__ = { event: { listen } };

    // A fetch that does not finish until the test says so. Held in an object for the same
    // reason as the event handler above: the callback assigns it, so narrowing cannot see it.
    const gate: { release: (() => void) | null } = { release: null };
    const invoke = vi.fn(async (command: string) => {
      if (command === "keep_add") await new Promise<void>((done) => (gate.release = done));
      return command === "keep_list" ? [] : null;
    });
    const keep = new KeepStore();
    keep.attach(invoke, "acct");
    const running = keep.keep(song("42"));

    await vi.waitFor(() => expect(keep.keeping("42")).toBe(true));
    bridge.handle?.({ id: "42", received: 512, total: 1024 });
    expect(keep.progress["42"]).toEqual({ received: 512, total: 1024 });

    gate.release?.();
    await running;
    expect(keep.progress["42"]).toBeUndefined();
    expect(listen).toHaveBeenCalledWith("hl:keep", expect.any(Function));
  });

  it("ignores a straggler for a fetch that already finished", async () => {
    const bridge: { handle: ((payload: unknown) => void) | null } = { handle: null };
    (globalThis as { __TAURI__?: unknown }).__TAURI__ = {
      event: {
        listen: async (_event: string, handler: (payload: unknown) => void) => {
          bridge.handle = handler;
        },
      },
    };
    const keep = new KeepStore();
    keep.attach(vi.fn(async (command: string) => (command === "keep_list" ? [] : null)), "acct");

    bridge.handle?.({ id: "42", received: 10, total: 10 });
    expect(keep.progress["42"]).toBeUndefined();
  });
});