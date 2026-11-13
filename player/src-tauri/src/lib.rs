//! The player's entire Rust surface: start the window `tauri.conf.json` describes and serve
//! the assembled `www/` through Tauri's asset protocol. Everything a person sees and every
//! decision about a server lives in that front end, because the library, the player and the
//! look already exist in `web/` and the player runs those as they are.
//!
//! No plugin is registered here on purpose: the keychain is reached through the `keyring` crate
//! in `accounts.rs`, and the commands below are ordinary Rust functions rather than plugin
//! permissions. A native audio background mode is still absent (PLAYER-004's note, in
//! `docs/player/playback.md`).

mod accounts;
mod offline;

/// The desktop binary and the mobile projects both enter here: on iOS and Android Tauri
/// generates a `mobile_entry_point` call into this function, so the same builder runs on all
/// three platforms.
#[cfg_attr(mobile, tauri::mobile_entry_point)]
pub fn run() {
    tauri::Builder::default()
        // The secret store exists for the window's lifetime: the keychain when the platform has
        // one, an in-memory fallback that says so when it does not.
        .manage(accounts::Vault::open())
        .invoke_handler(tauri::generate_handler![
            accounts::accounts_mode,
            accounts::accounts_put,
            accounts::accounts_secret,
            accounts::accounts_forget,
            accounts::accounts_present,
            offline::offline_manifest,
            offline::offline_begin,
            offline::offline_write,
            offline::offline_commit,
            offline::offline_pin,
            offline::offline_touch,
            offline::offline_stat,
            offline::offline_read,
            offline::offline_delete,
            offline::offline_free,
        ])
        .run(tauri::generate_context!())
        .expect("the player shell could not start");
}
