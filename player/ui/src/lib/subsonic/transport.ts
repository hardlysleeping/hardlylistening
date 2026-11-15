/* How a request reaches the server.
 *
 * The client speaks one interface; what sits behind it differs by platform, and that is the
 * whole point of the split:
 *
 * - `WebTransport` uses `fetch` from inside the WebView. It works in a browser and on a desktop
 *   shell whose server permits CORS, and it is the only transport that can hand an `<audio>`
 *   element a URL, because a media element cannot carry headers.
 * - `NativeTransport` is the mobile/desktop path: it passes `accountId`, an endpoint
 *   and parameters to the Rust side, which resolves the origin and the credential from the saved
 *   account. No URL comes from JavaScript, so the WebView never gains a general-purpose proxy.
 *
 * The rule both share: **the caller names an endpoint, never a URL.**
 */

import { ClientError, NotSubsonicError, SubsonicError, TransportError } from "./errors";
import { authParams, newSalt, protocolParams, type AuthForm, type Credentials } from "./auth";
import type { PlayableSource } from "./types";

export interface SubsonicRequest {
  /** The Subsonic endpoint without its `.view` suffix, e.g. `getAlbumList2`. */
  endpoint: string;
  /** Parameters for this endpoint only. Protocol and authentication are added by the transport. */
  params?: Record<string, string | number | undefined>;
}

export interface StreamOptions {
  /** Ask the server to transcode down to this rate. Omitted means "send the original". */
  maxBitRate?: number;
  /** A target container, e.g. `mp3`. Omitted means the server decides. */
  format?: string;
}

export interface SubsonicTransport {
  readonly kind: "web" | "native";
  /** The server this transport is bound to. `null` when the native side holds it. */
  readonly origin: string | null;
  /** One request, resolved to the parsed JSON body. Errors are the classes in `errors.ts`. */
  request(request: SubsonicRequest): Promise<unknown>;
  /** A URL an audio engine can play for one song. */
  source(songId: string, options?: StreamOptions): Promise<PlayableSource>;
  /** A URL for cover art, or `null` when the song has no artwork. */
  cover(coverArtId: string | undefined, size?: number): Promise<string | null>;
}

function text(value: unknown): string {
  return value == null ? "" : String(value);
}

function reasonOf(error: unknown): string {
  const said = error instanceof Error ? text(error.message).trim() : "";
  return said || "the request failed before an answer arrived";
}

function cleanParams(params: Record<string, string | number | undefined>): Record<string, string> {
  const out: Record<string, string> = {};
  for (const [key, value] of Object.entries(params)) {
    if (value === undefined) continue;
    out[key] = String(value);
  }
  return out;
}

/** `fetch`, as this module uses it — injectable so the tests never open a socket. */
export type FetchLike = (input: string, init?: RequestInit) => Promise<Response>;

export interface WebTransportOptions {
  origin: string;
  credentials: Credentials;
  /** Defaults to the platform `fetch`. */
  fetch?: FetchLike;
}

export class WebTransport implements SubsonicTransport {
  readonly kind = "web" as const;
  readonly origin: string;

  #credentials: Credentials;
  #fetch: FetchLike;

  constructor(options: WebTransportOptions) {
    if (!options.origin) throw new ClientError("A web transport needs the server's address");
    this.origin = options.origin;
    this.#credentials = options.credentials;
    this.#fetch = options.fetch ?? ((input, init) => fetch(input, init));
  }

  /** The credential this transport is using. Replaced when the account signs in again. */
  setCredentials(credentials: Credentials): void {
    this.#credentials = credentials;
  }

  /**
   * The full URL for one endpoint.
   *
   * Credentials travel in the query string because that is what the protocol specifies — a
   * Subsonic server reads `u`/`t`/`s` there. The consequence is real and worth stating: the
   * URL, and therefore the credential, is visible to anything that logs request URLs. That is
   * one of the reasons the native transport exists; on the web path the server is the person's
   * own and the connection is theirs to secure.
   */
  url(endpoint: string, params: Record<string, string | number | undefined> = {}): string {
    const query = new URLSearchParams({
      ...authParams(this.#credentials, newSalt()),
      ...protocolParams(),
      ...cleanParams(params),
    });
    return `${this.origin}/rest/${encodeURIComponent(endpoint)}.view?${query.toString()}`;
  }

  async request(request: SubsonicRequest): Promise<unknown> {
    const url = this.url(request.endpoint, request.params ?? {});

    let response: Response;
    try {
      response = await this.#fetch(url, {
        method: "GET",
        headers: { Accept: "application/json" },
        redirect: "follow",
        // No cookies: an account's identity is its credential, not a browser session.
        credentials: "omit",
      });
    } catch (error) {
      throw new TransportError(`Could not reach ${this.origin} — ${reasonOf(error)}`, error);
    }

    if (response.status === 401 || response.status === 403) {
      throw new SubsonicError(40, "Wrong username or password", request.endpoint);
    }
    if (!response.ok) {
      throw new TransportError(`${this.origin} answered HTTP ${response.status}`, undefined);
    }

    try {
      return await response.json();
    } catch {
      throw new NotSubsonicError(
        `${this.origin} answered, but not with JSON — that address is probably a web page rather than a Subsonic server`,
      );
    }
  }

  async source(songId: string, options: StreamOptions = {}): Promise<PlayableSource> {
    const url = this.url("stream", {
      id: songId,
      maxBitRate: options.maxBitRate,
      format: options.format,
    });
    return { songId, url, transcoded: options.maxBitRate !== undefined || options.format !== undefined };
  }

  async cover(coverArtId: string | undefined, size = 300): Promise<string | null> {
    if (!coverArtId) return null;
    return this.url("getCoverArt", { id: coverArtId, size });
  }
}

/* --------------------------------------------------------------------------------------------
 * The native transport.
 *
 * The rule is the shape of the message, not the fact that Rust makes the request: the page names
 * an **account, an endpoint and that endpoint's parameters**. It never names an origin, never
 * builds a URL, and never sees the secret again once it has been put in the vault. Rust owns the
 * address because it owns the credential, and a page that cannot state an address cannot point
 * the credential at a server of its choosing.
 *
 * That makes three of the checks below security boundaries rather than conveniences:
 *
 * - the endpoint must be a bare name (`getAlbumList2`), so it cannot carry a path, a `..`, a
 *   second host or its own query string;
 * - the caller may not set `u`/`p`/`t`/`s`/`v`/`c`/`f`, because those are the protocol's own and
 *   a caller that could set them could choose whose credential is used;
 * - a refusal from Rust arrives as a typed value (`kind`), never as a string to be searched for
 *   words, so an HTTP 401 is still the protocol's code 40 and not just prose.
 *
 * Rust repeats every one of these checks. This file is the polite half; `subsonic_url.rs` is the
 * half that holds if the page is hostile.
 * ------------------------------------------------------------------------------------------ */

/** The subset of Tauri's `invoke` this module needs. */
export type InvokeLike = (command: string, args?: Record<string, unknown>) => Promise<unknown>;

export const NATIVE_BIND_COMMAND = "subsonic_bind";
export const NATIVE_REQUEST_COMMAND = "subsonic_request";
export const NATIVE_SOURCE_COMMAND = "subsonic_source";
export const NATIVE_COVER_COMMAND = "subsonic_cover";

/** An endpoint is a Subsonic view name and nothing else. */
const ENDPOINT = /^[A-Za-z][A-Za-z0-9]{0,63}$/;

/** The protocol's own parameters. A caller that sets one is choosing a credential or a version. */
const RESERVED = new Set(["u", "p", "t", "s", "v", "c", "f"]);

function assertEndpoint(endpoint: string): void {
  if (!ENDPOINT.test(endpoint)) {
    throw new ClientError(`"${endpoint}" is not a Subsonic endpoint name`);
  }
}

/** Parameters, checked and stringified. Undefined is dropped, as the web path drops it. */
function assertedParams(params: Record<string, string | number | undefined>): Record<string, string> {
  const out: Record<string, string> = {};
  for (const [key, value] of Object.entries(params)) {
    if (value === undefined) continue;
    if (RESERVED.has(key)) {
      throw new ClientError(`The player sets "${key}" itself; a caller may not override it`);
    }
    if (!ENDPOINT.test(key)) {
      throw new ClientError(`"${key}" is not a Subsonic parameter name`);
    }
    if (typeof value === "number" && !Number.isFinite(value)) {
      throw new ClientError(`"${key}" is not a usable number`);
    }
    out[key] = String(value);
  }
  return out;
}

/** What Rust sends instead of a string when it refuses. */
interface NativeFailure {
  kind?: unknown;
  status?: unknown;
  message?: unknown;
}

function isFailure(value: unknown): value is NativeFailure {
  return typeof value === "object" && value !== null && "kind" in value;
}

/**
 * Turn a refusal from the native side into the error the rest of the app already understands.
 *
 * The kinds are Rust's, and each maps onto the class the same situation raises on the web path —
 * so the connect screen does not need to know which transport answered.
 */
function nativeError(error: unknown, endpoint: string): Error {
  if (error instanceof Error && /not found|unknown command/i.test(error.message)) {
    return new ClientError("This build has no native networking yet. The web transport is the current path.");
  }
  if (!isFailure(error)) {
    return new TransportError(reasonOf(error), error);
  }
  const message = text(error.message).trim();
  const status = typeof error.status === "number" ? error.status : null;
  switch (error.kind) {
    case "http":
      // 401 and 403 are the protocol's code 40 on the web path; keep them identical here.
      if (status === 401 || status === 403) return new SubsonicError(40, "Wrong username or password", endpoint);
      return new TransportError(message || `The server answered HTTP ${status ?? "?"}`);
    case "json":
      return new NotSubsonicError(message || "That server answered, but not with a Subsonic document");
    case "config":
      return new ClientError(message || "The native side cannot make that request");
    default:
      return new TransportError(message || reasonOf(error), error);
  }
}

export interface NativeTransportOptions {
  /** The account the vault filed the secret under. Rust reads the secret by this id. */
  accountId: string;
  /** Public record fields, sent once by `bind()` so Rust can pin the origin. */
  origin: string;
  username: string;
  /** The form the server accepted. Rust reproduces it; the page never sends the secret. */
  form?: AuthForm;
  invoke: InvokeLike;
}

export class NativeTransport implements SubsonicTransport {
  readonly kind = "native" as const;
  /** The address lives on the native side, which is what makes it impossible to redirect. */
  readonly origin = null;

  #accountId: string;
  #origin: string;
  #username: string;
  #form: AuthForm;
  #invoke: InvokeLike;

  constructor(options: NativeTransportOptions) {
    if (!options.accountId) throw new ClientError("A native transport needs an account");
    if (!options.origin) throw new ClientError("A native transport needs the server's address");
    if (!options.username) throw new ClientError("A native transport needs the username");
    this.#accountId = options.accountId;
    this.#origin = options.origin;
    this.#username = options.username;
    this.#form = options.form ?? "token";
    this.#invoke = options.invoke;
  }

  /**
   * Hand Rust the public record so it can resolve the secret the vault holds.
   *
   * Called once per connection, and again with the other form when a server refuses token
   * authentication — which is the only reason the form is ever sent.
   */
  async bind(form: AuthForm = this.#form): Promise<void> {
    try {
      await this.#invoke(NATIVE_BIND_COMMAND, {
        accountId: this.#accountId,
        origin: this.#origin,
        username: this.#username,
        form,
      });
      this.#form = form;
    } catch (error) {
      throw nativeError(error, "ping");
    }
  }

  async request(request: SubsonicRequest): Promise<unknown> {
    assertEndpoint(request.endpoint);
    const params = assertedParams(request.params ?? {});
    try {
      return await this.#invoke(NATIVE_REQUEST_COMMAND, {
        accountId: this.#accountId,
        endpoint: request.endpoint,
        params,
      });
    } catch (error) {
      throw nativeError(error, request.endpoint);
    }
  }

  async source(songId: string, options: StreamOptions = {}): Promise<PlayableSource> {
    const result = await this.#invoke(NATIVE_SOURCE_COMMAND, {
      accountId: this.#accountId,
      songId,
      maxBitRate: options.maxBitRate ?? null,
      format: options.format ?? null,
    });
    const bag = (result ?? {}) as { url?: unknown; contentType?: unknown; transcoded?: unknown };
    const url = text(bag.url);
    if (!url) throw new ClientError("The native side did not return a stream address");
    return {
      songId,
      url,
      contentType: text(bag.contentType) || undefined,
      transcoded: bag.transcoded === true,
    };
  }

  async cover(coverArtId: string | undefined, size = 300): Promise<string | null> {
    if (!coverArtId) return null;
    const result = await this.#invoke(NATIVE_COVER_COMMAND, {
      accountId: this.#accountId,
      coverArtId,
      size,
    });
    return text(result) || null;
  }
}

/** True when the page is running inside the Tauri shell, where `invoke` exists. */
export function nativeInvoke(): InvokeLike | null {
  const candidate = (globalThis as { __TAURI__?: { core?: { invoke?: InvokeLike } } }).__TAURI__;
  const invoke = candidate?.core?.invoke;
  return typeof invoke === "function" ? invoke : null;
}