/* The social read, made by the side that is allowed to make it.
 *
 * `social.ts` decides what a server can offer from the document it publishes, and it does that
 * through `fetch`. In a packaged player the window has a policy: `connect-src` is the asset origin
 * and IPC, so a `fetch()` aimed at the music server is refused by the platform before a packet
 * leaves. The fix is not a looser policy — the page still may not reach a host it names. It is a
 * command that names the *document*, with Rust resolving the address from the bound account, the
 * same rule `/rest` already follows.
 *
 * So this file is the seam between the two: a `fetch` that recognises exactly the two addresses
 * the probe builds, asks Rust for them by name, and answers with a `Response` shaped like the one
 * the network would have produced. Anything else is refused here — before IPC — because a URL this
 * module does not recognise is not one of the reads it exists for.
 */

import type { FetchLike, InvokeLike } from "../subsonic";
import { ACTIVITY_PATH, CAPABILITIES_PATH } from "./social";

/** The command `src-tauri/src/subsonic.rs` answers. */
export const NATIVE_PUBLIC_COMMAND = "subsonic_public";

export type PublicResource = "capabilities" | "activity";

export interface PublicRead {
  resource: PublicResource;
  /** The feed's size, when the address asked for one; `null` means the server's own default. */
  limit: number | null;
}

function trimOrigin(origin: string): string {
  return String(origin ?? "").replace(/\/+$/, "");
}

/**
 * The read an address stands for, or `null` when it is not one of this module's two.
 *
 * The origin is compared whole, so a URL aimed anywhere else — another host, another path, a
 * prefix that merely starts the same way — is not a read this build can make.
 */
export function publicRead(url: string, origin: string): PublicRead | null {
  const base = trimOrigin(origin);
  if (!base) return null;
  const asked = String(url ?? "");

  if (asked === base + CAPABILITIES_PATH) return { resource: "capabilities", limit: null };

  const feed = base + ACTIVITY_PATH;
  if (asked === feed) return { resource: "activity", limit: null };
  if (!asked.startsWith(feed + "?")) return null;

  const query = new URLSearchParams(asked.slice(feed.length + 1));
  // Only the feed's own parameter is a read this build knows; anything else is not the
  // address the probe builds, and guessing what it meant would be inventing a request.
  for (const key of query.keys()) {
    if (key !== "limit") return null;
  }
  const raw = query.get("limit");
  if (raw === null) return { resource: "activity", limit: null };
  const limit = Number(raw);
  // A limit that is not a positive whole number is not a limit; refusing turns it into the
  // probe's own "absent with a reason" instead of a request the server would have to clamp.
  if (!Number.isFinite(limit) || limit <= 0 || !Number.isInteger(limit)) return null;
  return { resource: "activity", limit };
}

/** What Rust sends instead of a string when it refuses (`subsonic.rs`'s `Failure`). */
function said(error: unknown): string {
  if (typeof error === "object" && error !== null) {
    const message = (error as { message?: unknown }).message;
    if (typeof message === "string" && message.trim()) return message.trim();
  }
  if (error instanceof Error && error.message.trim()) return error.message.trim();
  return String(error ?? "the request failed");
}

export interface NativePublicOptions {
  invoke: InvokeLike;
  /** The account whose address is used. The page never sends the address itself. */
  accountId: string;
  /** The address the probe builds its URLs from — the same string, used only for matching. */
  origin: string;
}

/**
 * A `fetch` for `probeSocial`/`readActivity` that goes through the native side.
 *
 * Refusals arrive as an `Error` carrying the sentence Rust produced, which is what the probe's
 * catch turns into `absent` with a reason — never as a silent success.
 */
export function nativePublicFetch(options: NativePublicOptions): FetchLike {
  const { invoke, accountId, origin } = options;
  return async (input) => {
    const read = publicRead(input, origin);
    if (!read) {
      throw new Error(
        "This player reads its server's capability document and its feed, and nothing else",
      );
    }

    let body: unknown;
    try {
      body = await invoke(NATIVE_PUBLIC_COMMAND, {
        accountId,
        resource: read.resource,
        limit: read.limit,
      });
    } catch (error) {
      throw new Error(said(error));
    }

    return new Response(JSON.stringify(body ?? null), {
      status: 200,
      headers: { "Content-Type": "application/json" },
    });
  };
}