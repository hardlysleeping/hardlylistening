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
//! 3. **Ownership is explicit.** `Shell::child` holds only a server *this window* spawned. A
//!    server found answering the health check that is not in that slot belongs to systemd,
//!    Docker or a terminal, and this window refuses to stop it — a desktop window that stops
//!    the VPS's service because it happened to be open is the failure mode this exists to
//!    prevent.
//!
//! Unverified on the machine this was written on: there is no `webkit2gtk-4.1`, so the crate
//! has never been compiled. The rules that can be tested live in `desktop/host/resolve.py` and
//! `desktop/ui/state.js`; see `desktop/README.md` for what a release build still needs.

mod host;
mod probe;

use std::path::Path;
use std::process::{Child, Command, Stdio};
use std::sync::{Arc, Mutex};
use std::time::{Duration, Instant};

use serde_json::{json, Value};
use tauri::Manager;

/// A local server answers in milliseconds; anything slower than this is not answering, and the
/// window must not hang waiting to find out.
const PROBE_TIMEOUT: Duration = Duration::from_secs(2);
/// How long a SIGTERM gets before the process is killed. The server shuts its queue worker down
/// on SIGTERM (`SERVER-PKG-004`), so the first request is the graceful one.
const STOP_GRACE: Duration = Duration::from_secs(3);

#[derive(Default)]
struct Shell {
    /// `Some` means **this window started it**. See rule 3 above.
    child: Mutex<Option<Child>>,
}

impl Shell {
    /// Whether a server this window started is still alive. A child that exited on its own is
    /// reaped and forgotten here, so "owned" reverts to false without the frontend having to
    /// notice a pid.
    fn owned_running(&self) -> bool {
        let mut owned = match self.child.lock() {
            Ok(owned) => owned,
            Err(_) => return false,
        };
        match owned.as_mut() {
            None => false,
            Some(child) => match child.try_wait() {
                Ok(Some(_)) => {
                    *owned = None;
                    false
                }
                Ok(None) => true,
                Err(_) => true,
            },
        }
    }
}

fn failure(reason: &str, detail: String) -> Value {
    json!({ "started": false, "stopped": false, "reason": reason, "detail": detail })
}

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

/// One poll: are we holding a child, and what does the server say about itself.
#[tauri::command]
fn server_status(shell: tauri::State<'_, Arc<Shell>>) -> Value {
    let owned = shell.owned_running();
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
fn server_start(app: tauri::AppHandle, shell: tauri::State<'_, Arc<Shell>>) -> Value {
    if shell.owned_running() {
        return failure("already_started", String::new());
    }
    let address = match host::address() {
        Ok(address) => address,
        Err(error) => return failure("bad_address", error),
    };
    // Something is answering: systemd, Docker, another checkout. Start nothing, and let the
    // stop button stay refused (the child slot is empty, so it will be).
    if probe::get(&address.api("/api/health"), PROBE_TIMEOUT).is_ok() {
        return failure("already_running", format!("{} answered", address.listen()));
    }
    let launcher = match host::launcher(&app) {
        Some(path) => path,
        None => {
            return failure(
                "no_launcher",
                format!(
                    "{} was not found next to the bundle or the checkout",
                    host::sidecar_name()
                ),
            )
        }
    };

    let mut command = Command::new(&launcher);
    command
        .args(address.argv())
        .stdin(Stdio::null())
        // The server's logs belong to whoever owns it (systemd's journal, a terminal, Docker).
        // A window that buffers them would be a second log sink; HOST-005 can add a viewer.
        .stdout(Stdio::null())
        .stderr(Stdio::null());
    if let Some(root) = launcher.parent().and_then(Path::parent) {
        command.current_dir(root);
    }

    match command.spawn() {
        Ok(child) => {
            let pid = child.id();
            match shell.child.lock() {
                Ok(mut owned) => *owned = Some(child),
                Err(_) => {
                    return failure(
                        "state_unavailable",
                        "the shell's process slot is poisoned".into(),
                    )
                }
            }
            json!({ "started": true, "stopped": false, "reason": null, "detail": null, "pid": pid })
        }
        Err(error) => failure("spawn_failed", format!("{}: {error}", launcher.display())),
    }
}

/// Stop only what this window started. An external server is left alone, and the answer says
/// so, because "nothing happened" is a state the window has to be able to explain.
#[tauri::command]
async fn server_stop(shell: tauri::State<'_, Arc<Shell>>) -> Result<Value, String> {
    let shell = shell.inner().clone();
    tauri::async_runtime::spawn_blocking(move || stop_owned(&shell))
        .await
        .map_err(|error| error.to_string())
}

#[tauri::command]
async fn quit(app: tauri::AppHandle, shell: tauri::State<'_, Arc<Shell>>) -> Result<(), String> {
    let shell = shell.inner().clone();
    // Same rule as the stop button, and the same rule on the window close: an externally owned
    // server keeps running. Whether closing should *ask* is HOST-003.
    let _ = tauri::async_runtime::spawn_blocking(move || stop_owned(&shell)).await;
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

fn stop_owned(shell: &Shell) -> Value {
    let child = match shell.child.lock() {
        Ok(mut owned) => owned.take(),
        Err(_) => {
            return failure(
                "state_unavailable",
                "the shell's process slot is poisoned".into(),
            )
        }
    };
    match child {
        None => failure("not_started_here", String::new()),
        Some(mut child) => {
            let pid = child.id();
            terminate(&mut child);
            json!({ "started": false, "stopped": true, "reason": null, "detail": null, "pid": pid })
        }
    }
}

fn terminate(child: &mut Child) {
    #[cfg(unix)]
    {
        // SIGTERM first: the server shuts its queue worker down on it. SIGKILL would skip that,
        // so it is the fallback rather than the method.
        let _ = Command::new("kill")
            .arg("-TERM")
            .arg(child.id().to_string())
            .stdout(Stdio::null())
            .stderr(Stdio::null())
            .status();
        let deadline = Instant::now() + STOP_GRACE;
        while Instant::now() < deadline {
            match child.try_wait() {
                Ok(Some(_)) => return,
                Ok(None) => std::thread::sleep(Duration::from_millis(50)),
                Err(_) => break,
            }
        }
    }
    let _ = child.kill();
    let _ = child.wait();
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
        .manage(Arc::new(Shell::default()))
        .invoke_handler(tauri::generate_handler![
            host_env,
            server_status,
            server_start,
            server_stop,
            open_web_ui,
            quit
        ])
        .on_window_event(|window, event| {
            if let tauri::WindowEvent::CloseRequested { .. } = event {
                if let Some(shell) = window.app_handle().try_state::<Arc<Shell>>() {
                    stop_owned(shell.inner());
                }
            }
        })
        .run(tauri::generate_context!())
        .expect("the desktop window could not start");
}
