//! The player's entire Rust surface: start the window `tauri.conf.json` describes and serve
//! the assembled `www/` through Tauri's asset protocol. Everything a person sees and every
//! decision about a server lives in that front end, because the library, the player and the
//! look already exist in `web/` and the player runs those as they are.
//!
//! No plugin is registered here on purpose. A Rust-side fetcher, a keychain and an audio
//! background mode each arrive with the card that needs them (PLAYER-002 for credentials,
//! PLAYER-004 for playback), and adding one now would be a dependency with no caller.

/// The desktop binary and the mobile projects both enter here: on iOS and Android Tauri
/// generates a `mobile_entry_point` call into this function, so the same builder runs on all
/// three platforms.
#[cfg_attr(mobile, tauri::mobile_entry_point)]
pub fn run() {
    tauri::Builder::default()
        .run(tauri::generate_context!())
        .expect("the player shell could not start");
}
