// Settings are a file and one desktop entry.
//
// The rules — what "local only" means for the bind address, what the two switches do, and when
// the environment wins — live in `desktop/host/settings.py`, which is the tested copy.
// `tests/test_host_settings.py` runs them. This module does only what needs a process: read the
// shell's config, write it back without losing keys it does not own, and install or remove the
// autostart entry.
//
// Never compiled on the machine this was written on (no `webkit2gtk-4.1`), so `rustfmt --check`
// is the whole proof of the Rust half.
//
// The one rule repeated here rather than delegated: **the autostart entry runs the server, not
// the window.** Its `Exec` is the launcher with `run --host … --port …` and the same `--no-…`
// switches the window spawns with.

use std::path::{Path, PathBuf};

use serde_json::{json, Map, Value};

use crate::host;

/// XDG's autostart directory and the one file this module may write there. Mirrored by
/// `desktop/host/settings.py`; `tests/test_host_settings.py` compares the two strings so a
/// renamed entry cannot be installed where nobody looks for it.
pub const AUTOSTART_DIR: &str = "autostart";
pub const AUTOSTART_FILE: &str = "hardlylistening-server.desktop";

/// The bind address each named exposure is. The server has one bind control (`--host`), so these
/// are spellings of it, not a second concept.
const LOCAL_HOST: &str = "127.0.0.1";
const ANY_HOST: &str = "0.0.0.0";

fn env_text(name: &str) -> Option<String> {
    match std::env::var(name) {
        Ok(value) if !value.trim().is_empty() => Some(value.trim().to_string()),
        _ => None,
    }
}

/// The only spellings the two switches understand — the same vocabulary `config.py` and
/// `resolve.py` accept. An unrecognised value is an error, not a truthy default.
const ON_VALUES: [&str; 4] = ["1", "true", "on", "yes"];
const OFF_VALUES: [&str; 4] = ["0", "false", "off", "no"];

fn env_flag(name: &str) -> Result<Option<bool>, String> {
    let text = match env_text(name) {
        Some(value) => value.to_lowercase(),
        None => return Ok(None),
    };
    if ON_VALUES.contains(&text.as_str()) {
        return Ok(Some(true));
    }
    if OFF_VALUES.contains(&text.as_str()) {
        return Ok(Some(false));
    }
    Err(format!(
        "{name} must be one of 1, true, on, yes, 0, false, off, no: {text:?}"
    ))
}

fn stored_flag(stored: &Value, key: &str) -> Result<bool, String> {
    match stored.get(key) {
        None => Ok(true),
        Some(Value::Bool(value)) => Ok(*value),
        Some(other) => Err(format!(
            "{key} in the desktop config is not true or false: {other}"
        )),
    }
}

/// The two surfaces the server can be told not to serve. Mirrors `resolve.Features`.
pub struct Features {
    pub web_ui: bool,
    pub subsonic: bool,
}

impl Features {
    fn argv(&self) -> Vec<&'static str> {
        let mut flags = Vec::new();
        if !self.web_ui {
            flags.push("--no-web-ui");
        }
        if !self.subsonic {
            flags.push("--no-subsonic");
        }
        flags
    }
}

/// Environment, then the file, then on — the same precedence the address has, and for the same
/// reason: the server reads those variables itself, so a file value under one could never win.
fn features(stored: &Value) -> Result<Features, String> {
    let web_ui = match env_flag("HARDLYLISTENING_WEB_UI")? {
        Some(value) => value,
        None => stored_flag(stored, "web_ui")?,
    };
    let subsonic = match env_flag("HARDLYLISTENING_SUBSONIC")? {
        Some(value) => value,
        None => stored_flag(stored, "subsonic")?,
    };
    Ok(Features { web_ui, subsonic })
}

fn home_dir() -> PathBuf {
    env_text("HOME")
        .or_else(|| env_text("USERPROFILE"))
        .map(PathBuf::from)
        .unwrap_or_default()
}

/// `<XDG_CONFIG_HOME>/autostart/hardlylistening-server.desktop`, with the same escape hatch the
/// rest of the shell has: an environment variable, so a test never reaches into a real config.
pub fn autostart_path() -> PathBuf {
    if let Some(declared) = env_text("HARDLYLISTENING_DESKTOP_AUTOSTART") {
        return PathBuf::from(declared);
    }
    let base = match env_text("XDG_CONFIG_HOME") {
        Some(config) => PathBuf::from(config),
        None => home_dir().join(".config"),
    };
    base.join(AUTOSTART_DIR).join(AUTOSTART_FILE)
}

/// Fields an environment variable pins. Writing them would change nothing, because
/// `host::address()` gives the environment precedence — so the write is refused and named.
fn locked_fields() -> Vec<&'static str> {
    let mut locked = Vec::new();
    for (field, name) in [
        ("host", "HARDLYLISTENING_HOST"),
        ("port", "HARDLYLISTENING_PORT"),
        ("web_ui", "HARDLYLISTENING_WEB_UI"),
        ("subsonic", "HARDLYLISTENING_SUBSONIC"),
    ] {
        if env_text(name).is_some() {
            locked.push(field);
        }
    }
    locked
}

fn refusal(field: &str, reason: &str) -> Value {
    json!({ "field": field, "reason": reason })
}

/// Everything the panel needs before anything is changed: the effective address (through
/// `host::address()`, so the environment still wins), the raw file, and the entry's presence.
#[tauri::command]
pub fn settings_read() -> Value {
    let path = host::config_path();
    let (stored, problem) = host::read_config(&path);
    let entry = autostart_path();
    let address = host::address();
    let (flags, feature_problem) = match features(&stored) {
        Ok(found) => (
            json!({ "web_ui": found.web_ui, "subsonic": found.subsonic }),
            String::new(),
        ),
        // A bad value in the file is reported and the defaults are shown, exactly as an
        // unreadable file is: a window that will not open is worse than one that says why.
        Err(error) => (json!({ "web_ui": true, "subsonic": true }), error),
    };
    let problem = [problem, feature_problem]
        .iter()
        .filter(|text| !text.is_empty())
        .cloned()
        .collect::<Vec<String>>()
        .join(" ");
    json!({
        "path": path.display().to_string(),
        "stored": stored,
        "locked": locked_fields(),
        "features": flags,
        "address": match &address {
            Ok(address) => json!({
                "host": address.host,
                "port": address.port,
                "source": address.source,
            }),
            Err(error) => json!({ "error": error }),
        },
        "autostart": entry.is_file(),
        "autostart_path": entry.display().to_string(),
        // `.desktop` entries are XDG: Linux and the BSDs. macOS and Windows would need their
        // own mechanism, which is not this card's, so the panel says so instead of pretending.
        "autostart_supported": cfg!(target_os = "linux"),
        "problem": problem,
    })
}

fn clean_host(value: &Value) -> Result<String, String> {
    let host = match value {
        Value::String(text) => text.trim().to_string(),
        _ => return Err("a bind address must be a string".to_string()),
    };
    if host.is_empty() {
        return Err("a bind address is required".to_string());
    }
    if host.chars().any(char::is_whitespace) {
        return Err(format!("a bind address cannot contain spaces: {host:?}"));
    }
    Ok(host)
}

fn parse_port(value: &Value, origin: &str) -> Result<u16, String> {
    let text = match value {
        Value::Number(number) => number.to_string(),
        Value::String(text) => text.trim().to_string(),
        _ => return Err(format!("{origin} is not a number")),
    };
    match text.parse::<u32>() {
        Ok(port) if (1..65536).contains(&port) => Ok(port as u16),
        Ok(_) => Err(format!("{origin} is out of range: {text}")),
        Err(_) => Err(format!("{origin} is not a number: {text}")),
    }
}

fn temp_beside(path: &Path) -> PathBuf {
    let mut name = path
        .file_name()
        .map(|name| name.to_os_string())
        .unwrap_or_default();
    name.push(".tmp");
    path.with_file_name(name)
}

/// Write beside the target and rename: a window that dies mid-write must not leave a half file
/// where the next start reads its address.
fn write_atomic(path: &Path, text: &str) -> Result<(), String> {
    if let Some(parent) = path.parent() {
        std::fs::create_dir_all(parent)
            .map_err(|error| format!("{}: {error}", parent.display()))?;
    }
    let temp = temp_beside(path);
    std::fs::write(&temp, text).map_err(|error| format!("{}: {error}", temp.display()))?;
    std::fs::rename(&temp, path).map_err(|error| {
        let _ = std::fs::remove_file(&temp);
        format!("{}: {error}", path.display())
    })
}

/// Write the four settings, preserving every key this shell does not own. An unreadable file is
/// copied to `<name>.bak` first: whatever a person hand-edited is theirs, not ours to destroy.
fn write_config(
    path: &Path,
    host: &str,
    port: u16,
    features: &Features,
) -> Result<Option<PathBuf>, String> {
    let (stored, problem) = host::read_config(path);
    let backup = if !problem.is_empty() && path.is_file() {
        let mut name = path
            .file_name()
            .map(|name| name.to_os_string())
            .unwrap_or_default();
        name.push(".bak");
        let backup = path.with_file_name(name);
        std::fs::copy(path, &backup).map_err(|error| format!("{}: {error}", backup.display()))?;
        Some(backup)
    } else {
        None
    };

    let mut object = match stored {
        Value::Object(map) => map,
        _ => Map::new(),
    };
    object.insert("host".to_string(), Value::String(host.to_string()));
    object.insert("port".to_string(), Value::Number(port.into()));
    object.insert("web_ui".to_string(), Value::Bool(features.web_ui));
    object.insert("subsonic".to_string(), Value::Bool(features.subsonic));
    let mut text = serde_json::to_string_pretty(&Value::Object(object))
        .map_err(|error| format!("the settings could not be serialised: {error}"))?;
    text.push('\n');
    write_atomic(path, &text)?;
    Ok(backup)
}

/// Quote for a `Desktop Entry` Exec key: `%` is a field code, and quoting needs backslashes.
fn exec_quote(value: &str) -> String {
    let text = value.replace('%', "%%");
    if text
        .chars()
        .any(|ch| matches!(ch, ' ' | '\t' | '"' | '\'' | '\\' | '$' | '`'))
    {
        let escaped = text
            .replace('\\', "\\\\")
            .replace('"', "\\\"")
            .replace('$', "\\$")
            .replace('`', "\\`");
        format!("\"{escaped}\"")
    } else {
        text
    }
}

/// The entry's body: the launcher, told to `run` on the address the window polls and to serve
/// the same two surfaces the window does.
pub fn desktop_entry(address: &host::Address, launcher: &Path, features: &Features) -> String {
    let mut flags = vec![
        "run".to_string(),
        "--host".to_string(),
        address.host.clone(),
        "--port".to_string(),
        address.port.to_string(),
    ];
    flags.extend(features.argv().iter().map(|flag| flag.to_string()));
    let command = format!(
        "{} {}",
        exec_quote(&launcher.display().to_string()),
        flags.join(" ")
    );
    format!(
        "[Desktop Entry]\n\
         Type=Application\n\
         Version=1.0\n\
         Name=HardlyListening server\n\
         Comment=Starts the HardlyListening server at sign-in, not the desktop window\n\
         Exec={command}\n\
         Terminal=false\n\
         NoDisplay=false\n\
         X-GNOME-Autostart-enabled=true\n"
    )
}

/// Install or remove the one entry this module names. `Terminal=false` is honest about the cost:
/// an autostarted server has no journal, no terminal and therefore no visible log.
pub fn set_autostart(
    enabled: bool,
    address: &host::Address,
    features: &Features,
    launcher: Option<&Path>,
) -> Result<&'static str, String> {
    let path = autostart_path();
    if !enabled {
        return match std::fs::remove_file(&path) {
            Ok(()) => Ok("removed"),
            Err(error) if error.kind() == std::io::ErrorKind::NotFound => Ok("unchanged"),
            Err(error) => Err(format!("{}: {error}", path.display())),
        };
    }
    let launcher = launcher.ok_or_else(|| {
        format!(
            "the server launcher ({}) was not found, so there is nothing to start at sign-in",
            host::sidecar_name()
        )
    })?;
    write_atomic(&path, &desktop_entry(address, launcher, features))?;
    #[cfg(unix)]
    {
        use std::os::unix::fs::PermissionsExt;

        // 0644: the entry is a file the session reads, not a program it executes.
        let _ = std::fs::set_permissions(&path, std::fs::Permissions::from_mode(0o644));
    }
    Ok("written")
}

/// Apply a patch of `host`/`port`/`scope`/`web_ui`/`subsonic` and, only when the choice is
/// explicit, the autostart entry. `autostart: null` is "no choice was made" and touches nothing.
#[tauri::command]
pub fn settings_write(
    app: tauri::AppHandle,
    patch: Value,
    autostart: Option<bool>,
) -> Result<Value, String> {
    let path = host::config_path();
    let current = host::address()?;
    let patch = patch.as_object().cloned().unwrap_or_default();
    let locked = locked_fields();
    // The stored switches are read from the file, env-first, so a write starts from what the
    // panel showed rather than from the defaults.
    let (stored, _) = host::read_config(&path);
    let current_features = features(&stored)?;

    let mut wanted_host = current.host.clone();
    let mut wanted_port = current.port;
    let mut wanted_web_ui = current_features.web_ui;
    let mut wanted_subsonic = current_features.subsonic;
    let mut explicit_host: Option<String> = None;
    let mut scope: Option<String> = None;
    let mut refused: Vec<Value> = Vec::new();

    for (field, value) in &patch {
        match field.as_str() {
            "scope" => match value.as_str() {
                Some("local") => scope = Some(LOCAL_HOST.to_string()),
                Some("lan") => scope = Some(ANY_HOST.to_string()),
                other => return Err(format!("unknown exposure: {other:?}")),
            },
            "host" => explicit_host = Some(clean_host(value)?),
            "port" => wanted_port = parse_port(value, "the port")?,
            "web_ui" => match value {
                Value::Bool(on) => wanted_web_ui = *on,
                _ => return Err(format!("web_ui is true or false, not {value}")),
            },
            "subsonic" => match value {
                Value::Bool(on) => wanted_subsonic = *on,
                _ => return Err(format!("subsonic is true or false, not {value}")),
            },
            "autostart" => refused.push(refusal(
                "autostart",
                "autostart is only changed by an explicit choice, never by a patch",
            )),
            other => refused.push(refusal(other, "not a setting this shell knows")),
        }
    }
    if let Some(host) = explicit_host.or(scope) {
        wanted_host = host;
    }

    if wanted_host != current.host && locked.contains(&"host") {
        refused.push(refusal(
            "host",
            "HARDLYLISTENING_HOST is set for this window and wins over the config file",
        ));
        wanted_host = current.host.clone();
    }
    if wanted_port != current.port && locked.contains(&"port") {
        refused.push(refusal(
            "port",
            "HARDLYLISTENING_PORT is set for this window and wins over the config file",
        ));
        wanted_port = current.port;
    }
    if wanted_web_ui != current_features.web_ui && locked.contains(&"web_ui") {
        refused.push(refusal(
            "web_ui",
            "HARDLYLISTENING_WEB_UI is set for this window and wins over the config file",
        ));
        wanted_web_ui = current_features.web_ui;
    }
    if wanted_subsonic != current_features.subsonic && locked.contains(&"subsonic") {
        refused.push(refusal(
            "subsonic",
            "HARDLYLISTENING_SUBSONIC is set for this window and wins over the config file",
        ));
        wanted_subsonic = current_features.subsonic;
    }

    let wanted_features = Features {
        web_ui: wanted_web_ui,
        subsonic: wanted_subsonic,
    };
    let mut written: Vec<&str> = Vec::new();
    let mut backup: Option<PathBuf> = None;
    if wanted_host != current.host
        || wanted_port != current.port
        || wanted_web_ui != current_features.web_ui
        || wanted_subsonic != current_features.subsonic
    {
        backup = write_config(&path, &wanted_host, wanted_port, &wanted_features)?;
        if wanted_host != current.host {
            written.push("host");
        }
        if wanted_port != current.port {
            written.push("port");
        }
        if wanted_web_ui != current_features.web_ui {
            written.push("web_ui");
        }
        if wanted_subsonic != current_features.subsonic {
            written.push("subsonic");
        }
    }

    let wanted = host::Address {
        host: wanted_host,
        port: wanted_port,
        source: "desktop-config",
    };
    let entry = match autostart {
        None => "unchanged",
        Some(enabled) => set_autostart(
            enabled,
            &wanted,
            &wanted_features,
            host::launcher(&app).as_deref(),
        )?,
    };
    let restart = !written.is_empty();

    Ok(json!({
        "saved": restart || entry != "unchanged",
        "settings": {
            "host": wanted.host,
            "port": wanted.port,
            "web_ui": wanted_features.web_ui,
            "subsonic": wanted_features.subsonic,
        },
        "written": written,
        "refused": refused,
        "autostart": entry,
        "restart_required": restart,
        "before": {
            "host": current.host,
            "port": current.port,
            "web_ui": current_features.web_ui,
            "subsonic": current_features.subsonic,
        },
        "config_path": path.display().to_string(),
        "backup": backup.map(|path| path.display().to_string()),
    }))
}
