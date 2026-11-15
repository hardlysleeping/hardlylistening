//! Native audio: the commands the page calls when the platform has its own player.
//!
//! What is real in this file, and what is not, stated plainly.
//!
//! **Real, and tested on this machine:** the rules — what plays after the last song, what a skip
//! does at either end, what repeat-one means to a manual press — and the argument contract a page
//! can get wrong. Both live in [`crate::native_queue`], which has no dependency on Tauri, a
//! socket or a device, and runs under `cargo test` here.
//!
//! **Not real, and deliberately absent:** the platform player. `AVQueuePlayer` is Swift with
//! Xcode, Media3 is Kotlin with the Android plugin; neither can be compiled on this machine, and
//! shipping source nobody has built would be a claim rather than a feature. So
//! [`PLATFORM_PLAYER_PRESENT`] is `false`, `native_audio_available` answers `false`, and the page
//! keeps using the WebView's own `<audio>` — exactly as it did before P3. Flipping that constant
//! is the last step of P3, and it happens on a machine that can build the app.
//!
//! What is left in this file is the door: one thin command per thing the page can ask for, each
//! of which checks its arguments, refuses clearly when there is no player, and otherwise hands
//! the work to the state machine. There is no logic here to get subtly wrong, which is the point
//! — it is the part that cannot be tested on this host.

use std::sync::Mutex;

use tauri::State;

use crate::native_queue::{
    checked_direction, checked_index, checked_queue, checked_seconds, checked_volume, Queue, Repeat,
};
use crate::subsonic::Failure;

/// The channel the page listens on. One name, both sides: `hl:native-audio`.
pub const NATIVE_AUDIO_EVENT: &str = "hl:native-audio";

/// Whether a platform player is compiled into this build.
///
/// `false` until the Swift and Kotlin sources exist and have been compiled by Xcode and the
/// Android toolchain. A `true` here with no player behind it would be the worst outcome available:
/// the page would stop using its own audio and every play button would do nothing.
pub const PLATFORM_PLAYER_PRESENT: bool = false;

const NO_PLAYER: &str =
    "This build plays through its own WebView; the platform player arrives with the mobile build";

/// The order and the playhead. The page's copy is for painting; this one is what a player obeys.
pub struct AudioState(Mutex<Queue>);

impl AudioState {
    pub fn new() -> AudioState {
        AudioState(Mutex::new(Queue::new(Vec::new(), Repeat::Off)))
    }

    fn lock(&self) -> Result<std::sync::MutexGuard<'_, Queue>, Failure> {
        self.0
            .lock()
            .map_err(|_| Failure::config("The audio queue is unusable"))
    }
}

/// Refuse, clearly, when there is no player to talk to.
fn require_player() -> Result<(), Failure> {
    if PLATFORM_PLAYER_PRESENT {
        Ok(())
    } else {
        Err(Failure::config(NO_PLAYER))
    }
}

/* ---- the commands the page calls ------------------------------------------------- */

#[tauri::command]
pub fn native_audio_available() -> bool {
    PLATFORM_PLAYER_PRESENT
}

/// Replace the order. `native_audio_play` is the explicit "start this one"; this says what exists.
#[tauri::command(rename_all = "camelCase")]
pub fn native_audio_queue(
    state: State<'_, AudioState>,
    ids: Vec<String>,
    repeat: String,
) -> Result<(), Failure> {
    let queue = checked_queue(ids, &repeat).map_err(Failure::config)?;
    require_player()?;
    *state.lock()? = queue;
    Ok(())
}

#[tauri::command(rename_all = "camelCase")]
pub fn native_audio_play(state: State<'_, AudioState>, index: usize) -> Result<(), Failure> {
    {
        let mut queue = state.lock()?;
        checked_index(&queue, index).map_err(Failure::config)?;
        queue.play(index);
    }
    require_player()
}

#[tauri::command(rename_all = "camelCase")]
pub fn native_audio_skip(state: State<'_, AudioState>, direction: i32) -> Result<(), Failure> {
    {
        let forward = checked_direction(direction).map_err(Failure::config)?;
        state.lock()?.skip(forward);
    }
    require_player()
}

#[tauri::command(rename_all = "camelCase")]
pub fn native_audio_repeat(state: State<'_, AudioState>, repeat: String) -> Result<(), Failure> {
    {
        let mode = Repeat::parse(&repeat).map_err(Failure::config)?;
        state.lock()?.set_repeat(mode);
    }
    require_player()
}

#[tauri::command(rename_all = "camelCase")]
pub fn native_audio_resume() -> Result<(), Failure> {
    require_player()
}

#[tauri::command(rename_all = "camelCase")]
pub fn native_audio_pause() -> Result<(), Failure> {
    require_player()
}

#[tauri::command(rename_all = "camelCase")]
pub fn native_audio_seek(seconds: f64) -> Result<(), Failure> {
    checked_seconds(seconds).map_err(Failure::config)?;
    require_player()
}

#[tauri::command(rename_all = "camelCase")]
pub fn native_audio_volume(volume: f64) -> Result<(), Failure> {
    checked_volume(volume).map_err(Failure::config)?;
    require_player()
}

#[tauri::command(rename_all = "camelCase")]
pub fn native_audio_mute(muted: bool) -> Result<(), Failure> {
    let _ = muted;
    require_player()
}

/// Stop and forget the order: the page is closing, or a person signed out.
#[tauri::command(rename_all = "camelCase")]
pub fn native_audio_stop(state: State<'_, AudioState>) -> Result<(), Failure> {
    if let Ok(mut queue) = state.lock() {
        *queue = Queue::new(Vec::new(), Repeat::Off);
    }
    require_player()
}
