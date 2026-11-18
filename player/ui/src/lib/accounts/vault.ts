/* The secret store, as the player uses it.
 *
 * A public account record (server, username, label, form) lives in `localStorage`; the secret
 * that goes with it never does. On a desktop shell the secret goes to the OS keychain through
 * the `accounts_*` commands in `player/src-tauri/src/accounts.rs`; a machine with no keyring
 * keeps it in memory for the session and says so.
 *
 * Since P2 the player does not read the secret back on a shell that has native networking: Rust
 * reads it from the keychain itself, by account id, when it builds a request. `get` survives for
 * the `fetch` fallback a browser or an older build needs, and that fallback is the only caller.
 * Android still has no Keystore backend — an Android build lands on the in-memory vault — and
 * that is P3's, where a device build can verify it.
 */

export type VaultMode = "keychain" | "memory";

export interface Vault {
  readonly mode: VaultMode;
  /** What the shell's store actually is, asked rather than assumed. Optional: a vault that
   * cannot be probed (a fake in a test, a browser) keeps `mode`'s answer. The native vault
   * reports `keychain` in `mode` before anything is stored, and only the Rust probe knows
   * whether a Secret Service is really there — which is the difference between "your password
   * is in the keychain" and "it is gone when you close this window". */
  probe?: () => Promise<VaultMode>;
  put(accountId: string, secret: string): Promise<VaultMode>;
  get(accountId: string): Promise<string | null>;
  forget(accountId: string): Promise<void>;
}

type InvokeLike = (command: string, args?: Record<string, unknown>) => Promise<unknown>;

function tauriInvoke(): InvokeLike | null {
  const candidate = (globalThis as { __TAURI__?: { core?: { invoke?: InvokeLike } } }).__TAURI__;
  const invoke = candidate?.core?.invoke;
  return typeof invoke === "function" ? invoke : null;
}

/** A keychain-backed vault when the shell has one; an in-memory vault that reports itself. */
export function openVault(): Vault {
  const invoke = tauriInvoke();
  if (!invoke) return memoryVault();

  return {
    get mode(): VaultMode {
      return "keychain";
    },
    async probe() {
      try {
        const mode = await invoke("accounts_mode");
        return mode === "keychain" ? "keychain" : "memory";
      } catch {
        return "memory";
      }
    },
    async put(accountId, secret) {
      const mode = await invoke("accounts_put", { account: accountId, secret });
      return mode === "keychain" ? "keychain" : "memory";
    },
    async get(accountId) {
      const value = await invoke("accounts_secret", { account: accountId });
      return typeof value === "string" && value ? value : null;
    },
    async forget(accountId) {
      await invoke("accounts_forget", { account: accountId });
    },
  };
}

/** Nothing is written anywhere, and it says so rather than pretending. */
export function memoryVault(): Vault {
  const cells = new Map<string, string>();
  return {
    mode: "memory",
    async probe() {
      return "memory";
    },
    async put(accountId, secret) {
      cells.set(accountId, secret);
      return "memory";
    },
    async get(accountId) {
      return cells.get(accountId) ?? null;
    },
    async forget(accountId) {
      cells.delete(accountId);
    },
  };
}