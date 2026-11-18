/* One server at a time, on purpose.
 *
 * The host app keeps a list of accounts because it is a library manager. A player asks a server
 * for music; a second active server would make every id ambiguous, and the protocol's ids are
 * only unique inside one server. So this store holds one active account and remembers the
 * others as records you can switch to.
 *
 * The record is public — server, username, label, form — and the secret is the vault's. That
 * split is the existing accounts model, kept.
 */

import {
  NativeTransport,
  SubsonicClient,
  SubsonicError,
  WebTransport,
  describeError,
  nativeInvoke,
  normaliseOrigin,
  type AuthForm,
  type Credentials,
  type FetchLike,
  type InvokeLike,
  type ServerInfo,
  type SubsonicTransport,
} from "../subsonic";
import { openVault, type Vault, type VaultMode } from "../accounts/vault";
import { probeSocial, type SocialAvailability } from "../hardly/social";
import { nativePublicFetch } from "../hardly/native";
import type { PlayerStore } from "../player/store.svelte";

export interface AccountRecord {
  id: string;
  server: string;
  username: string;
  label: string;
  form: AuthForm;
  added: string;
}

interface AccountDocument {
  version: 1;
  activeId: string | null;
  accounts: AccountRecord[];
}

const STORAGE_KEY = "hl.player.accounts";
const RECORD_FIELDS = ["id", "server", "username", "label", "form", "added"] as const;

function isRecord(value: unknown): value is Record<string, unknown> {
  return typeof value === "object" && value !== null;
}

/** Read a stored record through an allow-list: a field nobody expects is dropped by name. */
function readRecord(raw: unknown): AccountRecord | null {
  if (!isRecord(raw)) return null;
  const out: Record<string, string> = {};
  for (const field of RECORD_FIELDS) {
    const value = raw[field];
    if (typeof value === "string" && value) out[field] = value;
  }
  if (!out["id"] || !out["server"] || !out["username"]) return null;
  const form = out["form"];
  return {
    id: out["id"]!,
    server: out["server"]!,
    username: out["username"]!,
    label: out["label"] ?? out["server"]!,
    form: form === "token" || form === "device-invite" || form === "password" ? form : "password",
    added: out["added"] ?? new Date().toISOString(),
  };
}

export type SessionStatus = "idle" | "connecting" | "ready" | "error";

/**
 * What the session is allowed to reach the outside world with. Production passes nothing and
 * gets the real vault, the real `invoke` when the shell has one, and the platform `fetch`; the
 * tests pass substitutes so the conversation can be read without a socket or a keychain.
 */
export interface SessionOptions {
  vault?: Vault;
  /** `null` means "this build has no native side"; omitted means "ask the shell". */
  invoke?: InvokeLike | null;
  fetch?: FetchLike;
  /**
   * The browsing side of the app. It is here, not in `App.svelte`, because it has to be bound at
   * exactly the moment a client exists and let go at exactly the moment one stops existing.
   *
   * It was missing from both places until the first build was run against a real server: the
   * player was attached and the library was not, so connecting worked, the shell appeared, and
   * every wall stayed empty — with no error to explain it, because `Library.loadAlbums` returns
   * early when it has no server.
   */
  library?: LibrarySink;
}

/** The slice of `Library` this file needs, so the store stays out of the session's way. */
export interface LibrarySink {
  attach(server: SubsonicClient): void;
  detach(): void;
}

export class Session {
  records = $state<AccountRecord[]>([]);
  activeId = $state<string | null>(null);
  server = $state<ServerInfo | null>(null);
  status = $state<SessionStatus>("idle");
  error = $state<string | null>(null);
  vaultMode = $state<VaultMode>("memory");

  #client: SubsonicClient | null = null;
  #vault: Vault;
  #player: PlayerStore;
  #invoke: InvokeLike | null;
  #fetch: FetchLike | undefined;
  #library: LibrarySink | null;

  constructor(player: PlayerStore, options: SessionOptions = {}) {
    this.#player = player;
    this.#vault = options.vault ?? openVault();
    this.#invoke = options.invoke === undefined ? nativeInvoke() : options.invoke;
    this.#fetch = options.fetch;
    this.#library = options.library ?? null;
    this.vaultMode = this.#vault.mode;
    this.#read();
    void this.#measureVault();
  }

  /** One client, every subscriber: the player and the browsing store are bound together or not
   * at all, so a signed-in window can never be half-wired. */
  #attach(client: SubsonicClient): void {
    this.#player.attach(client);
    this.#library?.attach(client);
  }

  /** True when requests go through Rust and the page never sees the server's address. */
  get native(): boolean {
    return this.#invoke !== null;
  }

  get client(): SubsonicClient | null {
    return this.#client;
  }

  get active(): AccountRecord | null {
    return this.records.find((record) => record.id === this.activeId) ?? null;
  }

  /* ---- connecting ----------------------------------------------------------------------- */

  async connect(input: { server: string; username: string; password: string }): Promise<boolean> {
    const target = normaliseOrigin(input.server);
    if ("error" in target) {
      this.status = "error";
      this.error = target.error;
      return false;
    }

    const username = input.username.trim();
    if (!username) {
      this.status = "error";
      this.error = "Enter the username the server gave you";
      return false;
    }
    if (!input.password) {
      this.status = "error";
      this.error = "Enter the password for that username";
      return false;
    }

    this.status = "connecting";
    this.error = null;

    // The account id exists before the first request, because the secret is filed under it and
    // the native side reads it from there. Nothing is remembered until the server answers.
    const existing = this.records.find(
      (record) => record.server === target.origin && record.username === username,
    );
    const id = existing?.id ?? crypto.randomUUID();
    // `put` answers with the store it actually used, which is the measured answer: on a machine
    // with no Secret Service the entry lives in this process and the screen has to say so.
    this.vaultMode = await this.#vault.put(id, input.password);

    const native = this.#nativeFor(id, target.origin, username);

    let lastError: unknown = null;
    for (const form of ["token", "password"] as const) {
      let transport: SubsonicTransport | null = null;
      try {
        transport = await this.#transportFor(native, id, target.origin, username, form, input.password);
      } catch (error) {
        // A native refusal (no stored secret, a keychain that would not open) is a failed
        // sign-in like any other, not an exception for the caller to handle.
        lastError = error;
        break;
      }
      if (!transport) break;
      const client = new SubsonicClient({ transport });
      try {
        const info = await client.ping();
        this.#client = client;
        this.server = info;
        this.#remember(existing, id, target.origin, username, form);
        this.#attach(client);
        this.status = "ready";
        return true;
      } catch (error) {
        lastError = error;
        // Only "the server will not take this form" is worth a second attempt. A wrong password
        // (40) on token form is a wrong password on password form too.
        if (error instanceof SubsonicError && (error.code === 41 || error.code === 42)) continue;
        break;
      }
    }

    // A new account that never connected leaves no secret behind. An account that was already
    // saved keeps whatever was just typed: a mistyped retry must not erase a working password,
    // and the person can sign in again.
    if (!existing) await this.#vault.forget(id);

    this.status = "error";
    this.error = describeError(lastError);
    return false;
  }

  /** Sign in again with a saved account. */
  async activate(id: string): Promise<boolean> {
    const record = this.records.find((entry) => entry.id === id);
    if (!record) return false;

    this.status = "connecting";
    this.error = null;

    const native = this.#nativeFor(id, record.server, record.username);
    let transport: SubsonicTransport | null = null;
    try {
      transport = await this.#transportFor(native, id, record.server, record.username, record.form, null);
    } catch (error) {
      this.status = "error";
      this.error = describeError(error);
      return false;
    }
    if (!transport) {
      this.status = "error";
      this.error = "That account's password is gone. Enter it again to reconnect";
      return false;
    }

    const client = new SubsonicClient({ transport });
    try {
      this.server = await client.ping();
      this.#client = client;
      this.activeId = id;
      this.#attach(client);
      this.#write();
      this.status = "ready";
      return true;
    } catch (error) {
      this.status = "error";
      this.error = describeError(error);
      return false;
    }
  }

  /** Reload the page's saved account, if there is one. Called once at boot. */
  async restore(): Promise<void> {
    if (!this.activeId) return;
    await this.activate(this.activeId);
  }

  /**
   * Whether the connected server has an activity feed.
   *
   * This is P6's seam, not a surface: nothing draws the answer yet, and no probe runs at connect.
   * Social belongs to this server, not to the Subsonic protocol, so the question is asked of the
   * server's capability document rather than assumed from the app being this player. `null` means
   * there is no connected account to ask about. Never throws — `absent` carries the reason.
   *
   * The read itself goes through Rust when this build has it: the window's policy (`connect-src`
   * is the asset origin and IPC) refuses a page `fetch` to any host, and loosening that to make a
   * public document readable would be the wrong trade. `nativePublicFetch` names the document and
   * lets the bound account supply the address. A build without a native side keeps the page path.
   */
  async social(): Promise<SocialAvailability | null> {
    const record = this.active;
    if (!record) return null;
    const read = this.#invoke
      ? nativePublicFetch({ invoke: this.#invoke, accountId: record.id, origin: record.server })
      : this.#fetch;
    return probeSocial(record.server, read ? { fetch: read } : {});
  }

  async forget(id: string): Promise<void> {
    await this.#vault.forget(id);
    await this.#unbind(id);
    this.records = this.records.filter((record) => record.id !== id);
    if (this.activeId === id) {
      this.#client = null;
      this.server = null;
      this.status = "idle";
    }
    this.#write();
  }

  async signOut(): Promise<void> {
    if (this.activeId) await this.#unbind(this.activeId);
    this.#client = null;
    // The walls belong to the account that was just signed out; leaving them up would show one
    // person's library to the next. The queue and the last playback failure belong to it too: a
    // song id is only a name on the server that issued it.
    this.#library?.detach();
    this.#player.detach();
    this.server = null;
    this.activeId = null;
    this.status = "idle";
    this.error = null;
    this.#write();
  }

  /** Ask the shell what its secret store really is, once, at startup. */
  async #measureVault(): Promise<void> {
    if (!this.#vault.probe) return;
    try {
      this.vaultMode = await this.#vault.probe();
    } catch {
      /* A store that cannot answer is the one `mode` already described. */
    }
  }

  /* ---- transports ------------------------------------------------------------------------ */

  /**
   * The native transport for one account, or `null` when this build has no native side.
   *
   * It is built from the *public* record. `secret` never goes near it: Rust reads the secret from
   * the vault by account id, which is what lets the branch below exist at all.
   */
  #nativeFor(accountId: string, origin: string, username: string): NativeTransport | null {
    if (!this.#invoke) return null;
    return new NativeTransport({ accountId, origin, username, invoke: this.#invoke });
  }

  /**
   * The transport for one attempt: native when it binds, `fetch` when it cannot.
   *
   * A shell that has never heard of `subsonic_bind` is an older build, not a broken server, so
   * the session goes back to the web path instead of leaving a person unable to connect. The
   * secret is only read here on that fallback — the native path never asks for it.
   */
  async #transportFor(
    native: NativeTransport | null,
    accountId: string,
    origin: string,
    username: string,
    form: AuthForm,
    secret: string | null,
  ): Promise<SubsonicTransport | null> {
    if (native) {
      try {
        await native.bind(form);
        return native;
      } catch (error) {
        if (!this.#builtWithoutNative(error)) throw error;
        this.#invoke = null;
      }
    }

    const usable = secret ?? (await this.#vault.get(accountId));
    if (usable) return this.#webTransport(origin, username, form, usable);
    return null;
  }

  #webTransport(origin: string, username: string, form: AuthForm, secret: string): SubsonicTransport {
    const credentials: Credentials = { username, secret, form };
    return new WebTransport({ origin, credentials, fetch: this.#fetch });
  }

  /**
   * Whether the native side is simply absent from this build.
   *
   * Two shapes mean that: Tauri's own "command not found", and the `ClientError` the transport
   * raises when it recognises one. The second is this project's own wording, not a server's, so
   * matching it is a check on our own branch and not a guess about anyone else's text.
   */
  #builtWithoutNative(error: unknown): boolean {
    const said = error instanceof Error ? error.message : String(error);
    return /not found|unknown command|no native networking/i.test(said);
  }

  async #unbind(accountId: string): Promise<void> {
    if (!this.#invoke) return;
    try {
      await this.#invoke("subsonic_unbind", { accountId });
    } catch {
      /* An older build has nothing to forget, and a sign-out must not fail on that. */
    }
  }

  /* ---- the saved document ---------------------------------------------------------------- */

  #remember(
    existing: AccountRecord | undefined,
    id: string,
    origin: string,
    username: string,
    form: AuthForm,
  ): void {
    const record: AccountRecord = {
      id,
      server: origin,
      username,
      label: origin.replace(/^https?:\/\//, ""),
      form,
      added: existing?.added ?? new Date().toISOString(),
    };
    this.records = [...this.records.filter((entry) => entry.id !== id), record];
    this.activeId = id;
    this.#write();
  }

  #read(): void {
    let parsed: unknown;
    try {
      parsed = JSON.parse(localStorage.getItem(STORAGE_KEY) ?? "null");
    } catch {
      return;
    }
    if (!isRecord(parsed)) return;
    const accounts = Array.isArray(parsed["accounts"]) ? parsed["accounts"] : [];
    this.records = accounts
      .map(readRecord)
      .filter((record): record is AccountRecord => record !== null);
    const activeId = parsed["activeId"];
    this.activeId = typeof activeId === "string" ? activeId : null;
  }

  #write(): void {
    const document: AccountDocument = {
      version: 1,
      activeId: this.activeId,
      accounts: this.records,
    };
    try {
      localStorage.setItem(STORAGE_KEY, JSON.stringify(document));
    } catch {
      /* Storage is the browser's to refuse; the session continues without persistence. */
    }
  }
}