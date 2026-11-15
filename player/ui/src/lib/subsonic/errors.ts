/* What a server says when it refuses, in the words a person can act on.
 *
 * Subsonic error codes are part of the protocol, not a detail of one server: 40 is wrong
 * credentials everywhere, 50 is "this account may not use the API" everywhere. Mapping them
 * here means the connect screen, the library and the player all say the same thing about the
 * same refusal, and a caller can branch on `code` without matching on English.
 */

export const SUBSONIC_ERRORS: Readonly<Record<number, string>> = {
  0: "The server did not say what went wrong",
  10: "The server refused a required parameter",
  20: "This client speaks a protocol version the server will not use",
  30: "The server speaks a Subsonic version this player does not understand",
  40: "Wrong username or password",
  41: "This server does not accept token authentication",
  42: "The server rejected the token and salt",
  43: "The server rejected the API key",
  44: "The server rejected the session token",
  50: "That account is not allowed to use the API",
  60: "The server cannot accept that request",
  70: "The server could not find what the request asked for",
};

/** A refusal that came from the server, not from the network. `code` is the Subsonic code. */
export class SubsonicError extends Error {
  readonly code: number;
  readonly endpoint: string;

  constructor(code: number, message: string, endpoint = "") {
    super(message);
    this.name = "SubsonicError";
    this.code = code;
    this.endpoint = endpoint;
  }

  /** Codes that mean "the credential is wrong", the only ones worth returning to the sign-in. */
  get isAuthFailure(): boolean {
    return this.code === 40 || this.code === 41 || this.code === 42 || this.code === 43 || this.code === 44;
  }

  /** Codes that mean "the server will not speak to this client at all". */
  get isProtocolFailure(): boolean {
    return this.code === 20 || this.code === 30;
  }
}

/** The server answered, but not as a Subsonic server (an HTML page, a proxy error, garbage). */
export class NotSubsonicError extends Error {
  constructor(message: string) {
    super(message);
    this.name = "NotSubsonicError";
  }
}

/** The request never reached the server: DNS, TLS, a refused connection, a dead proxy. */
export class TransportError extends Error {
  readonly cause?: unknown;

  constructor(message: string, cause?: unknown) {
    super(message);
    this.name = "TransportError";
    this.cause = cause;
  }
}

/** A refusal this client makes on its own: a malformed address, a missing credential. */
export class ClientError extends Error {
  constructor(message: string) {
    super(message);
    this.name = "ClientError";
  }
}

/** Plain-language text for any error this client can raise. */
export function describeError(error: unknown): string {
  if (error instanceof SubsonicError) return error.message;
  if (error instanceof NotSubsonicError) return error.message;
  if (error instanceof TransportError) return error.message;
  if (error instanceof ClientError) return error.message;
  if (error instanceof Error) return error.message || "The request failed";
  return "The request failed";
}