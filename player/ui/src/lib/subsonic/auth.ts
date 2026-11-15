/* Credentials, server addresses, and the query a Subsonic request carries.
 *
 * The three forms a server can accept are recorded, never guessed: a server that accepted a
 * password keeps accepting it, a server that only accepts token auth is never sent `p=`. The
 * caller that got a form from `ping` puts that form on the account; this file only knows how
 * to turn a form and a secret into parameters.
 *
 * Nothing here touches storage or the network, which is what makes it testable.
 */

import { md5Hex } from "./md5";
import { ClientError } from "./errors";

export type AuthForm = "password" | "token" | "device-invite";

export const AUTH_FORMS: Readonly<Record<AuthForm, { params: readonly string[]; label: string }>> = {
  password: { params: ["u", "p"], label: "password" },
  token: { params: ["u", "t", "s"], label: "token + salt" },
  "device-invite": { params: ["u", "p"], label: "device invite" },
};

export const AUTH_FORM_IDS = Object.keys(AUTH_FORMS) as AuthForm[];

export interface Credentials {
  username: string;
  /** The password, the shared secret, or the issued device credential — per `form`. */
  secret: string;
  form: AuthForm;
}

/** The protocol version this client speaks, and the name it reports. Not a preference. */
export const API_VERSION = "1.16.1";
export const CLIENT_NAME = "hardlylistening-player";

function text(value: unknown): string {
  return value == null ? "" : String(value);
}

export type OriginResult = { origin: string } | { error: string };

/**
 * A server address is a scheme, a host and an optional path — the part *before* `/rest`.
 *
 * Trailing slashes are dropped so `…/` and `…` ask the same URL. A missing scheme is refused
 * rather than invented (`http` and `https` are the person's choice), an address carrying
 * `user:pass@` is refused rather than stripped, and a pasted `/rest` is refused with the fix
 * in the message instead of being silently trimmed.
 */
export function normaliseOrigin(raw: unknown): OriginResult {
  const value = text(raw).trim();
  if (!value) return { error: "Enter the server address — for example https://music.example.com" };
  if (/^[^/]*@/.test(value)) {
    return { error: "That address carries a username or password. Remove the part before @" };
  }

  let parsed: URL;
  try {
    parsed = new URL(value);
  } catch {
    return { error: "That address is missing its scheme. Try https://music.example.com" };
  }

  if (parsed.protocol !== "http:" && parsed.protocol !== "https:") {
    return { error: "A server address starts with http:// or https://" };
  }
  if (!parsed.hostname) return { error: "That address has no host name" };
  if (parsed.search || parsed.hash) {
    return { error: "Use the server's address on its own, without a query string or a #fragment" };
  }
  if (parsed.username || parsed.password) {
    return { error: "That address carries a username or password. Remove the part before @" };
  }

  const path = parsed.pathname.replace(/\/+$/, "");
  if (/\/rest(\/ping\.view)?$/.test(path)) {
    return { error: "Drop the /rest part — the address is the server root, the player adds /rest itself" };
  }

  return { origin: parsed.protocol + "//" + parsed.host + path };
}

/** A fresh salt per request: 16 lowercase hex characters from the platform CSPRNG. */
export function newSalt(): string {
  const bytes = new Uint8Array(8);
  crypto.getRandomValues(bytes);
  let out = "";
  for (const byte of bytes) out += byte.toString(16).padStart(2, "0");
  return out;
}

/**
 * The authentication parameters for one request.
 *
 * Token form is `u`, `t = md5(secret + salt)`, `s = salt`. Password and device-invite forms
 * are `u`, `p`. A server that issues a device credential checks it exactly like a password,
 * which is why both use `p=`.
 */
export function authParams(credentials: Credentials, salt: string = newSalt()): Record<string, string> {
  const username = text(credentials.username).trim();
  if (!username) throw new ClientError("This account has no username");
  if (!credentials.secret) throw new ClientError("This account has no stored credential");

  if (credentials.form === "token") {
    return { u: username, t: md5Hex(credentials.secret + salt), s: salt };
  }
  if (credentials.form === "password" || credentials.form === "device-invite") {
    return { u: username, p: credentials.secret };
  }
  throw new ClientError(`Unknown authentication form “${String(credentials.form)}”`);
}

/** The parameters every request carries: who we are, which protocol, and JSON. */
export function protocolParams(apiVersion = API_VERSION, clientName = CLIENT_NAME): Record<string, string> {
  return { v: apiVersion, c: clientName, f: "json" };
}