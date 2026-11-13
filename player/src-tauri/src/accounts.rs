//! The account secrets: the operating system's keychain is the store, and a machine without one
//! still runs.
//!
//! Why the list and the secret are kept apart. *Which* servers an account list holds — their
//! addresses, usernames, and which form of authentication each one accepted — is public data.
//! It says nothing a person's own window does not already show, it is what the shell needs at
//! startup before any keychain is unlocked, and writing it to a config file is how the list
//! survives a crash. A password is not public data, and a config file is a file: it is copied
//! into backups, it is readable by every process running as the same user, and it is printed by
//! accident. So the front end (`shell/accounts.js`) owns the list and may persist it; this
//! module owns exactly one thing, a secret, keyed by the account id the front end derived, and
//! it hands that secret to `keyring` — the Secret Service on Linux, the Keychain on macOS and
//! iOS, the Credential Manager on Windows — rather than to a path.
//!
//! `keyring` is not always there. A headless Linux session with no `gnome-keyring` or
//! `kwalletd` has no Secret Service to talk to, and a player that refuses to start without one
//! is a player that does not run on a server, in a container, or over a plain SSH session. So
//! `Vault::open` *probes* the keychain with a write, a read and a delete of a throwaway entry,
//! and a probe that fails becomes `Mode::Memory`: the same commands answer, the secrets live in
//! this process, and `accounts_mode` says `"memory"` so the front end can tell a person the
//! password is forgotten when the window closes instead of letting them find out at the next
//! launch. Nothing in the memory backend touches the filesystem, and nothing in this module
//! does either: the one artefact this card must never produce is a plaintext password in a
//! file, and a file is the easy mistake.
//!
//! Not here, deliberately: the account list, the active server, migration from the one-session
//! shape PLAYER-001 had, and the form an account records. Those are decisions with no OS in
//! them, they are testable without Rust, and they live in `shell/accounts.js` where the tests
//! can run them. This module is a store with three verbs and a mode.
//!
//! Unverified, plainly: none of this has been compiled. This host has no `webkit2gtk-4.1`,
//! `gtk+-3.0` or `libsoup-3.0` development packages and no `cargo-tauri`, so `keyring` was
//! never resolved and no keychain was ever opened. `rustfmt --check` proves the file parses and
//! nothing more; see `docs/player/accounts.md` for what a build on a real machine has to settle.

use std::collections::HashMap;
use std::sync::Mutex;

use keyring::Entry;

/// The service name every entry is filed under. It is the bundle identifier, so the entries are
/// findable in `seahorse`/`Keychain Access`/`Credential Manager` by the same name the app has.
pub const SERVICE: &str = "ing.hardlysleep.listening.player";

/// The account name used only by the startup probe. It is written, read back and deleted before
/// any real secret exists, so `open()` reports the backend it will actually use rather than the
/// one it hopes for.
const PROBE: &str = "__player-002-probe__";

/// What a secret is actually stored in. Reported to the front end verbatim, because "memory"
/// is a promise the person has to be told about: closing the window forgets the password.
#[derive(Clone, Copy, PartialEq, Eq, Debug)]
pub enum Mode {
    Keychain,
    Memory,
}

impl Mode {
    pub fn as_str(self) -> &'static str {
        match self {
            Mode::Keychain => "keychain",
            Mode::Memory => "memory",
        }
    }
}

/// The store the Tauri builder manages. Cheap to construct, safe to share, and the only place
/// in the player where a secret is written down.
pub struct Vault {
    mode: Mode,
    /// Used only in `Mode::Memory`. A `HashMap` in this process is the honest fallback: it is
    /// not persistence, and the mode says so.
    memory: Mutex<HashMap<String, String>>,
}

impl Vault {
    /// Probe the OS keychain and settle on a backend. The probe is a real round trip because
    /// the failure that matters — a session with no Secret Service, or a keyring that will not
    /// accept a write — does not show up until something is stored.
    pub fn open() -> Vault {
        match probe() {
            Ok(()) => Vault {
                mode: Mode::Keychain,
                memory: Mutex::new(HashMap::new()),
            },
            Err(reason) => {
                // The reason is a keyring diagnostic; it never contains a secret, because the
                // probe's secret is a constant that is not one.
                eprintln!(
                    "accounts: no OS keychain ({reason}); secrets stay in memory for this run"
                );
                Vault {
                    mode: Mode::Memory,
                    memory: Mutex::new(HashMap::new()),
                }
            }
        }
    }

    pub fn mode(&self) -> Mode {
        self.mode
    }

    /// Store one secret. The `Mode` it returns is the backend that actually holds it, so the
    /// caller never has to guess whether the next launch will ask again.
    pub fn put(&self, account: &str, secret: &str) -> Result<Mode, String> {
        match self.mode {
            Mode::Keychain => {
                entry(account)?
                    .set_password(secret)
                    .map_err(|error| format!("the keychain refused the secret: {error}"))?;
                Ok(Mode::Keychain)
            }
            Mode::Memory => {
                self.memory
                    .lock()
                    .map_err(|_| "the in-memory secret store is unusable".to_string())?
                    .insert(account.to_string(), secret.to_string());
                Ok(Mode::Memory)
            }
        }
    }

    /// Read one secret, or `None` when there is nothing to send. A keychain that answers with an
    /// error is treated as "nothing stored": the stderr line says which error it was, and the
    /// connect screen's own 401 already tells a person their credentials were refused. The
    /// alternative — failing the whole window because a keyring is locked — is worse than asking
    /// again.
    pub fn get(&self, account: &str) -> Option<String> {
        match self.mode {
            Mode::Keychain => match entry(account).and_then(|entry| {
                entry
                    .get_password()
                    .map_err(|error| format!("the keychain could not answer: {error}"))
            }) {
                Ok(secret) => Some(secret),
                Err(error) => {
                    eprintln!("accounts: no secret for {account}: {error}");
                    None
                }
            },
            Mode::Memory => self
                .memory
                .lock()
                .ok()
                .and_then(|map| map.get(account).cloned()),
        }
    }

    /// Forget one secret. An entry that is already gone is the state the caller asked for, so
    /// `NoEntry` is a success here and not an error the front end has to interpret.
    pub fn forget(&self, account: &str) -> Result<(), String> {
        match self.mode {
            Mode::Keychain => {
                let entry = entry(account)?;
                match entry.delete_credential() {
                    Ok(()) => Ok(()),
                    Err(keyring::Error::NoEntry) => Ok(()),
                    Err(error) => Err(format!("the keychain kept the secret: {error}")),
                }
            }
            Mode::Memory => {
                self.memory
                    .lock()
                    .map_err(|_| "the in-memory secret store is unusable".to_string())?
                    .remove(account);
                Ok(())
            }
        }
    }

    /// Which of `accounts` have a secret here, in the order they were asked for. The list UI
    /// needs to show "remembered" without pulling every secret across the bridge to find out.
    pub fn present(&self, accounts: &[String]) -> Vec<String> {
        accounts
            .iter()
            .filter(|account| self.get(account).is_some())
            .cloned()
            .collect()
    }
}

fn entry(account: &str) -> Result<Entry, String> {
    Entry::new(SERVICE, account).map_err(|error| format!("no keychain entry: {error}"))
}

/// Write, read back and delete a throwaway entry. `Ok` means the keychain accepts all three, so
/// a secret put there now will be there at the next launch.
fn probe() -> Result<(), String> {
    let entry = entry(PROBE)?;
    entry
        .set_password("probe")
        .map_err(|error| format!("the keychain refused a write: {error}"))?;
    entry
        .get_password()
        .map_err(|error| format!("the keychain could not read it back: {error}"))?;
    entry
        .delete_credential()
        .map_err(|error| format!("the keychain could not delete it: {error}"))?;
    Ok(())
}

// ---- the commands the shell's page calls ----------------------------------
//
// Four verbs and a mode, all keyed by the account id `accounts.js` derived. A secret crosses the
// bridge because it has to: Subsonic carries the password (or the md5 of it) on the request
// itself, so the front end cannot build the request without it. What must not happen is the
// secret being *kept* on the front-end side beyond that request, which is the page's rule, and
// not being written anywhere, which is this module's.

#[tauri::command]
pub fn accounts_mode(state: tauri::State<'_, Vault>) -> String {
    state.mode().as_str().to_string()
}

#[tauri::command]
pub fn accounts_put(
    state: tauri::State<'_, Vault>,
    account: String,
    secret: String,
) -> Result<String, String> {
    state
        .put(&account, &secret)
        .map(|mode| mode.as_str().to_string())
}

#[tauri::command]
pub fn accounts_secret(state: tauri::State<'_, Vault>, account: String) -> Option<String> {
    state.get(&account)
}

#[tauri::command]
pub fn accounts_forget(state: tauri::State<'_, Vault>, account: String) -> Result<(), String> {
    state.forget(&account)
}

#[tauri::command]
pub fn accounts_present(state: tauri::State<'_, Vault>, accounts: Vec<String>) -> Vec<String> {
    state.present(&accounts)
}

#[cfg(test)]
mod tests {
    use super::Mode;

    #[test]
    fn the_mode_is_reported_as_the_front_end_reads_it() {
        assert_eq!(Mode::Keychain.as_str(), "keychain");
        assert_eq!(Mode::Memory.as_str(), "memory");
    }
}
