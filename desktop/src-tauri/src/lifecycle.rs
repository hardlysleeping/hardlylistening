// HOST-003: spawn, TERM, kill, wait — and nothing that is a decision.
//
// Every rule this file obeys is written and tested in `desktop/host/lifecycle.py`: what a start
// is, when it has failed, which stop is refused, and what closing the window does. This is the
// OS half of that spec, and it is kept deliberately boring, because the crate cannot be
// compiled on the machine it was written on. `tests/test_host_lifecycle.py` compares the
// constants below against the Python ones, so the two copies cannot drift silently.
//
// The rule underneath the rest: the window's child slot holds **only a process this window
// spawned**. An external server is never signalled, and closing the window hands off rather than
// kills unless the person stored `stop_on_close`.

use std::net::{TcpStream, ToSocketAddrs};
use std::path::{Path, PathBuf};
use std::process::{Child, Command, Stdio};
use std::sync::Mutex;
use std::time::{Duration, Instant, SystemTime, UNIX_EPOCH};

use serde_json::{json, Value};

use crate::host;
use crate::probe;

/// Matches `START_DEADLINE` in `desktop/host/lifecycle.py` and `STATUS_AFTER_START_MS` in
/// `desktop/ui/state.js`: the window's status line and the start watch cannot disagree.
pub const START_DEADLINE: Duration = Duration::from_secs(20);
pub const STOP_GRACE: Duration = Duration::from_secs(3);
pub const POLL_INTERVAL: Duration = Duration::from_millis(100);
pub const HEALTH_TIMEOUT: Duration = Duration::from_secs(2);
pub const PORT_TIMEOUT: Duration = Duration::from_millis(250);
/// The state file, beside `host.json`. Named here and justified in `docs/desktop/lifecycle.md`.
pub const HANDOFF_FILE: &str = "lifecycle.json";

pub const IDLE: &str = "idle";
pub const STARTING: &str = "starting";
pub const UP: &str = "up";
pub const FAILED: &str = "failed";
pub const STOPPING: &str = "stopping";

/// The one state a window keeps. `phase` and `ownership` are separate because "failed" and "not
/// mine" are different facts; ownership is derived from the child slot, never stored beside it.
pub struct Lifecycle {
    state: Mutex<State>,
}

struct State {
    child: Option<Child>,
    phase: &'static str,
    reason: String,
    detail: String,
    pid: Option<u32>,
}

impl Default for Lifecycle {
    fn default() -> Self {
        Lifecycle {
            state: Mutex::new(State {
                child: None,
                phase: IDLE,
                reason: String::new(),
                detail: String::new(),
                pid: None,
            }),
        }
    }
}

// ---- OS primitives -------------------------------------------------------------------------

/// The host a client connects to: a listen-anywhere bind is reached on loopback. `host.rs`
/// keeps its own copy private, and this crate does not reach into another module's internals.
pub fn probe_host(address: &host::Address) -> String {
    match address.host.as_str() {
        "0.0.0.0" | "" | "*" => "127.0.0.1".to_string(),
        "::" | "[::]" => "::1".to_string(),
        other => other
            .trim_matches(|character| character == '[' || character == ']')
            .to_string(),
    }
}

/// Whether something is bound at `host:port`. A refusal means the address is free.
pub fn port_open(host_name: &str, port: u16) -> bool {
    let target = match (host_name, port).to_socket_addrs() {
        Ok(mut addresses) => match addresses.next() {
            Some(address) => address,
            None => return false,
        },
        Err(_) => return false,
    };
    TcpStream::connect_timeout(&target, PORT_TIMEOUT).is_ok()
}

/// One health question, the same one the window's status poll asks.
pub fn healthy(address: &host::Address) -> bool {
    match probe::get(&address.api("/api/health"), HEALTH_TIMEOUT) {
        Ok(response) => (200..300).contains(&response.status),
        Err(_) => false,
    }
}

/// The spawn plan: the canonical launcher, explicit flags, the address exported twice (argv and
/// environment say the same thing so the server cannot serve where the window does not poll).
pub fn command(launcher: &Path, address: &host::Address) -> Command {
    let mut command = Command::new(launcher);
    command
        .args(address.argv())
        .env("HARDLYLISTENING_HOST", &address.host)
        .env("HARDLYLISTENING_PORT", address.port.to_string())
        .stdin(Stdio::null())
        // The server's logs belong to its owner (the journal, a terminal, Docker's log driver).
        // Capturing them here would make this window a second log sink.
        .stdout(Stdio::null())
        .stderr(Stdio::null());
    // The launcher lives in <app>/bin and locates the application relative to itself.
    if let Some(root) = launcher.parent().and_then(Path::parent) {
        if root.is_dir() {
            command.current_dir(root);
        }
    }
    #[cfg(unix)]
    {
        use std::os::unix::process::CommandExt;
        // Its own process group: a terminal Ctrl+C aimed at the window must not take down a
        // server the window is about to hand off.
        command.process_group(0);
    }
    command
}

pub enum Ready {
    Up,
    /// Exited on its own before answering; the code, when the OS reports one.
    Exited(Option<i32>),
    TimedOut,
}

/// Watch health until it answers, the child exits, or the deadline passes.
pub fn wait_ready(child: &mut Child, address: &host::Address, deadline: Duration) -> Ready {
    let until = Instant::now() + deadline;
    while Instant::now() < until {
        match child.try_wait() {
            Ok(Some(status)) => return Ready::Exited(status.code()),
            Ok(None) => {}
            Err(_) => {}
        }
        if healthy(address) {
            return Ready::Up;
        }
        std::thread::sleep(POLL_INTERVAL);
    }
    Ready::TimedOut
}

pub enum Stop {
    Terminated,
    Killed,
}

fn wait_exit(child: &mut Child, seconds: Duration) -> bool {
    let until = Instant::now() + seconds;
    while Instant::now() < until {
        match child.try_wait() {
            Ok(Some(_)) => return true,
            Ok(None) => {}
            Err(_) => return true,
        }
        std::thread::sleep(POLL_INTERVAL);
    }
    matches!(child.try_wait(), Ok(Some(_)) | Err(_))
}

/// SIGTERM first (the server shuts its queue worker down on it), SIGKILL only after the grace.
pub fn terminate(child: &mut Child, grace: Duration) -> Stop {
    #[cfg(unix)]
    {
        let _ = Command::new("kill")
            .arg("-TERM")
            .arg(child.id().to_string())
            .stdout(Stdio::null())
            .stderr(Stdio::null())
            .status();
        if wait_exit(child, grace) {
            return Stop::Terminated;
        }
    }
    // Windows has no graceful path here: `Child::kill` is a hard terminate. Stated in the docs
    // rather than papered over.
    let _ = child.kill();
    let _ = child.wait();
    Stop::Killed
}

// ---- the window's memory -------------------------------------------------------------------

pub fn state_path(config: &Path) -> PathBuf {
    config
        .parent()
        .unwrap_or_else(|| Path::new("."))
        .join(HANDOFF_FILE)
}

pub fn read_state(config: &Path) -> Value {
    let text = match std::fs::read_to_string(state_path(config)) {
        Ok(text) => text,
        Err(_) => return json!({}),
    };
    match serde_json::from_str::<Value>(&text) {
        Ok(value) if value.is_object() => value,
        _ => json!({}),
    }
}

/// Replace the file atomically: a crash mid-write leaves the old record, not half of one.
pub fn write_state(config: &Path, value: &Value) -> Result<(), String> {
    let path = state_path(config);
    let tmp = path.with_extension("json.tmp");
    if let Some(dir) = path.parent() {
        std::fs::create_dir_all(dir).map_err(|error| format!("{}: {error}", dir.display()))?;
    }
    let text = serde_json::to_string_pretty(value).map_err(|error| error.to_string())?;
    std::fs::write(&tmp, format!("{text}\n"))
        .map_err(|error| format!("{}: {error}", tmp.display()))?;
    std::fs::rename(&tmp, &path).map_err(|error| format!("{}: {error}", path.display()))
}

pub fn pid_alive(pid: u32) -> bool {
    if pid == 0 {
        return false;
    }
    #[cfg(target_os = "linux")]
    {
        Path::new("/proc").join(pid.to_string()).exists()
    }
    #[cfg(not(target_os = "linux"))]
    {
        true // No portable probe without a dependency; the record is advice, not authority.
    }
}

/// The handed-off server, if a window left one still running. A record naming a dead pid is
/// removed rather than reported — the same rule as `lifecycle.py`.
pub fn handoff(config: &Path) -> Value {
    let state = read_state(config);
    let record = match state.get("handoff") {
        Some(value) if value.is_object() => value.clone(),
        _ => return Value::Null,
    };
    let pid = record.get("pid").and_then(Value::as_u64).unwrap_or(0) as u32;
    if !pid_alive(pid) {
        let _ = clear_handoff(config);
        return Value::Null;
    }
    record
}

pub fn clear_handoff(config: &Path) -> Result<(), String> {
    let mut state = read_state(config);
    if let Some(object) = state.as_object_mut() {
        object.remove("handoff");
    }
    write_state(config, &state)
}

pub fn stop_on_close(config: &Path) -> bool {
    read_state(config).get("stop_on_close") == Some(&Value::Bool(true))
}

pub fn set_stop_on_close(config: &Path, flag: bool) -> Result<(), String> {
    let mut state = read_state(config);
    if let Some(object) = state.as_object_mut() {
        object.insert("stop_on_close".to_string(), Value::Bool(flag));
    }
    write_state(config, &state)
}

fn now() -> f64 {
    SystemTime::now()
        .duration_since(UNIX_EPOCH)
        .map(|elapsed| elapsed.as_secs_f64())
        .unwrap_or(0.0)
}

// ---- the transitions -----------------------------------------------------------------------

impl Lifecycle {
    /// Whether the child this window spawned is still running; a dead one is reaped here.
    fn alive(state: &mut State) -> bool {
        match state.child.as_mut() {
            None => false,
            Some(child) => match child.try_wait() {
                Ok(Some(_)) => {
                    state.child = None;
                    state.pid = None;
                    false
                }
                Ok(None) => true,
                Err(_) => true,
            },
        }
    }

    pub fn snapshot(&self, address: &host::Address, config: &Path) -> Value {
        let mut state = match self.state.lock() {
            Ok(state) => state,
            Err(_) => return json!({ "reason": "state_unavailable" }),
        };
        let ours = Self::alive(&mut state) && state.phase != IDLE;
        let record = handoff(config);
        let ownership = if ours {
            "ours"
        } else if record.is_object() {
            // A server a previous window left running is not this window's to stop: the record
            // says it exists, not that this window may signal it.
            "external"
        } else {
            "none"
        };
        json!({
            "phase": state.phase,
            "ownership": ownership,
            "pid": state.pid,
            "reason": state.reason,
            "detail": state.detail,
            "listen": address.listen(),
            "url": address.base_url(),
            "address": { "host": address.host, "port": address.port, "source": address.source },
            "stop_on_close": stop_on_close(config),
            "handoff": record,
        })
    }

    /// Spawn and watch. Mirrors `Lifecycle.start` in `lifecycle.py`, reason for reason.
    pub fn start(&self, app: &tauri::AppHandle, address: &host::Address, config: &Path) -> Value {
        let mut state = match self.state.lock() {
            Ok(state) => state,
            Err(_) => return json!({ "started": false, "reason": "state_unavailable" }),
        };
        if Self::alive(&mut state) {
            return json!({ "started": true, "reason": "already_started", "phase": state.phase,
                           "ownership": "ours", "pid": state.pid });
        }
        if healthy(address) {
            state.phase = UP;
            state.reason = "already_running".into();
            state.detail = format!(
                "{} answered; this window did not start that server, so it will not stop it",
                address.listen()
            );
            return json!({ "started": false, "reason": state.reason, "detail": state.detail,
                           "phase": UP, "ownership": "external" });
        }
        if port_open(&probe_host(address), address.port) {
            state.phase = FAILED;
            state.reason = "port_in_use".into();
            state.detail = format!(
                "{} is already in use and does not answer /api/health; this window will not start a second server there",
                address.listen()
            );
            return json!({ "started": false, "reason": state.reason, "detail": state.detail,
                           "phase": FAILED, "ownership": "external" });
        }
        let launcher = match host::launcher(app) {
            Some(path) => path,
            None => {
                state.phase = FAILED;
                state.reason = "no_launcher".into();
                state.detail = format!("{} was not found", host::sidecar_name());
                return json!({ "started": false, "reason": state.reason, "detail": state.detail,
                               "phase": FAILED, "ownership": "none" });
            }
        };
        let mut child = match command(&launcher, address).spawn() {
            Ok(child) => child,
            Err(error) => {
                state.phase = FAILED;
                state.reason = "spawn_failed".into();
                state.detail = format!("{}: {error}", launcher.display());
                return json!({ "started": false, "reason": state.reason, "detail": state.detail,
                               "phase": FAILED, "ownership": "none" });
            }
        };
        state.pid = Some(child.id());
        state.phase = STARTING;
        state.reason.clear();
        state.detail.clear();

        match wait_ready(&mut child, address, START_DEADLINE) {
            Ready::Up => {
                state.child = Some(child);
                state.phase = UP;
                json!({ "started": true, "phase": UP, "ownership": "ours", "pid": state.pid })
            }
            Ready::Exited(code) => {
                let detail = format!(
                    "the server exited with status {} before it answered {}",
                    code.map(|value| value.to_string())
                        .unwrap_or_else(|| "unknown".into()),
                    address.base_url()
                );
                state.child = None;
                state.pid = None;
                state.phase = FAILED;
                state.reason = "exited".into();
                state.detail = detail.clone();
                json!({ "started": false, "reason": "exited", "detail": detail,
                       "phase": FAILED, "ownership": "none" })
            }
            Ready::TimedOut => {
                // The process is still this window's, so Stop stays available: killing it here
                // would hide the reason the window is showing.
                state.child = Some(child);
                state.phase = FAILED;
                state.reason = "timeout".into();
                state.detail = format!(
                    "no answer from {} within {} s; the process this window started is still running",
                    address.base_url(),
                    START_DEADLINE.as_secs()
                );
                json!({ "started": false, "reason": "timeout", "detail": state.detail,
                       "phase": FAILED, "ownership": "ours", "pid": state.pid })
            }
        }
    }

    /// SIGTERM, grace, SIGKILL — only for a child this window spawned.
    pub fn stop(&self, address: &host::Address, config: &Path) -> Value {
        let mut state = match self.state.lock() {
            Ok(state) => state,
            Err(_) => return json!({ "stopped": false, "reason": "state_unavailable" }),
        };
        if !Self::alive(&mut state) {
            state.child = None;
            state.pid = None;
            let ownership;
            if healthy(address) {
                state.phase = UP;
                state.reason = "not_started_here".into();
                state.detail = format!(
                    "{} is answered by a server this window did not start (systemd, Docker or a terminal); it was left alone",
                    address.listen()
                );
                ownership = "external";
            } else {
                state.phase = IDLE;
                state.reason = "not_started_here".into();
                state.detail = "this window is not running a server".into();
                ownership = "none";
            }
            return json!({ "stopped": false, "reason": state.reason, "detail": state.detail,
                           "phase": state.phase, "ownership": ownership });
        }
        let mut child = match state.child.take() {
            Some(child) => child,
            None => return json!({ "stopped": false, "reason": "not_started_here" }),
        };
        let pid = child.id();
        state.phase = STOPPING;
        let outcome = terminate(&mut child, STOP_GRACE);
        state.pid = None;
        state.phase = IDLE;
        state.reason.clear();
        let forced = matches!(outcome, Stop::Killed);
        state.detail = if forced {
            "the server did not exit within the grace period and was killed".into()
        } else {
            "the server exited on SIGTERM".into()
        };
        let _ = clear_handoff(config);
        json!({ "stopped": true, "forced": forced, "pid": pid, "phase": IDLE,
                "ownership": "none", "detail": state.detail })
    }

    /// The window is closing. Default is handoff; `stop_on_close` makes it a stop.
    pub fn close(
        &self,
        address: &host::Address,
        config: &Path,
        keep_running: Option<bool>,
    ) -> Value {
        let keep = keep_running.unwrap_or_else(|| !stop_on_close(config));
        let running = match self.state.lock() {
            Ok(mut state) => Self::alive(&mut state),
            Err(_) => false,
        };
        if !running {
            return json!({ "action": "nothing", "stopped": false,
                           "detail": "nothing this window started is running" });
        }
        if !keep {
            let mut stopped = self.stop(address, config);
            if let Some(object) = stopped.as_object_mut() {
                object.insert("action".to_string(), json!("stop"));
            }
            return stopped;
        }
        let mut state = match self.state.lock() {
            Ok(state) => state,
            Err(_) => return json!({ "action": "handoff", "problem": true }),
        };
        let pid = state.pid.unwrap_or(0);
        let mut data = read_state(config);
        if let Some(object) = data.as_object_mut() {
            object.insert(
                "handoff".to_string(),
                json!({ "pid": pid, "listen": address.listen(), "recorded_at": now() }),
            );
        }
        let problem = write_state(config, &data).err().unwrap_or_default();
        // The record is written; the child keeps running and this window forgets it. The next
        // window reads the record and treats the server as external to itself.
        state.child = None;
        state.pid = None;
        state.phase = IDLE;
        state.reason.clear();
        json!({ "action": "handoff", "stopped": false, "handoff_pid": pid,
                "detail": format!("pid {pid} keeps serving {}", address.listen()),
                "problem": problem })
    }
}

/// The one command the window calls. `action` is `state`, `start`, `stop`, `close` or
/// `close_behaviour`; `value` carries the boolean for the last one.
pub fn handle(
    app: &tauri::AppHandle,
    lifecycle: &Lifecycle,
    action: &str,
    value: Option<&Value>,
) -> Value {
    let config = host::config_path();
    let address = match host::address() {
        Ok(address) => address,
        Err(error) => return json!({ "reason": "bad_address", "detail": error }),
    };
    match action {
        "state" => lifecycle.snapshot(&address, &config),
        "start" => lifecycle.start(app, &address, &config),
        "stop" => lifecycle.stop(&address, &config),
        "close" => lifecycle.close(&address, &config, None),
        "close_behaviour" => {
            let flag = value.and_then(Value::as_bool).unwrap_or(false);
            match set_stop_on_close(&config, flag) {
                Ok(()) => json!({ "saved": true, "stop_on_close": flag }),
                Err(problem) => json!({ "saved": false, "detail": problem }),
            }
        }
        other => json!({ "reason": "unknown_action", "detail": other }),
    }
}

/// The window's close event: hand off (or stop, if the person chose that), never kill an
/// external server. Returns the decision so a caller may show it.
pub fn on_close(lifecycle: &Lifecycle) -> Value {
    let config = host::config_path();
    match host::address() {
        Ok(address) => lifecycle.close(&address, &config, None),
        Err(error) => json!({ "action": "nothing", "detail": error }),
    }
}
