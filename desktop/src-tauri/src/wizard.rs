// The first-run wizard's filesystem half (HOST-002).
//
// This is the Rust form of `desktop/host/setup.py`, which is the *tested* copy: the crate cannot
// be compiled on a machine without `webkit2gtk-4.1`, so every rule is proven in Python and
// mirrored here. `tests/test_host_wizard.py` reads this file and fails when a message, a limit or
// the account script drifts from the Python one — the same guard `test_desktop_host.py` applies to
// `host.rs` and `resolve.py`.
//
// Three rules, and the crate stays honest:
//
// 1. **The wizard never writes the database.** The administrator is created by running the
//    server's own `Security.create_user` + `Security.set_password` in a server process
//    (`ADMIN_SCRIPT`), so the email rule, the scrypt profile and "that email is already in use"
//    are the server's answers rather than a second opinion. The password reaches that process on
//    a pipe: never in argv, never in the state file, never in a window title.
// 2. **The state file is the resume point.** `setup_save` is atomic (temporary file, then a
//    rename), so a window killed mid-write reopens the step instead of reading half an object.
// 3. **Nothing here is a server dependency.** The only interface is the environment the packaged
//    installs already use, so a wizard-configured install can be reproduced by hand in systemd.
//
// Unverified on the machine this was written on: no compiler has seen this file. `rustfmt` proves
// it parses; `tests/test_host_wizard.py` and `tests/test_host_wizard.cjs` prove the decisions it
// renders.

use std::io::Write;
use std::path::{Path, PathBuf};
use std::process::{Command, Stdio};

use serde_json::{json, Value};

/// The shell's state directory, beside `host.json` (`host.rs` names the same one).
const CONFIG_DIR: &str = "hardlylistening-desktop";
const SETUP_FILE: &str = "setup.json";
const DB_NAME: &str = "hardlylistening.db";
const VERSION: u64 = 1;

/// The server's limits, repeated because the window imports no server module.
/// `security.Security` is the copy that enforces them; this is the one that answers before a
/// server exists.
const NAME_MAX: usize = 64;
const EMAIL_MAX: usize = 254;
const PASSWORD_MIN: usize = 10;
const PASSWORD_MAX: usize = 1024;

/// Relative to the person's home: the server's default media root (`db.DEFAULTS['root']`) and the
/// XDG data layout.
const MUSIC_SUFFIX: &str = "Music/HardlyListening";
const XDG_DATA_SUFFIX: &str = ".local/share";
const DATA_LEAF: &str = "hardlylistening";

const STEPS: [&str; 4] = ["music", "data", "name", "admin"];
const FIELDS: [&str; 4] = ["music_dir", "data_dir", "server_name", "admin_email"];

/// The server-side half of the account step, identical to `setup.ADMIN_SCRIPT`.
const ADMIN_SCRIPT: &str = r#"import json, sys
from pathlib import Path
from db import Store
from security import Security

payload = json.load(sys.stdin)
store = Store(Path(payload["db"]))
member_id = None
try:
    sec = Security()
    # Both answers are checked by the server's own validators *before* the member row exists:
    # `create_user` commits, so a refusal after it would leave a nameless administrator behind.
    email = sec.validate_email(payload["email"])
    password = sec.validate_password(payload["password"])
    sec.schema(store)
    with store.lock:
        taken = store.conn.execute(
            "SELECT 1 FROM member_credentials WHERE login_email=?", (email,)).fetchone()
    if taken:
        raise SystemExit("That email is already in use")
    member_id = sec.create_user(store, {"role": "admin"}, None, "admin")
    sec.set_password(store, member_id, password, email)
finally:
    store._projection_closed = True
    timer = getattr(store, "_projection_timer", None)
    if timer:
        timer.cancel()
    store.close()
print(json.dumps({"member_id": member_id, "email": email}))
"#;

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

/// `setup.json`, beside the shell's `host.json` and behind the same override: when
/// `HARDLYLISTENING_DESKTOP_CONFIG` names the config *file*, the wizard's state is its sibling.
fn state_path() -> PathBuf {
    if let Some(declared) = env_text("HARDLYLISTENING_DESKTOP_CONFIG") {
        let path = PathBuf::from(declared);
        return match path.parent() {
            Some(parent) => parent.join(SETUP_FILE),
            None => PathBuf::from(SETUP_FILE),
        };
    }
    let base = match env_text("XDG_STATE_HOME") {
        Some(state) => PathBuf::from(state),
        None => home_dir().join(".local").join("state"),
    };
    base.join(CONFIG_DIR).join(SETUP_FILE)
}

fn defaults() -> Value {
    let home = home_dir();
    json!({
        "music_dir": home.join(MUSIC_SUFFIX).display().to_string(),
        "data_dir": home.join(XDG_DATA_SUFFIX).join(DATA_LEAF).display().to_string(),
    })
}

/// The saved answers, plus a plain-language problem. A malformed file is reported and ignored,
/// never fatal: the window then asks the four questions instead of refusing to open.
fn read_state(path: &Path) -> (Value, String) {
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
        Ok(value) if value.is_object() => {
            if value.get("version").and_then(Value::as_u64) != Some(VERSION) {
                return (
                    Value::Null,
                    format!(
                        "{} was written by another version of the wizard",
                        path.display()
                    ),
                );
            }
            (value, String::new())
        }
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

/// Atomic: a temporary file in the same directory, 0600 from the first byte, then a rename. The
/// file holds a person's paths and login address, so nobody else needs to read it.
fn write_state(path: &Path, state: &Value) -> Result<(), String> {
    if let Some(parent) = path.parent() {
        if !parent.as_os_str().is_empty() {
            std::fs::create_dir_all(parent)
                .map_err(|error| format!("{}: {error}", parent.display()))?;
            set_mode(parent, 0o700);
        }
    }
    let text = serde_json::to_string_pretty(state).map_err(|error| error.to_string())? + "\n";
    let temporary = path.with_extension("json.tmp");
    std::fs::write(&temporary, text.as_bytes())
        .map_err(|error| format!("{}: {error}", temporary.display()))?;
    set_mode(&temporary, 0o600);
    match std::fs::rename(&temporary, path) {
        Ok(()) => Ok(()),
        Err(error) => {
            let _ = std::fs::remove_file(&temporary);
            Err(format!("{}: {error}", path.display()))
        }
    }
}

#[cfg(unix)]
fn set_mode(path: &Path, mode: u32) {
    use std::os::unix::fs::PermissionsExt;

    let _ = std::fs::set_permissions(path, std::fs::Permissions::from_mode(mode));
}

#[cfg(not(unix))]
fn set_mode(_path: &Path, _mode: u32) {}

/// Whether this directory may be written to. The owner bit is the check `os.access(W_OK)` makes
/// for the person running the window; a stricter answer would need a probe write, and a validator
/// that writes is not a validator.
#[cfg(unix)]
fn writable(path: &Path) -> bool {
    use std::os::unix::fs::PermissionsExt;

    match std::fs::metadata(path) {
        Ok(meta) => meta.permissions().mode() & 0o200 != 0,
        Err(_) => false,
    }
}

#[cfg(not(unix))]
fn writable(path: &Path) -> bool {
    match std::fs::metadata(path) {
        Ok(meta) => !meta.permissions().readonly(),
        Err(_) => false,
    }
}

fn readable(path: &Path) -> bool {
    match std::fs::read_dir(path) {
        Ok(mut entries) => {
            let _ = entries.next();
            true
        }
        Err(_) => false,
    }
}

/// One answer, one sentence. The wording is part of the contract: `setup.MESSAGES` lists every
/// fragment and the test refuses to let this file disagree with it.
fn validate(field: &str, raw: &str) -> Result<String, String> {
    let text = raw.trim();
    match field {
        "music_dir" => {
            if text.is_empty() {
                return Err("Choose a folder for your music".to_string());
            }
            let path = expand(text);
            if !path.exists() {
                return Err(format!("{} does not exist", path.display()));
            }
            if !path.is_dir() {
                return Err(format!("{} is not a folder", path.display()));
            }
            if !readable(&path) {
                return Err(format!("{} cannot be read", path.display()));
            }
            Ok(path.display().to_string())
        }
        "data_dir" => {
            if text.is_empty() {
                return Err("Choose a folder for the server data".to_string());
            }
            let path = expand(text);
            if path.exists() {
                if !path.is_dir() {
                    return Err(format!("{} is a file, not a folder", path.display()));
                }
                if !writable(&path) {
                    return Err(format!("{} is not writable", path.display()));
                }
                return Ok(path.display().to_string());
            }
            // Same rule as `cli._creatable`: a packaged install's data directory does not exist
            // before the first start, so the honest question is whether it *could* be made.
            let mut ancestor = path.clone();
            while !ancestor.exists() {
                match ancestor.parent() {
                    Some(parent) if parent != ancestor => ancestor = parent.to_path_buf(),
                    _ => break,
                }
            }
            if !ancestor.is_dir() {
                return Err(format!(
                    "{} is missing and {} is not a folder",
                    path.display(),
                    ancestor.display()
                ));
            }
            if !writable(&ancestor) {
                return Err(format!(
                    "{} is missing and {} is not writable",
                    path.display(),
                    ancestor.display()
                ));
            }
            Ok(path.display().to_string())
        }
        "server_name" => {
            if text.is_empty() || text.chars().count() > NAME_MAX {
                return Err(format!("A server name is 1-{NAME_MAX} characters"));
            }
            if text.chars().any(char::is_control) {
                return Err("A server name cannot contain control characters".to_string());
            }
            Ok(text.to_string())
        }
        "admin_email" => {
            let email = text.to_lowercase();
            if !valid_email(&email) {
                return Err("Enter an email address".to_string());
            }
            Ok(email)
        }
        "password" => {
            // Not trimmed: leading and trailing spaces are part of a password.
            if raw.chars().count() < PASSWORD_MIN {
                return Err(format!(
                    "Password must be at least {PASSWORD_MIN} characters"
                ));
            }
            if raw.chars().count() > PASSWORD_MAX {
                return Err("Password is too long".to_string());
            }
            Ok(raw.to_string())
        }
        other => Err(format!("unknown field {other}")),
    }
}

fn valid_email(email: &str) -> bool {
    let characters = email.chars().count();
    if characters < 5 || characters > EMAIL_MAX || email.matches('@').count() != 1 {
        return false;
    }
    if email.chars().any(char::is_whitespace) {
        return false;
    }
    match email.split_once('@') {
        Some((local, domain)) => {
            !local.is_empty()
                && domain.contains('.')
                && !domain.starts_with('.')
                && !domain.ends_with('.')
        }
        None => false,
    }
}

fn expand(text: &str) -> PathBuf {
    match text.strip_prefix("~/") {
        Some(rest) => home_dir().join(rest),
        None => PathBuf::from(text),
    }
}

/// Validate the answers present in a state object, returning the normalized copy. An empty field
/// is "not answered yet" rather than an error: the state after one step holds one answer.
fn validate_state(state: &Value) -> Result<Value, (String, String)> {
    let object = match state.as_object() {
        Some(object) => object,
        None => {
            return Err((
                String::new(),
                "the wizard state must be an object".to_string(),
            ))
        }
    };
    if object.contains_key("password") {
        return Err((
            String::new(),
            "a password is never written to the config".to_string(),
        ));
    }
    if object.get("version").and_then(Value::as_u64) != Some(VERSION) {
        return Err((
            String::new(),
            "that config was written by another version of the wizard".to_string(),
        ));
    }
    let mut out = object.clone();
    for field in FIELDS {
        let raw = object
            .get(field)
            .and_then(Value::as_str)
            .unwrap_or("")
            .trim()
            .to_string();
        if raw.is_empty() {
            continue;
        }
        match validate(field, &raw) {
            Ok(value) => {
                out.insert(field.to_string(), Value::String(value));
            }
            Err(detail) => return Err((field.to_string(), detail)),
        }
    }
    let completed = object
        .get("completed")
        .and_then(Value::as_array)
        .map(|steps| {
            let mut kept: Vec<Value> = Vec::new();
            for step in STEPS {
                if steps.iter().any(|value| value.as_str() == Some(step)) {
                    kept.push(Value::String(step.to_string()));
                }
            }
            kept
        })
        .unwrap_or_default();
    out.insert("completed".to_string(), Value::Array(completed));
    Ok(Value::Object(out))
}

fn data_dir_of(state: &Value) -> Option<PathBuf> {
    state
        .get("data_dir")
        .and_then(Value::as_str)
        .map(str::trim)
        .filter(|value| !value.is_empty())
        .map(expand)
}

/// One folder dialog, from whatever the desktop already has. No plugin: `tauri-plugin-dialog`
/// would add a dependency and a capability file for the same three lines, and every desktop this
/// targets ships one of these helpers. A miss is reported, never guessed around — the field is
/// still typeable.
#[cfg(target_os = "linux")]
fn pick_folder(title: &str) -> Option<String> {
    let candidates: [(&str, Vec<&str>); 2] = [
        (
            "zenity",
            vec![
                "--file-selection",
                "--directory",
                "--title",
                title,
                "--filename",
                "~/",
            ],
        ),
        (
            "kdialog",
            vec!["--getexistingdirectory", "~", "--title", title],
        ),
    ];
    for (program, args) in candidates {
        if let Some(path) = run_picker(program, &args) {
            return Some(path);
        }
    }
    None
}

#[cfg(target_os = "macos")]
fn pick_folder(title: &str) -> Option<String> {
    let script = format!("POSIX path of (choose folder with prompt \"{title}\")");
    run_picker("osascript", &["-e", &script])
}

#[cfg(target_os = "windows")]
fn pick_folder(_title: &str) -> Option<String> {
    let script = "Add-Type -AssemblyName System.Windows.Forms; $f = New-Object System.Windows.Forms.FolderBrowserDialog; if ($f.ShowDialog() -eq 'OK') { $f.SelectedPath }";
    run_picker("powershell", &["-NoProfile", "-Command", script])
}

#[cfg(not(any(target_os = "linux", target_os = "macos", target_os = "windows")))]
fn pick_folder(_title: &str) -> Option<String> {
    None
}

fn run_picker(program: &str, args: &[&str]) -> Option<String> {
    let output = Command::new(program)
        .args(args)
        .stdin(Stdio::null())
        .stdout(Stdio::piped())
        .stderr(Stdio::null())
        .output()
        .ok()?;
    if !output.status.success() {
        return None;
    }
    let path = String::from_utf8_lossy(&output.stdout).trim().to_string();
    if path.is_empty() {
        None
    } else {
        Some(path)
    }
}

fn app_root(app: &tauri::AppHandle) -> Option<PathBuf> {
    if let Some(launcher) = crate::host::launcher(app) {
        if let Some(root) = launcher.parent().and_then(Path::parent) {
            return Some(root.to_path_buf());
        }
    }
    std::env::current_dir().ok()
}

fn python_command() -> String {
    env_text("HARDLYLISTENING_PYTHON").unwrap_or_else(|| "python3".to_string())
}

/// The environment a start needs for *this* install, read from `setup.json` on every start.
/// `lib.rs::server_start` applies it before spawning, so a run of the wizard takes effect without
/// reopening the window. Empty when there is no usable state: a start then behaves exactly as it
/// did before this card, and the server's own defaults decide.
pub fn server_env() -> Vec<(&'static str, String)> {
    let (state, _) = read_state(&state_path());
    let field = |name: &str| -> String {
        state
            .get(name)
            .and_then(Value::as_str)
            .unwrap_or_default()
            .trim()
            .to_string()
    };
    let data = field("data_dir");
    if data.is_empty() {
        return Vec::new();
    }
    let db = Path::new(&data).join(DB_NAME).display().to_string();
    let mut env = vec![
        ("HARDLYLISTENING_DATA_DIR", data),
        ("HARDLYLISTENING_DB", db),
    ];
    let music = field("music_dir");
    if !music.is_empty() {
        env.push(("HARDLYLISTENING_MUSIC_DIR", music));
    }
    env
}

// ---- the commands the window calls ---------------------------------------

/// Everything the wizard needs to decide whether to appear, and what to pre-fill.
#[tauri::command]
pub fn setup_state(_app: tauri::AppHandle) -> Value {
    let path = state_path();
    let (state, problem) = read_state(&path);
    json!({
        "path": path.display().to_string(),
        "state": state,
        "problem": problem,
        "home": home_dir().display().to_string(),
        "defaults": defaults(),
    })
}

/// A folder dialog, or a sentence explaining why there was none.
#[tauri::command]
pub fn setup_pick_folder(kind: String) -> Value {
    let title = if kind == "data" {
        "Where should the server keep its files?"
    } else {
        "Where is your music?"
    };
    match pick_folder(title) {
        Some(path) => json!({ "path": path, "detail": "" }),
        None => json!({
            "path": Value::Null,
            "detail": "No folder dialog was available; type the path instead.",
        }),
    }
}

/// Validate the answers present and write them. Refusals name the field, so the window can put the
/// sentence under the right input.
#[tauri::command]
pub fn setup_save(_app: tauri::AppHandle, state: Value) -> Value {
    let checked = match validate_state(&state) {
        Ok(checked) => checked,
        Err((field, detail)) => {
            return json!({ "saved": false, "field": field, "detail": detail });
        }
    };
    // The data folder is the one directory the wizard creates: the account step has to put a
    // database somewhere, and a first start must not fail on a missing parent.
    let mut detail = String::new();
    if let Some(data_dir) = data_dir_of(&checked) {
        if !data_dir.exists() {
            if let Err(error) = std::fs::create_dir_all(&data_dir) {
                return json!({
                    "saved": false,
                    "field": "data_dir",
                    "detail": format!("{}: {error}", data_dir.display()),
                });
            }
            set_mode(&data_dir, 0o700);
            detail = format!("{} was created.", data_dir.display());
        }
    }
    let path = state_path();
    if let Err(error) = write_state(&path, &checked) {
        return json!({ "saved": false, "field": "", "detail": error });
    }
    json!({
        "saved": true,
        "path": path.display().to_string(),
        "field": "",
        "detail": detail,
    })
}

/// Create the administrator with the server's own account code. The password arrives in the
/// payload, travels on the child's stdin and is not kept here.
#[tauri::command]
pub fn setup_admin(
    app: tauri::AppHandle,
    email: String,
    password: String,
    data_dir: String,
) -> Value {
    let dir = match validate("data_dir", &data_dir) {
        Ok(dir) => PathBuf::from(dir),
        Err(detail) => {
            return json!({ "created": false, "field": "data_dir", "detail": detail });
        }
    };
    let db = dir.join(DB_NAME);
    let root = match app_root(&app) {
        Some(root) => root,
        None => {
            return json!({
                "created": false,
                "field": "",
                "detail": "the server's application folder was not found",
            })
        }
    };
    let payload = match serde_json::to_string(&json!({
        "db": db.display().to_string(),
        "email": email,
        "password": password,
    })) {
        Ok(payload) => payload,
        Err(error) => {
            return json!({ "created": false, "field": "", "detail": error.to_string() });
        }
    };

    let mut command = Command::new(python_command());
    command
        .arg("-c")
        .arg(ADMIN_SCRIPT)
        .current_dir(&root)
        .env("HARDLYLISTENING_DATA_DIR", &dir)
        .env("HARDLYLISTENING_DB", &db)
        .stdin(Stdio::piped())
        .stdout(Stdio::piped())
        .stderr(Stdio::piped());
    let mut child = match command.spawn() {
        Ok(child) => child,
        Err(error) => {
            return json!({
                "created": false,
                "field": "",
                "detail": format!("{}: {error}", python_command()),
            })
        }
    };
    if let Some(mut stdin) = child.stdin.take() {
        let _ = stdin.write_all(payload.as_bytes());
    }
    let output = match child.wait_with_output() {
        Ok(output) => output,
        Err(error) => {
            return json!({ "created": false, "field": "", "detail": error.to_string() });
        }
    };
    let stdout = String::from_utf8_lossy(&output.stdout);
    match serde_json::from_str::<Value>(stdout.trim()) {
        Ok(answer) if output.status.success() => json!({
            "created": true,
            "member_id": answer.get("member_id").cloned().unwrap_or(Value::Null),
            "email": answer.get("email").cloned().unwrap_or(Value::Null),
            "field": "",
            "detail": "",
        }),
        _ => {
            let stderr = String::from_utf8_lossy(&output.stderr);
            let last = stderr
                .lines()
                .rev()
                .find(|line| !line.trim().is_empty())
                .unwrap_or("the server refused the account")
                .trim()
                .to_string();
            json!({ "created": false, "field": "password", "detail": last })
        }
    }
}
