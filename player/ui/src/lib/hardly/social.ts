/* HardlyListening's own social surface — offered only when the server says it has one.
 *
 * Everything else this player talks to is Subsonic: a catalogue, a queue, stars. Social is not in
 * that protocol. The activity feed, comments and votes are a HardlyListening server's own tables,
 * reached through its own `/api/v1/*`, and the answer to "is there a feed here at all?" comes from
 * the server's capability document — never from this app's source and never from a guess.
 *
 * Three conditions, all read from the document the server publishes:
 *
 *   1. `GET /api/v1/capabilities` answers at all. A plain Subsonic server has no such address.
 *   2. It names `features.social.activity`. A domain without that flag is not a feed.
 *   3. It does not require members. The feed is a member's read, and this player holds no member
 *      session — only Subsonic credentials — so a server that requires members is a server whose
 *      feed this client cannot read, which is a fact about the client, not about the server.
 *
 * The four states below are the whole decision. Absent is not a failure: it is the ordinary
 * answer from a plain Subsonic server, and every degradation — refused, HTML, empty, unreadable —
 * lands there **with a reason**, because a capability read that failed is not a capability.
 *
 * Nothing here throws during a probe, holds a credential, or reads storage. The probe is inert.
 */

import type { FetchLike } from "../subsonic";

/** Where a HardlyListening server publishes its capability document (`sdk/capabilities.py`). */
export const CAPABILITIES_PATH = "/api/v1/capabilities";
/** The feed's versioned read (`api/v1.py`; `sdk/social.py`'s `ACTIVITY_PATH`). */
export const ACTIVITY_PATH = "/api/v1/social/activity";
/** What the app's own feed asks for; the server clamps it to 1..200. */
export const ACTIVITY_LIMIT = 80;

/** `absent` is the ordinary answer from a server that is not this one. */
export type SocialState = "activity" | "members" | "no-activity" | "absent";

export interface SocialAvailability {
  state: SocialState;
  /** Why this state, in a sentence a person can read. Names the address and status on a failure. */
  reason: string;
  origin: string;
  /** Who answered, when a document was readable at all. */
  server: { name: string; version: string } | null;
  requiresMembers: boolean;
}

export interface SocialMember {
  id: string;
  name: string;
  icon: string;
}

/** The feed's own flat track shape. Its ids are the server's rows, not Subsonic's opaque ids. */
export interface SocialTrack {
  id: number;
  title: string;
  artist: string;
}

export interface BoostItem {
  kind: "boost";
  ts: number;
  expiresAt: number;
  swapped: boolean;
  alreadyActive: boolean;
  track: SocialTrack | null;
  member: SocialMember | null;
}

export interface SocialEntry {
  kind: string;
  ts: number;
  title: string;
  detail: string;
  playlistId: number;
  track: SocialTrack | null;
}

export interface DayItem {
  kind: "day";
  member: SocialMember;
  day: string;
  ts: number;
  firstTs: number;
  counts: Record<string, number>;
  groups: Record<string, SocialEntry[]>;
}

export type SocialItem = BoostItem | DayItem;

/** A refusal this module decided itself, or an answer it could not read. */
export class SocialError extends Error {
  constructor(message: string) {
    super(message);
    this.name = "SocialError";
  }
}

/** No feed to read — either the server has none, or it has one this player cannot read. */
export class SocialUnavailable extends SocialError {
  readonly state: SocialState;

  constructor(state: SocialState, reason: string) {
    super(`there is no activity feed to read: ${reason}`);
    this.name = "SocialUnavailable";
    this.state = state;
  }
}

/** The server (or a proxy) answered and declined, and said why where it said anything. */
export class SocialDenied extends SocialError {
  readonly status: number;
  /** The server's own sentence, or empty when the status came with no refusal body. */
  readonly said: string;

  constructor(status: number, said: string) {
    super(said || `the activity feed answered HTTP ${status}`);
    this.name = "SocialDenied";
    this.status = status;
    this.said = said;
  }
}

type Bag = Record<string, unknown>;

function isBag(value: unknown): value is Bag {
  return typeof value === "object" && value !== null && !Array.isArray(value);
}

function list(value: unknown): unknown[] {
  return Array.isArray(value) ? value : [];
}

function text(value: unknown, fallback = ""): string {
  return typeof value === "string" && value ? value : fallback;
}

function number(value: unknown): number {
  const asNumber = Number(value);
  return Number.isFinite(asNumber) ? asNumber : 0;
}

function count(value: unknown): number {
  return Math.trunc(number(value));
}

function messageOf(error: unknown): string {
  return error instanceof Error ? error.message : String(error);
}

function trimOrigin(base: string): string {
  return String(base ?? "").replace(/\/+$/, "");
}

/** One probe's answer, built in one place so every refusal carries the same fields. */
function availability(
  origin: string,
  state: SocialState,
  reason: string,
  server: SocialAvailability["server"] = null,
  requiresMembers = false,
): SocialAvailability {
  return { state, reason, origin, server, requiresMembers };
}

export interface ProbeOptions {
  /** Defaults to the platform `fetch`. Injected by tests, and by a session that has its own. */
  fetch?: FetchLike;
}

/**
 * Whether the connected server has an activity feed.
 *
 * Never throws: a missing, refused, unreadable or empty document is `absent` with a reason, and
 * the caller can go on starting the player. That rule is the same one `sdk.capabilities.probe`
 * follows on the server side, for the same reason — a capability read that failed must not stop
 * anything else.
 */
export async function probeSocial(
  base: string,
  options: ProbeOptions = {},
): Promise<SocialAvailability> {
  const origin = trimOrigin(base);
  if (!origin) return availability("", "absent", "no server address to ask");

  const address = origin + CAPABILITIES_PATH;
  const ask = options.fetch ?? ((input: string, init?: RequestInit) => fetch(input, init));

  let response: Response;
  try {
    // Credentials are omitted explicitly: the document is public, and a session cookie is not
    // this player's to send.
    response = await ask(address, {
      headers: { Accept: "application/json" },
      credentials: "omit",
    });
  } catch (error) {
    return availability(origin, "absent", `${address} did not answer: ${messageOf(error)}`);
  }

  if (!response.ok) {
    return availability(origin, "absent", `${address} answered HTTP ${response.status}`);
  }

  let document: unknown;
  try {
    document = await response.json();
  } catch {
    return availability(origin, "absent", `${address} answered, but not with JSON`);
  }

  if (!isBag(document) || Object.keys(document).length === 0) {
    return availability(origin, "absent", `${address} answered with an empty document`);
  }

  const server = isBag(document["server"]) ? document["server"] : {};
  const features = isBag(document["features"]) ? document["features"] : {};
  const social = isBag(features["social"]) ? features["social"] : null;
  const named = { name: text(server["name"]), version: text(server["version"]) };
  const requiresMembers = server["requires_members"] === true;

  // A domain the document did not name (or named with no flags) is not a feed. `bool(details)`
  // on the server side is the same reading, and it is why this is not "assume it is probably
  // there".
  if (!social || Object.keys(social).length === 0) {
    return availability(origin, "absent", `${origin} names no social domain`, named, requiresMembers);
  }

  if (social["activity"] !== true) {
    return availability(
      origin,
      "no-activity",
      `${origin} names a social domain but not social.activity`,
      named,
      requiresMembers,
    );
  }

  if (requiresMembers) {
    return availability(
      origin,
      "members",
      `${origin} offers social.activity, but it requires members and this player holds no member session`,
      named,
      requiresMembers,
    );
  }

  return availability(
    origin,
    "activity",
    `${origin} offers social.activity and needs no member session`,
    named,
    requiresMembers,
  );
}

export interface ActivityOptions {
  fetch?: FetchLike;
  /** Rows to ask for. Passed through unchanged; the server clamps to 1..200. */
  limit?: number;
}

/**
 * The activity feed, read from the versioned endpoint.
 *
 * Refuses before the wire when the probe did not say `activity` — a control built on an `absent`
 * server must fail loudly here rather than draw an empty feed, because an empty list and a
 * missing feature look identical on screen and mean opposite things.
 */
export async function readActivity(
  base: string,
  state: SocialAvailability,
  options: ActivityOptions = {},
): Promise<SocialItem[]> {
  if (state.state !== "activity") throw new SocialUnavailable(state.state, state.reason);

  const origin = trimOrigin(base) || state.origin;
  const limit = options.limit !== undefined && Number.isFinite(options.limit) && options.limit > 0
    ? Math.trunc(options.limit)
    : ACTIVITY_LIMIT;
  const address = `${origin}${ACTIVITY_PATH}?limit=${limit}`;
  const ask = options.fetch ?? ((input: string, init?: RequestInit) => fetch(input, init));

  let response: Response;
  try {
    response = await ask(address, {
      headers: { Accept: "application/json" },
      credentials: "omit",
    });
  } catch (error) {
    throw new SocialDenied(0, `${address} did not answer: ${messageOf(error)}`);
  }

  if (!response.ok) {
    // The refusal sentence is the server's, kept as it said it; the status is the branch.
    let said = "";
    try {
      const body: unknown = await response.json();
      if (isBag(body)) said = text(body["error"]);
    } catch {
      said = "";
    }
    throw new SocialDenied(response.status, said);
  }

  let document: unknown;
  try {
    document = await response.json();
  } catch {
    throw new SocialDenied(0, `${address} answered, but not with JSON`);
  }

  if (!isBag(document) || document["api"] !== "v1" || !("data" in document)) {
    throw new SocialDenied(0, `${address} did not answer with a v1 envelope`);
  }
  if (!Array.isArray(document["data"])) {
    throw new SocialDenied(0, `${address} answered with no row list`);
  }

  return (document["data"] as unknown[]).map(toItem);
}

/* ---- rows ---------------------------------------------------------------------------------- */

function toMember(row: Bag): SocialMember {
  return {
    id: text(row["member_id"]),
    name: text(row["member"]),
    icon: text(row["member_icon"]),
  };
}

function toTrack(row: Bag): SocialTrack {
  return {
    id: count(row["id"]),
    title: text(row["title"]),
    artist: text(row["artist_name"]),
  };
}

function toItem(row: unknown): SocialItem {
  const bag = isBag(row) ? row : {};
  const kind = text(bag["kind"]);
  if (kind === "boost") return toBoost(bag);
  if (kind === "day") return toDay(bag);
  // Named rather than dropped: a silently shorter feed is the failure this message prevents.
  throw new SocialError(`the activity feed named a kind this player does not know: ${kind || "none"}`);
}

function toBoost(row: Bag): BoostItem {
  const track = isBag(row["track"]) ? toTrack(row["track"]) : null;
  return {
    kind: "boost",
    ts: number(row["ts"] ?? row["boosted_at"]),
    expiresAt: number(row["expires_at"]),
    swapped: row["swapped"] === true,
    alreadyActive: row["already_active"] === true,
    track,
    member: text(row["member_id"]) ? toMember(row) : null,
  };
}

function toEntry(group: string, row: unknown): SocialEntry {
  const bag = isBag(row) ? row : {};
  if (group === "listen" || group === "add") {
    const track = toTrack(bag);
    return { kind: group, ts: number(bag["ts"]), title: track.title, detail: "", playlistId: 0, track };
  }
  if (group === "playlist") {
    return {
      kind: text(bag["kind"], "playlist"),
      ts: number(bag["ts"]),
      title: text(bag["title"]),
      detail: "",
      playlistId: count(bag["id"]),
      track: null,
    };
  }
  return {
    kind: text(bag["kind"], group),
    ts: number(bag["ts"]),
    title: "",
    detail: text(bag["detail"]),
    playlistId: 0,
    track: null,
  };
}

function toDay(row: Bag): DayItem {
  const groups: Record<string, SocialEntry[]> = {};
  const rawGroups = isBag(row["groups"]) ? row["groups"] : {};
  for (const [name, value] of Object.entries(rawGroups)) {
    groups[name] = list(value).map((entry) => toEntry(name, entry));
  }

  const counts: Record<string, number> = {};
  const rawCounts = isBag(row["counts"]) ? row["counts"] : {};
  for (const [name, value] of Object.entries(rawCounts)) counts[name] = count(value);

  return {
    kind: "day",
    member: toMember(row),
    day: text(row["day"]),
    ts: number(row["ts"]),
    firstTs: number(row["first_ts"]),
    counts,
    groups,
  };
}