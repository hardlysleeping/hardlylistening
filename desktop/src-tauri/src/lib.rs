//! The optional desktop window for a HardlyListening server.
//!
//! Three rules, and the crate gets small very fast when they are obeyed:
//!
//! 1. **No domain rules.** The window shows what `/api/health` and `/api/v1/capabilities` say,
//!    because those are the server's own answers. There is no track, queue or member logic in
//!    here — not even a version string, which `/api/health` already carries.
//! 2. **The server is the canonical launcher.** What gets spawned is
//!    `bin/hardlylistening-server`, the same file systemd and Docker start, bundled as Tauri's
//!    sidecar. Nothing in this crate reimplements a server behaviour.
//! 3. **Ownership is explicit.** The lifecycle holds a child only if *this window* spawned it. A
//!    server found answering the health check that is not in that slot belongs to systemd,
//!    Docker or a terminal, and this window refuses to stop it — a desktop window that stops
//!    the VPS's service because it happened to be open is the failure mode this exists to
//!    prevent. Starting, stopping and closing all live in `lifecycle.rs`; this file must not
//!    grow a second spawn path beside it.
//!
//! Unverified on the machine this was written on: there is no `webkit2gtk-4.1`, so the crate
//! has never been compiled. The rules that can be tested live in `desktop/host/*.py` and
//! `desktop/ui/*.js`; see `desktop/README.md` for what a release build still needs.

mod host;
mod lifecycle;
mod probe;
mod settings;
mod wizard;

use std::process::{Command, Stdio};
use std::time::Duration;

use serde_json::{json, Value};
use tauri::Manager;

/// A local server answers in milliseconds; anything slower than this is not answering, and the
/// window must not hang waiting to find out.
const PROBE_TIMEOUT: Duration = Duration::from_secs(2);

/// Everything the window needs on startup: where the launcher is, which address it will poll,
/// and whether the sidecar was found at all (a miss is shown, never guessed around).
#[tauri::command]
fn host_env(app: tauri::AppHandle) -> Value {
    let config = host::config_path();
    let (_, config_problem) = host::read_config(&config);
    let launcher = host::launcher(&app);
    match host::address() {
        Ok(address) => json!({
            "app_version": app.package_info().version.to_string(),
            "address": { "host": address.host, "port": address.port, "source": address.source },
            "listen": address.listen(),
            "url": address.base_url(),
            "launcher": launcher.as_ref().map(|path| path.display().to_string()),
            "launcher_exists": launcher.is_some(),
            "config_path": config.display().to_string(),
            "config_problem": config_problem,
            "sidecar": host::sidecar_name(),
            "platform": std::env::consts::OS,
        }),
        Err(error) => json!({
            "app_version": app.package_info().version.to_string(),
            "address": Value::Null,
            "listen": Value::Null,
            "url": Value::Null,
            "launcher": launcher.as_ref().map(|path| path.display().to_string()),
            "launcher_exists": launcher.is_some(),
            "config_path": config.display().to_string(),
            "config_problem": config_problem,
            "sidecar": host::sidecar_name(),
            "platform": std::env::consts::OS,
            "error": error,
        }),
    }
}

/// HOST-006: the address a person could type on another device, or the reason there is none.
///
/// The decision is `desktop/host/addresses.py`, which `tests/test_host_addresses.py` drives.
/// This shell cannot import Python, but it must not grow a second precedence either: it runs
/// that file and passes the JSON through. A bundle that did not ship the detector reports a
/// miss, and the window then shows no address — the card's rule, not a failure.
#[tauri::command]
fn host_addresses(app: tauri::AppHandle) -> Value {
    let script = match host::detector(&app) {
        Some(path) => path,
        None => {
            return json!({
                "primary": Value::Null,
                "others": [],
                "note": "The address detector was not bundled, so no reachable address can be shown.",
            })
        }
    };
    let address = host::address().map(|value| value.host).unwrap_or_default();
    let output = Command::new("python3")
        .arg(&script)
        .arg(&address)
        .stdin(Stdio::null())
        .stderr(Stdio::null())
        .output();
    match output {
        Ok(done) if done.status.success() => {
            serde_json::from_slice(&done.stdout).unwrap_or_else(|error| {
                json!({ "primary": Value::Null, "others": [],
                        "note": format!("The address detector printed nothing usable: {error}") })
            })
        }
        Ok(done) => json!({ "primary": Value::Null, "others": [],
                            "note": format!("The address detector exited {}", done.status) }),
        Err(error) => json!({ "primary": Value::Null, "others": [],
                              "note": format!("The address detector could not run: {error}") }),
    }
}

/// One poll: are we holding a child, and what does the server say about itself.
#[tauri::command]
fn server_status(lifecycle_state: tauri::State<'_, lifecycle::Lifecycle>) -> Value {
    let owned = lifecycle_state.owned();
    let address = match host::address() {
        Ok(address) => address,
        Err(error) => {
            return json!({
                "owned": owned,
                "health": Value::Null,
                "health_error": error,
                "capabilities": Value::Null,
                "capabilities_error": Value::Null,
            })
        }
    };

    let (health, health_error): (Option<Value>, Option<String>) =
        match probe::get(&address.api("/api/health"), PROBE_TIMEOUT) {
            Ok(response) if (200..300).contains(&response.status) => {
                match serde_json::from_str::<Value>(&response.body) {
                    Ok(body) => (Some(body), None),
                    Err(error) => (None, Some(format!("/api/health was not JSON: {error}"))),
                }
            }
            Ok(response) => (
                None,
                Some(format!("the server answered {}", response.status)),
            ),
            Err(error) => (None, Some(error)),
        };

    // Capabilities are asked for only once health has answered: two timeouts per poll when the
    // server is down would double the window's patience for no information.
    let (capabilities, capabilities_error): (Option<Value>, Option<String>) = if health.is_some() {
        match probe::get(&address.api("/api/v1/capabilities"), PROBE_TIMEOUT) {
            Ok(response) if (200..300).contains(&response.status) => {
                match serde_json::from_str::<Value>(&response.body) {
                    Ok(body) => (Some(body), None),
                    Err(error) => (
                        None,
                        Some(format!("/api/v1/capabilities was not JSON: {error}")),
                    ),
                }
            }
            Ok(response) => (
                None,
                Some(format!("capabilities answered {}", response.status)),
            ),
            Err(error) => (None, Some(error)),
        }
    } else {
        (None, None)
    };

    json!({
        "owned": owned,
        "address": { "host": address.host, "port": address.port, "source": address.source },
        "health": health,
        "health_error": health_error,
        "capabilities": capabilities,
        "capabilities_error": capabilities_error,
    })
}

#[tauri::command]
fn lifecycle_step(
    action: String,
    value: Option<serde_json::Value>,
    app: tauri::AppHandle,
    state: tauri::State<'_, lifecycle::Lifecycle>,
) -> serde_json::Value {
    lifecycle::handle(&app, state.inner(), &action, value.as_ref())
}

/// Closing the window hands the server off rather than killing it, unless the person asked
/// otherwise in `lifecycle.json`. The Quit button is the same close, not a quieter second way.
#[tauri::command]
fn quit(
    app: tauri::AppHandle,
    state: tauri::State<'_, lifecycle::Lifecycle>,
) -> Result<(), String> {
    let _ = lifecycle::on_close(state.inner());
    app.exit(0);
    Ok(())
}

#[tauri::command]
fn open_web_ui() -> Result<String, String> {
    let address = host::address()?;
    let url = address.base_url();
    open_external(&url)?;
    Ok(url)
}

/// The OS's browser, not ours: the served UI is opened where the person already reads it, with
/// their extensions and their session. `tauri-plugin-opener` would do the same thing behind a
/// dependency; three lines of `Command` keep the crate's dependency list to Tauri and JSON.
fn open_external(url: &str) -> Result<(), String> {
    #[cfg(target_os = "linux")]
    let mut command = {
        let mut command = Command::new("xdg-open");
        command.arg(url);
        command
    };
    #[cfg(target_os = "macos")]
    let mut command = {
        let mut command = Command::new("open");
        command.arg(url);
        command
    };
    #[cfg(target_os = "windows")]
    let mut command = {
        let mut command = Command::new("cmd");
        command.args(["/C", "start", "", url]);
        command
    };
    command
        .stdin(Stdio::null())
        .stdout(Stdio::null())
        .stderr(Stdio::null())
        .spawn()
        .map(|_| ())
        .map_err(|error| format!("could not open {url}: {error}"))
}

pub fn run() {
    tauri::Builder::default()
        .manage(lifecycle::Lifecycle::default())
        .invoke_handler(tauri::generate_handler![
            host_env,
            host_addresses,
            server_status,
            open_web_ui,
            lifecycle_step,
            settings::settings_read,
            settings::settings_write,
            wizard::setup_state,
            wizard::setup_pick_folder,
            wizard::setup_save,
            wizard::setup_admin,
            quit
        ])
        .on_window_event(|window, event| {
            if let tauri::WindowEvent::CloseRequested { .. } = event {
                if let Some(state) = window.app_handle().try_state::<lifecycle::Lifecycle>() {
                    let _ = lifecycle::on_close(state.inner());
                }
            }
        })
        .run(tauri::generate_context!())
        .expect("the desktop window could not start");
}
