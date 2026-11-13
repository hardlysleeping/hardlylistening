// Where the shell looks for the server, and which address it talks to.
//
// This is the Rust form of `desktop/host/resolve.py`, which is the *tested* copy: this crate
// cannot be compiled on a machine without `webkit2gtk-4.1`, so the decisions are proven in
// Python and mirrored here. `tests/test_desktop_host.py` reads this file for the two constants
// below and fails if they drift from the Python ones — that is the cheapest possible guard
// against a rule that exists twice and disagrees with itself.
//
// The rule the whole shell rests on: the server is `bin/hardlylistening-server`, the same
// artefact systemd and Docker start. The desktop bundles *that file* under Tauri's sidecar name
// and spawns it; it does not reimplement a single thing the server does.

use std::path::{Path, PathBuf};

use serde_json::Value;

/// The canonical entry point, relative to the application root.
pub const LAUNCHER_NAME: &str = "hardlylistening-server";
/// The server's documented defaults (`config.py`), repeated because the shell imports no server
/// module. The server still wins: these become `--host`/`--port` flags, not assumptions.
pub const DEFAULT_HOST: &str = "127.0.0.1";
pub const DEFAULT_PORT: u16 = 8470;

const CONFIG_DIR: &str = "hardlylistening-desktop";
const CONFIG_FILE: &str = "host.json";

#[derive(Clone, Debug)]
pub struct Address {
    pub host: String,
    pub port: u16,
    /// "environment", "desktop-config" or "default" — shown in the window so a surprising
    /// address can be explained without opening a terminal.
    pub source: &'static str,
}

impl Address {
    /// The host a *client* connects to. `0.0.0.0` and `::` are listen-anywhere addresses and
    /// are not reachable as written, so polling and the browser use loopback.
    fn probe_host(&self) -> &str {
        match self.host.as_str() {
            "0.0.0.0" | "" | "*" => "127.0.0.1",
            "::" | "[::]" => "::1",
            other => other,
        }
    }

    fn host_for_url(&self) -> String {
        let host = self.probe_host();
        if host.contains(':') && !host.starts_with('[') {
            format!("[{host}]")
        } else {
            host.to_string()
        }
    }

    pub fn base_url(&self) -> String {
        format!("http://{}:{}", self.host_for_url(), self.port)
    }

    pub fn api(&self, path: &str) -> String {
        format!("{}/{}", self.base_url(), path.trim_start_matches('/'))
    }

    /// `host:port` as the launcher prints it — the status line's address.
    pub fn listen(&self) -> String {
        let host = if self.host.contains(':') && !self.host.starts_with('[') {
            format!("[{}]", self.host)
        } else {
            self.host.clone()
        };
        format!("{host}:{}", self.port)
    }

    /// The flags, explicit, so the server cannot resolve a different address than we poll.
    pub fn argv(&self) -> Vec<String> {
        vec![
            "--host".to_string(),
            if self.host.is_empty() {
                DEFAULT_HOST.to_string()
            } else {
                self.host.clone()
            },
            "--port".to_string(),
            self.port.to_string(),
        ]
    }
}

fn env_text(name: &str) -> Option<String> {
    match std::env::var(name) {
        Ok(value) if !value.trim().is_empty() => Some(value.trim().to_string()),
        _ => None,
    }
}

fn home_dir() -> PathBuf {
    env_text("HOME")
        .or_else(|| env_text("USERPROFILE"))
        .map(PathBuf::from)
        .unwrap_or_default()
}

pub fn config_path() -> PathBuf {
    if let Some(declared) = env_text("HARDLYLISTENING_DESKTOP_CONFIG") {
        return PathBuf::from(declared);
    }
    let base = match env_text("XDG_STATE_HOME") {
        Some(state) => PathBuf::from(state),
        None => home_dir().join(".local").join("state"),
    };
    base.join(CONFIG_DIR).join(CONFIG_FILE)
}

/// The shell's config file. A malformed file is reported and ignored, never fatal: a window
/// that refuses to open because a JSON file lost a brace is worse than one that says so.
pub fn read_config(path: &Path) -> (Value, String) {
    let text = match std::fs::read_to_string(path) {
        Ok(text) => text,
        Err(error) if error.kind() == std::io::ErrorKind::NotFound => {
            return (Value::Null, String::new())
        }
        Err(error) => {
            return (
                Value::Null,
                format!("{} could not be read: {error}", path.display()),
            )
        }
    };
    match serde_json::from_str::<Value>(&text) {
        Ok(value) if value.is_object() => (value, String::new()),
        Ok(_) => (
            Value::Null,
            format!("{} does not hold an object", path.display()),
        ),
        Err(error) => (
            Value::Null,
            format!("{} is not JSON: {error}", path.display()),
        ),
    }
}

fn stored_port(stored: &Value) -> Result<u16, String> {
    match stored.get("port") {
        None | Some(Value::Null) => Ok(DEFAULT_PORT),
        Some(value) => parse_port(&value.to_string(), "the desktop config port"),
    }
}

fn parse_port(raw: &str, origin: &str) -> Result<u16, String> {
    let text = raw.trim().trim_matches('"');
    match text.parse::<u32>() {
        Ok(value) if (1..65536).contains(&value) => Ok(value as u16),
        Ok(_) => Err(format!("{origin} is out of range: {text}")),
        Err(_) => Err(format!("{origin} is not a number: {text}")),
    }
}

/// Environment, then the shell's config file, then the defaults. A typo is an error rather than
/// a silent fallback, for the same reason `config.port()` refuses one: starting the server
/// somewhere the operator did not ask for is worse than not starting it.
pub fn address() -> Result<Address, String> {
    let stored = read_config(&config_path()).0;
    let env_host = env_text("HARDLYLISTENING_HOST");
    let env_port = env_text("HARDLYLISTENING_PORT");
    let stored_host = stored
        .get("host")
        .and_then(Value::as_str)
        .map(str::to_string)
        .filter(|host| !host.trim().is_empty());

    if env_host.is_some() || env_port.is_some() {
        return Ok(Address {
            host: env_host
                .or(stored_host)
                .unwrap_or_else(|| DEFAULT_HOST.to_string()),
            port: match env_port {
                Some(raw) => parse_port(&raw, "HARDLYLISTENING_PORT")?,
                None => stored_port(&stored)?,
            },
            source: "environment",
        });
    }
    if stored_host.is_some() || stored.get("port").is_some() {
        return Ok(Address {
            host: stored_host.unwrap_or_else(|| DEFAULT_HOST.to_string()),
            port: stored_port(&stored)?,
            source: "desktop-config",
        });
    }
    Ok(Address {
        host: DEFAULT_HOST.to_string(),
        port: DEFAULT_PORT,
        source: "default",
    })
}

/// Tauri's sidecar convention: `name` in `tauri.conf.json`, `name-<triple>` on disk.
pub fn target_triple() -> Option<&'static str> {
    if cfg!(all(target_os = "linux", target_arch = "x86_64")) {
        Some("x86_64-unknown-linux-gnu")
    } else if cfg!(all(target_os = "linux", target_arch = "aarch64")) {
        Some("aarch64-unknown-linux-gnu")
    } else if cfg!(all(target_os = "macos", target_arch = "x86_64")) {
        Some("x86_64-apple-darwin")
    } else if cfg!(all(target_os = "macos", target_arch = "aarch64")) {
        Some("aarch64-apple-darwin")
    } else if cfg!(all(target_os = "windows", target_arch = "x86_64")) {
        Some("x86_64-pc-windows-msvc")
    } else {
        None
    }
}

pub fn sidecar_name() -> String {
    match target_triple() {
        Some(triple) => format!("{LAUNCHER_NAME}-{triple}"),
        None => LAUNCHER_NAME.to_string(),
    }
}

/// The bundle layout for `externalBin` differs per target, so the search order is explicit and
/// a miss is reported rather than guessed at.
fn bundled(app: &tauri::AppHandle) -> Option<PathBuf> {
    use tauri::Manager;

    let name = sidecar_name();
    let mut roots = Vec::new();
    if let Ok(dir) = app.path().resource_dir() {
        roots.push(dir.clone());
        roots.push(dir.join("bin"));
    }
    if let Ok(exe) = std::env::current_exe() {
        if let Some(dir) = exe.parent() {
            roots.push(dir.to_path_buf());
        }
    }
    for root in roots {
        for candidate in [root.join(&name), root.join(LAUNCHER_NAME)] {
            if candidate.is_file() {
                return Some(candidate);
            }
        }
    }
    None
}

/// A development run: walk up from the binary looking for a checkout (`bin/` beside `web/`).
/// No build-machine path is compiled in — a release bundle simply finds nothing here.
fn dev_launcher() -> Option<PathBuf> {
    let mut dir = std::env::current_exe().ok()?.parent()?.to_path_buf();
    for _ in 0..6 {
        let candidate = dir.join("bin").join(LAUNCHER_NAME);
        if candidate.is_file() && dir.join("server.py").is_file() {
            return Some(candidate);
        }
        dir = dir.parent()?.to_path_buf();
    }
    None
}

pub fn launcher(app: &tauri::AppHandle) -> Option<PathBuf> {
    if let Some(declared) = env_text("HARDLYLISTENING_SERVER") {
        return Some(PathBuf::from(declared));
    }
    bundled(app).or_else(dev_launcher)
}
