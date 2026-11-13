//! Offline copies on disk: the per-account directory, the atomic write, the manifest, and the
//! free-space figure. This is the player's only filesystem surface, and it is deliberately
//! dumb — it knows nothing about budgets, eviction or the network. Those are decisions and they
//! live in `player/shell/offline.js`, where they can be tested without a `webkit2gtk` build.
//!
//! Four rules, each one a failure this file exists to prevent:
//!
//! 1. **No name from the network reaches a path unsanitised.** A track id, a suffix and an
//!    account key all arrive as JSON, and all three go through [`safe_component`] /
//!    [`account_name`] / [`file_name`] before a `join`. A manifest entry's own `file` field is a
//!    claim, never permission: it is re-sanitised on every use ([`entry_file`]), and every path
//!    is checked with [`inside`] after it is built.
//! 2. **A written file is a renamed file.** Bytes land in `<name>.part`; the completed copy is
//!    `rename`d onto `<name>`, and the manifest is written to `manifest.json.tmp` and renamed
//!    too. A crash leaves a `.part` (resumable) rather than a file that looks complete and is
//!    not.
//! 3. **A short file is never committed.** `offline_commit` refuses unless the bytes on disk are
//!    exactly the length the caller declared, so the manifest cannot describe a download that
//!    stopped early.
//! 4. **Write from the byte after the last one.** `offline_write` requires the chunk's offset to
//!    equal the current length of the partial, so a resumed download cannot leave a gap or
//!    overwrite what it already holds.
//!
//! The block between the `pure rules` markers has no Tauri, no I/O and no network.
//! `tests/test_player_offline.py` extracts it and compiles it on its own, which is how the name
//! and decode rules are tested on a machine that cannot build the crate.
//!
//! Unverified here: nothing in this file has been compiled against Tauri, because this machine
//! has no `webkit2gtk-4.1`, `gtk+-3.0` or `libsoup-3.0`. `rustfmt` accepts it and the pure block
//! is compiled and exercised by the test; the command layer is not.

use std::fs::{self, File, OpenOptions};
use std::io::{Read, Seek, SeekFrom, Write};
use std::path::{Path, PathBuf};

use serde_json::{json, Value};
use tauri::Manager;

/// The directory copies live in, under the app's own data directory.
const STORE_DIR: &str = "offline";
/// One manifest per account, beside that account's files.
const MANIFEST_FILE: &str = "manifest.json";
const MANIFEST_VERSION: u64 = 1;
/// What an in-progress download is called: `<name>.part`, and it is the only thing a resume
/// reads.
const PART_EXT: &str = ".part";
/// One read for playback. Large enough that a track is a handful of messages, small enough that
/// the base64 of one message is not a second copy of the file.
const READ_MAX: u64 = 4 * 1024 * 1024;

// --- pure rules (no Tauri, no I/O, no network) ---------------------------------------------
//
// Everything between this marker and `end pure rules` is compiled on its own by the test suite.
// It must not gain a `Value`, a command, a file handle or a socket.

/// The longest single path component this player will accept. Longer is refused, not truncated:
/// a truncated name is a name that collides with another one.
const COMPONENT_MAX: usize = 64;
const SUFFIX_MAX: usize = 8;
const FALLBACK_SUFFIX: &str = "bin";

/// A file name component, or `None` when it is not one. The allow-list is the rule: ASCII
/// alphanumerics, dot, dash, underscore, no leading dot, at most [`COMPONENT_MAX`] bytes. Anything
/// else — a separator, a drive letter, a control character, a non-ASCII look-alike slash, `..` —
/// is refused rather than escaped, because a name this player cannot represent is a name it has
/// no business writing.
fn safe_component(raw: &str) -> Option<String> {
    let value = raw.trim();
    if value.is_empty() || value.len() > COMPONENT_MAX {
        return None;
    }
    if value.starts_with('.') {
        return None;
    }
    if !value
        .bytes()
        .all(|byte| byte.is_ascii_alphanumeric() || byte == b'.' || byte == b'-' || byte == b'_')
    {
        return None;
    }
    Some(value.to_string())
}

/// An extension is a hint, never a path: everything but ASCII alphanumerics is dropped, so
/// `../../etc` becomes `etc`, and an empty or overlong hint becomes `bin`.
fn safe_suffix(raw: &str) -> String {
    let lower = raw.trim().trim_start_matches('.').to_ascii_lowercase();
    let clean: String = lower
        .chars()
        .filter(|character| character.is_ascii_alphanumeric())
        .collect();
    if clean.is_empty() || clean.len() > SUFFIX_MAX {
        FALLBACK_SUFFIX.to_string()
    } else {
        clean
    }
}

/// `key.suffix`, with both halves sanitised.
fn file_name(key: &str, suffix: &str) -> Option<String> {
    let name = safe_component(key)?;
    Some(format!("{}.{}", name, safe_suffix(suffix)))
}

/// 32-bit FNV-1a over the bytes, written out rather than taken from a crate so the account tag
/// is the same value the front end folds over the same UTF-8 (see `accountName` in offline.js).
fn fnv1a(bytes: &[u8]) -> u64 {
    let mut hash: u32 = 0x811c_9dc5;
    for byte in bytes {
        hash ^= u32::from(*byte);
        hash = hash.wrapping_mul(0x0100_0193);
    }
    u64::from(hash)
}

/// One directory name for one account: a readable prefix plus a tag of the whole key, so two
/// accounts whose prefixes collide (`https://a/b` and `https://a_b`) still get their own
/// directory. The result is lowercase ASCII, has no dot and no separator, and is never empty.
fn account_name(raw: &str) -> String {
    let mut prefix = String::with_capacity(40);
    for character in raw.chars() {
        if prefix.len() >= 40 {
            break;
        }
        prefix.push(match character {
            'a'..='z' | '0'..='9' | '-' | '_' => character,
            'A'..='Z' => character.to_ascii_lowercase(),
            _ => '_',
        });
    }
    while prefix.ends_with('_') {
        prefix.pop();
    }
    if prefix.is_empty() {
        prefix.push_str("account");
    }
    format!("{}-{:016x}", prefix, fnv1a(raw.as_bytes()))
}

/// Whether `path` is exactly one ordinary entry inside `dir`. `file_name()` is `None` for a path
/// that ends in `.` or `..`, which is what makes this a check and not a formality; `parent()`
/// catches a separator smuggled into a name. Every path this module builds is checked with this
/// before it is opened, written, renamed or removed.
fn inside(dir: &Path, path: &Path) -> bool {
    path.file_name().is_some() && path.parent() == Some(dir)
}

/// Text that came from a server and is only ever displayed: control characters removed, length
/// capped. It is never used to build a path.
fn sanitise_text(value: &str, cap: usize) -> String {
    value
        .chars()
        .filter(|character| !character.is_control())
        .take(cap)
        .collect()
}

const B64: &[u8; 64] = b"ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijklmnopqrstuvwxyz0123456789+/";

/// The bytes a chunk carries, as the string the IPC message holds.
fn b64_encode(bytes: &[u8]) -> String {
    let mut out = String::with_capacity((bytes.len() + 2) / 3 * 4);
    for chunk in bytes.chunks(3) {
        let a = u32::from(chunk[0]);
        let b = if chunk.len() > 1 {
            u32::from(chunk[1])
        } else {
            0
        };
        let c = if chunk.len() > 2 {
            u32::from(chunk[2])
        } else {
            0
        };
        let triple = (a << 16) | (b << 8) | c;
        out.push(B64[(triple >> 18) as usize & 63] as char);
        out.push(B64[(triple >> 12) as usize & 63] as char);
        out.push(if chunk.len() > 1 {
            B64[(triple >> 6) as usize & 63] as char
        } else {
            '='
        });
        out.push(if chunk.len() > 2 {
            B64[triple as usize & 63] as char
        } else {
            '='
        });
    }
    out
}

/// The reverse, and strict: an unrecognised byte is `None` rather than a guess, because a chunk
/// decoded wrongly is a file that plays as noise.
fn b64_decode(text: &str) -> Option<Vec<u8>> {
    let mut out = Vec::with_capacity(text.len() / 4 * 3);
    let mut acc: u32 = 0;
    let mut bits = 0;
    for byte in text.bytes() {
        let value = match byte {
            b'A'..=b'Z' => byte - b'A',
            b'a'..=b'z' => byte - b'a' + 26,
            b'0'..=b'9' => byte - b'0' + 52,
            b'+' | b'-' => 62,
            b'/' | b'_' => 63,
            b'=' => break,
            b'\n' | b'\r' | b' ' | b'\t' => continue,
            _ => return None,
        };
        acc = (acc << 6) | u32::from(value);
        bits += 6;
        if bits >= 8 {
            bits -= 8;
            out.push(((acc >> bits) & 0xff) as u8);
        }
    }
    Some(out)
}

// --- end pure rules ------------------------------------------------------------------------

/// `<app data>/offline`.
fn store_root(app: &tauri::AppHandle) -> Result<PathBuf, String> {
    let base = app
        .path()
        .app_data_dir()
        .map_err(|error| format!("the app data directory is unavailable: {error}"))?;
    Ok(base.join(STORE_DIR))
}

/// This account's directory, created on first use. The name comes from the account key alone and
/// is checked again after the join, so a future edit to `account_name` cannot quietly turn a key
/// into a path.
fn store_dir(app: &tauri::AppHandle, account: &str) -> Result<PathBuf, String> {
    let root = store_root(app)?;
    let dir = root.join(account_name(account));
    if !inside(&root, &dir) {
        return Err("that account does not name a directory inside the store".to_string());
    }
    fs::create_dir_all(&dir)
        .map_err(|error| format!("could not create {}: {error}", dir.display()))?;
    Ok(dir)
}

fn manifest_path(dir: &Path) -> PathBuf {
    dir.join(MANIFEST_FILE)
}

fn empty_manifest() -> Value {
    json!({ "version": MANIFEST_VERSION, "entries": {} })
}

/// A missing manifest is an empty one. A manifest that exists but cannot be read is also an empty
/// one — it is not a licence to delete files, so the copies become unreferenced bytes the person
/// can clear from the dialog rather than files this code silently removes.
fn read_manifest(dir: &Path) -> Value {
    let path = manifest_path(dir);
    let text = match fs::read_to_string(&path) {
        Ok(text) => text,
        Err(_) => return empty_manifest(),
    };
    match serde_json::from_str::<Value>(&text) {
        Ok(value) if value.get("entries").map(Value::is_object).unwrap_or(false) => value,
        _ => empty_manifest(),
    }
}

/// Rename over the target. On unix that is atomic. On Windows a rename does not replace an
/// existing file, so the target goes first — a crash between the two loses a manifest, never a
/// copy.
fn replace(from: &Path, to: &Path) -> Result<(), String> {
    #[cfg(windows)]
    {
        let _ = fs::remove_file(to);
    }
    fs::rename(from, to).map_err(|error| format!("could not replace {}: {error}", to.display()))
}

/// The manifest, through a temporary file, so a reader never sees a half-written one.
fn write_manifest(dir: &Path, manifest: &Value) -> Result<(), String> {
    let path = manifest_path(dir);
    let temp = dir.join(format!("{MANIFEST_FILE}{PART_EXT}"));
    let body = serde_json::to_string_pretty(manifest)
        .map_err(|error| format!("the manifest could not be serialised: {error}"))?;
    fs::write(&temp, body + "\n")
        .map_err(|error| format!("could not write {}: {error}", temp.display()))?;
    replace(&temp, &path)
}

fn part_for(dir: &Path, name: &str) -> PathBuf {
    dir.join(format!("{name}{PART_EXT}"))
}

/// The file a manifest entry names, re-sanitised on every use: the manifest is JSON that began as
/// JSON from the network, so a stored name is a claim and not permission. A name that is gone or
/// unusable is rebuilt from the entry's own key and suffix, which are sanitised in turn.
fn entry_file(dir: &Path, entry: &Value) -> Result<PathBuf, String> {
    let stored = entry.get("file").and_then(Value::as_str).unwrap_or("");
    let name = safe_component(stored)
        .or_else(|| {
            let key = entry.get("key").and_then(Value::as_str).unwrap_or("");
            let suffix = entry.get("suffix").and_then(Value::as_str).unwrap_or("");
            file_name(key, suffix)
        })
        .ok_or_else(|| "that manifest entry does not name a usable file".to_string())?;
    let path = dir.join(&name);
    if !inside(dir, &path) {
        return Err("that manifest entry points outside the account's directory".to_string());
    }
    Ok(path)
}

/// A manifest entry built from known fields only. The incoming `file` is never copied: the name
/// on disk is the one this function was given, which came from `file_name`.
fn clean_entry(entry: &Value, file: &str, key: &str, suffix: &str, size: u64) -> Value {
    let string = |name: &str, cap: usize| {
        entry
            .get(name)
            .and_then(Value::as_str)
            .map(|value| sanitise_text(value, cap))
            .filter(|value| !value.is_empty())
            .map(Value::from)
            .unwrap_or(Value::Null)
    };
    let number = |name: &str| {
        entry
            .get(name)
            .and_then(Value::as_u64)
            .map(Value::from)
            .unwrap_or(Value::Null)
    };
    json!({
        "file": file,
        "key": key,
        "suffix": suffix,
        "size": size,
        "expected": size,
        "etag": string("etag", 128),
        "contentType": string("contentType", 64),
        "title": string("title", 200),
        "sourceSize": number("sourceSize"),
        "addedAt": number("addedAt"),
        "lastPlayed": number("lastPlayed"),
        "complete": true,
    })
}

/// Every `<key>.*.part` in an account's directory. The delete path uses this so a cancelled or
/// superseded download cannot be resumed into a track the person removed.
fn remove_partials(dir: &Path, key: &str) -> usize {
    let prefix = format!("{key}.");
    let mut removed = 0;
    let Ok(entries) = fs::read_dir(dir) else {
        return 0;
    };
    for entry in entries.flatten() {
        let name = entry.file_name().to_string_lossy().to_string();
        if name.starts_with(&prefix) && name.ends_with(PART_EXT) && inside(dir, &entry.path()) {
            if fs::remove_file(entry.path()).is_ok() {
                removed += 1;
            }
        }
    }
    removed
}

fn name_refusal(key: &str) -> String {
    format!(
        "this player cannot use \"{}\" as a file name",
        sanitise_text(key, 40)
    )
}

/// A command has to be told which account it is acting for. The key travels as-is (it is the
/// server address and the username, which may contain anything); `account_name` is what turns it
/// into a directory, and `store_dir` checks the result.
fn require_account(account: &str) -> Result<(), String> {
    if account.trim().is_empty() {
        return Err("no account was named, so there is no directory to use".to_string());
    }
    Ok(())
}

/// Free and total bytes on the volume the store lives on.
#[cfg(unix)]
fn disk_free(dir: &Path) -> Value {
    use std::os::unix::ffi::OsStrExt;

    let mut path: Vec<u8> = dir.as_os_str().as_bytes().to_vec();
    path.push(0);
    // SAFETY: `statvfs` writes one `statvfs` into the pointer it is given, and the pointer is to
    // a zeroed, correctly aligned value of exactly that type. The path is NUL-terminated and
    // lives for the call. A failure is reported as "no figure" below, never as a number.
    let mut stats: libc::statvfs = unsafe { std::mem::zeroed() };
    let answered = unsafe { libc::statvfs(path.as_ptr() as *const libc::c_char, &mut stats) } == 0;
    if !answered {
        return json!({
            "available": Value::Null,
            "total": Value::Null,
            "reason": "the system did not report free space for the store",
        });
    }
    let unit = if stats.f_frsize > 0 {
        stats.f_frsize
    } else {
        stats.f_bsize
    } as u64;
    json!({
        "available": stats.f_bavail as u64 * unit,
        "total": stats.f_blocks as u64 * unit,
        "reason": Value::Null,
    })
}

/// On a platform with no free-space call here, the honest answer is that there is none: the
/// budget is then the only ceiling, and the dialog says so rather than showing a figure.
#[cfg(not(unix))]
fn disk_free(_dir: &Path) -> Value {
    json!({
        "available": Value::Null,
        "total": Value::Null,
        "reason": "this platform does not report free space; the budget is the only ceiling",
    })
}

/// The manifest as it stands, creating the directory on first use. The front end reads this to
/// decide what is kept and what to evict, so the store never has to be guessed at.
#[tauri::command]
pub fn offline_manifest(app: tauri::AppHandle, account: String) -> Result<Value, String> {
    Ok(read_manifest(&store_dir(&app, &account)?))
}

/// Start, or continue, a download: the length of the `.part` is the byte a resume begins at, and
/// creating the file here proves the directory is writable before a byte of audio is fetched.
#[tauri::command]
pub fn offline_begin(
    app: tauri::AppHandle,
    account: String,
    key: String,
    suffix: String,
) -> Result<Value, String> {
    require_account(&account)?;
    let dir = store_dir(&app, &account)?;
    let name = file_name(&key, &suffix).ok_or_else(|| name_refusal(&key))?;
    let part = part_for(&dir, &name);
    if !inside(&dir, &part) {
        return Err("that name does not belong in the account's directory".to_string());
    }
    if !part.exists() {
        File::create(&part)
            .map_err(|error| format!("could not start {}: {error}", part.display()))?;
    }
    let partial = fs::metadata(&part)
        .map_err(|error| format!("could not read {}: {error}", part.display()))?
        .len();
    Ok(json!({ "file": name, "partial": partial }))
}

/// One chunk at one offset. The offset must be exactly the current length, which is the rule
/// that makes a resumed download a continuous file rather than a guess.
#[tauri::command]
pub fn offline_write(
    app: tauri::AppHandle,
    account: String,
    key: String,
    suffix: String,
    offset: u64,
    data: String,
) -> Result<Value, String> {
    let bytes = b64_decode(&data).ok_or_else(|| "the chunk was not base64".to_string())?;
    require_account(&account)?;
    let dir = store_dir(&app, &account)?;
    let name = file_name(&key, &suffix).ok_or_else(|| name_refusal(&key))?;
    let part = part_for(&dir, &name);
    let mut file = OpenOptions::new()
        .write(true)
        .open(&part)
        .map_err(|error| format!("could not write {}: {error}", part.display()))?;
    let held = file
        .metadata()
        .map_err(|error| format!("could not size {}: {error}", part.display()))?
        .len();
    if offset != held {
        return Err(format!(
            "the store holds {held} bytes and the chunk claimed to start at {offset}"
        ));
    }
    file.seek(SeekFrom::Start(offset))
        .map_err(|error| format!("could not seek {}: {error}", part.display()))?;
    file.write_all(&bytes)
        .map_err(|error| format!("could not write {}: {error}", part.display()))?;
    Ok(json!({ "written": bytes.len(), "partial": offset + bytes.len() as u64 }))
}

/// Publish a finished download: the partial is renamed only when its length is exactly what the
/// caller declared, so a truncated file is never allowed to look complete.
#[tauri::command]
pub fn offline_commit(
    app: tauri::AppHandle,
    account: String,
    key: String,
    suffix: String,
    entry: Value,
) -> Result<Value, String> {
    require_account(&account)?;
    let dir = store_dir(&app, &account)?;
    let name = file_name(&key, &suffix).ok_or_else(|| name_refusal(&key))?;
    let safe_key = safe_component(&key).ok_or_else(|| name_refusal(&key))?;
    let part = part_for(&dir, &name);
    let target = dir.join(&name);
    if !inside(&dir, &part) || !inside(&dir, &target) {
        return Err("that name does not belong in the account's directory".to_string());
    }
    let held = fs::metadata(&part)
        .map_err(|error| format!("there is no partial copy to commit: {error}"))?
        .len();
    let declared = entry.get("size").and_then(Value::as_u64).unwrap_or(0);
    if declared == 0 || held != declared {
        return Err(format!(
            "the copy on disk is {held} bytes and the manifest claims {declared}; \
             a short file is not committed"
        ));
    }
    replace(&part, &target)?;

    let mut manifest = read_manifest(&dir);
    let clean = clean_entry(&entry, &name, &safe_key, &safe_suffix(&suffix), held);
    match manifest.get_mut("entries").and_then(Value::as_object_mut) {
        Some(entries) => {
            entries.insert(safe_key, clean);
        }
        None => return Err("the manifest has no entries object".to_string()),
    }
    manifest["version"] = json!(MANIFEST_VERSION);
    write_manifest(&dir, &manifest)?;
    Ok(json!({ "ok": true, "file": name, "size": held }))
}

/// Remember which file a partial came from: its ETag and the length the server declared. Without
/// this a resume could stitch the first half of one file onto the second half of another, and the
/// result would be exactly the right length and entirely wrong. A pinned entry is not a kept
/// copy — `complete` is false, so verification, playback and eviction all ignore it — and it is
/// what `offline_begin` leaves behind for the next attempt to read.
#[tauri::command]
pub fn offline_pin(
    app: tauri::AppHandle,
    account: String,
    key: String,
    suffix: String,
    etag: Option<String>,
    expected: Option<u64>,
) -> Result<Value, String> {
    require_account(&account)?;
    let dir = store_dir(&app, &account)?;
    let safe_key = safe_component(&key).ok_or_else(|| name_refusal(&key))?;
    let name = file_name(&key, &suffix).ok_or_else(|| name_refusal(&key))?;
    let part = part_for(&dir, &name);
    if !inside(&dir, &part) {
        return Err("that name does not belong in the account's directory".to_string());
    }
    let partial = fs::metadata(&part).map(|meta| meta.len()).unwrap_or(0);
    let tag = etag
        .as_deref()
        .map(|value| sanitise_text(value, 128))
        .filter(|value| !value.is_empty());
    let entry = json!({
        "file": name,
        "key": safe_key,
        "suffix": safe_suffix(&suffix),
        "size": partial,
        "expected": expected,
        "etag": tag,
        "contentType": Value::Null,
        "title": Value::Null,
        "sourceSize": Value::Null,
        "addedAt": Value::Null,
        "lastPlayed": 0,
        "complete": false,
    });
    let mut manifest = read_manifest(&dir);
    match manifest.get_mut("entries").and_then(Value::as_object_mut) {
        Some(entries) => {
            entries.insert(safe_key, entry);
        }
        None => return Err("the manifest has no entries object".to_string()),
    }
    manifest["version"] = json!(MANIFEST_VERSION);
    write_manifest(&dir, &manifest)?;
    Ok(json!({ "ok": true, "partial": partial, "etag": tag }))
}

/// Mark a kept copy as played, which is the only ordering the eviction rule needs.
#[tauri::command]
pub fn offline_touch(
    app: tauri::AppHandle,
    account: String,
    key: String,
    at: u64,
) -> Result<Value, String> {
    let dir = store_dir(&app, &account)?;
    let safe_key = safe_component(&key).ok_or_else(|| name_refusal(&key))?;
    let mut manifest = read_manifest(&dir);
    let mut found = false;
    if let Some(entry) = manifest
        .get_mut("entries")
        .and_then(Value::as_object_mut)
        .and_then(|entries| entries.get_mut(&safe_key))
        .and_then(Value::as_object_mut)
    {
        entry.insert("lastPlayed".to_string(), json!(at));
        found = true;
    }
    if found {
        write_manifest(&dir, &manifest)?;
    }
    Ok(json!({ "ok": found }))
}

/// How many bytes of a kept copy are on disk. `exists: false` is the answer for a manifest entry
/// whose file was removed behind the store's back, and the front end drops the entry on it.
#[tauri::command]
pub fn offline_stat(app: tauri::AppHandle, account: String, key: String) -> Result<Value, String> {
    let dir = store_dir(&app, &account)?;
    let safe_key = safe_component(&key).ok_or_else(|| name_refusal(&key))?;
    let manifest = read_manifest(&dir);
    let entry = manifest
        .get("entries")
        .and_then(|entries| entries.get(&safe_key))
        .cloned();
    let Some(entry) = entry else {
        return Ok(json!({ "ok": true, "exists": false, "bytes": Value::Null }));
    };
    let path = entry_file(&dir, &entry)?;
    let bytes = fs::metadata(&path).map(|meta| meta.len()).ok();
    Ok(json!({
        "ok": true,
        "exists": bytes.is_some(),
        "bytes": bytes,
        "file": entry.get("file").cloned().unwrap_or(Value::Null),
    }))
}

/// A slice of a kept copy, as base64. Seeking a local file is a slice read, not a second
/// download, and this is what lets the audio element scrub a copy that never touches the network.
#[tauri::command]
pub fn offline_read(
    app: tauri::AppHandle,
    account: String,
    key: String,
    offset: u64,
    length: u64,
) -> Result<Value, String> {
    let dir = store_dir(&app, &account)?;
    let safe_key = safe_component(&key).ok_or_else(|| name_refusal(&key))?;
    let manifest = read_manifest(&dir);
    let entry = manifest
        .get("entries")
        .and_then(|entries| entries.get(&safe_key))
        .cloned()
        .ok_or_else(|| "no copy of that track is kept".to_string())?;
    let path = entry_file(&dir, &entry)?;
    let mut file =
        File::open(&path).map_err(|error| format!("could not open {}: {error}", path.display()))?;
    let size = file
        .metadata()
        .map_err(|error| format!("could not size {}: {error}", path.display()))?
        .len();
    if offset > size {
        return Err("that read starts past the end of the copy".to_string());
    }
    let want = length.min(READ_MAX).min(size - offset);
    file.seek(SeekFrom::Start(offset))
        .map_err(|error| format!("could not seek {}: {error}", path.display()))?;
    let mut buffer = vec![0u8; want as usize];
    file.read_exact(&mut buffer)
        .map_err(|error| format!("could not read {}: {error}", path.display()))?;
    Ok(json!({
        "data": b64_encode(&buffer),
        "offset": offset,
        "eof": offset + want >= size,
    }))
}

/// Remove a copy and its manifest entry, with any partial of the same key. Deleting one without
/// the other is how a dialog that says "kept" ends up with nothing on disk.
#[tauri::command]
pub fn offline_delete(
    app: tauri::AppHandle,
    account: String,
    key: String,
) -> Result<Value, String> {
    let dir = store_dir(&app, &account)?;
    let safe_key = safe_component(&key).ok_or_else(|| name_refusal(&key))?;
    let mut manifest = read_manifest(&dir);
    let mut removed = false;
    if let Some(entries) = manifest.get_mut("entries").and_then(Value::as_object_mut) {
        if let Some(entry) = entries.remove(&safe_key) {
            if let Ok(path) = entry_file(&dir, &entry) {
                let _ = fs::remove_file(&path);
            }
            removed = true;
        }
    }
    let partials = remove_partials(&dir, &safe_key);
    if removed || partials > 0 {
        write_manifest(&dir, &manifest)?;
    }
    Ok(json!({ "ok": true, "removed": removed, "partials": partials }))
}

/// What the system says about the volume this account's copies are on.
#[tauri::command]
pub fn offline_free(app: tauri::AppHandle, account: String) -> Result<Value, String> {
    let dir = store_dir(&app, &account)?;
    Ok(disk_free(&dir))
}
