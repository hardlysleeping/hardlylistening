//! The player's entire Rust surface: start the window `tauri.conf.json` describes and serve
//! the built `ui/dist/` through Tauri's asset protocol. Everything a person sees and every
//! decision about a server lives in that front end: the player is its own Svelte build in
//! `ui/`, a Subsonic client rather than a shell around this repository's own catalogue.
//!
//! No plugin is registered here on purpose: the keychain is reached through the `keyring` crate
//! in `accounts.rs`, and requests to a music server are made by `subsonic.rs` rather than by the
//! page. Both are ordinary Rust functions rather than plugin permissions, which is what keeps the
//! surface small: there is no IPC command in this build that takes a URL.
//!
//! Native audio is a seam rather than a player. `native_audio.rs` declares the commands a platform
//! player would answer and holds the order in `native_queue.rs`, but no platform player is
//! compiled in, so `native_audio_available` answers `false` and every build keeps playing through
//! its own WebView. That gap is stated in `docs/player/native-audio.md` rather than implied, and
//! closing it is the mobile build's job — it needs Xcode and the Android SDK, not this host.

mod accounts;
mod native_audio;
pub mod native_queue;
mod subsonic;
mod subsonic_url;

/// The desktop binary and the mobile projects both enter here: on iOS and Android Tauri
/// generates a `mobile_entry_point` call into this function, so the same builder runs on all
/// three platforms.
#[cfg_attr(mobile, tauri::mobile_entry_point)]
pub fn run() {
    tauri::Builder::default()
        // The secret store exists for the window's lifetime: the keychain when the platform has
        // one, an in-memory fallback that says so when it does not.
        .manage(accounts::Vault::open())
        // The addresses this session has bound, by account id. Empty until a person signs in.
        .manage(subsonic::Registry::new())
        // The order a platform player would obey. Empty on every build today: no platform player
        // is compiled in, and `native_audio_available` says so.
        .manage(native_audio::AudioState::new())
        .invoke_handler(tauri::generate_handler![
            accounts::accounts_mode,
            accounts::accounts_put,
            accounts::accounts_secret,
            accounts::accounts_forget,
            accounts::accounts_present,
            subsonic::subsonic_bind,
            subsonic::subsonic_unbind,
            subsonic::subsonic_request,
            subsonic::subsonic_source,
            subsonic::subsonic_cover,
            subsonic::subsonic_public,
            native_audio::native_audio_available,
            native_audio::native_audio_queue,
            native_audio::native_audio_play,
            native_audio::native_audio_skip,
            native_audio::native_audio_repeat,
            native_audio::native_audio_resume,
            native_audio::native_audio_pause,
            native_audio::native_audio_seek,
            native_audio::native_audio_volume,
            native_audio::native_audio_mute,
            native_audio::native_audio_stop,
        ])
        .run(tauri::generate_context!())
        .expect("the player shell could not start");
}
