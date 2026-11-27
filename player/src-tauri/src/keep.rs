//! Kept copies: the songs a person asked this device to hold.
//!
//! A phone on a bad connection cannot stream, so the player keeps the bytes it was already
//! allowed to stream and plays them from its own folder. Three rules shape the whole module:
//!
//! * **Only the server a person signed into.** A keep command takes a song id, never an
//!   address. Rust resolves the origin from the bound account, exactly as `subsonic_source`
//!   does, so no payload can point this at another host.
//! * **The folder is ours.** Files live under the app's own data directory, one per song,
//!   named by a sanitised id, and the index that describes them is a JSON file beside them.
//! * **Playback is a loopback URL.** The WebView's `<audio>` needs a URL, not bytes, so a
//!   listener on `127.0.0.1` serves the file with `Range`, which is what seeking needs. No
//!   CSP change, no new plugin, no file-path exposure to the page.
//!
//! The half that can be tested on a machine without WebKit is here: the index, the naming
//! rules, the byte arithmetic and a real socket. The Tauri commands below it are glue.

use std::io::{Read, Seek, SeekFrom, Write};
use std::net::{TcpListener, TcpStream};
use std::path::{Path, PathBuf};
use std::sync::Mutex;
use std::time::{SystemTime, UNIX_EPOCH};

use serde::{Deserialize, Serialize};

/// The index lives beside the files it describes.
pub const INDEX_FILE: &str = "index.json";

/// Longest id this player will turn into a file name. Subsonic ids are short; a page that
/// hands us a sentence is a page that is wrong.
const MAX_ID: usize = 96;

/// One song the device holds.
#[derive(Serialize, Deserialize, Clone, Debug, PartialEq)]
pub struct Kept {
    pub id: String,
    #[serde(default)]
    pub title: String,
    #[serde(default)]
    pub artist: String,
    #[serde(default)]
    pub album: String,
    pub suffix: String,
    pub bytes: u64,
    #[serde(default)]
    pub kept_at: u64,
}

/// Every kept song, in the order they were kept.
#[derive(Default, Serialize, Deserialize, Clone, Debug)]
pub struct Index {
    #[serde(default)]
    pub entries: Vec<Kept>,
}

impl Index {
    /// Read the index. A missing, empty or corrupt file is an empty index rather than an
    /// error: the files themselves are still on disk, and a person who lost the index has
    /// lost names, not music.
    pub fn load(dir: &Path) -> Index {
        let path = dir.join(INDEX_FILE);
        let Ok(text) = std::fs::read_to_string(&path) else {
            return Index::default();
        };
        serde_json::from_str(&text).unwrap_or_default()
    }

    /// Write it through a temporary file so a kill mid-write cannot leave half an index.
    pub fn save(&self, dir: &Path) -> std::io::Result<()> {
        std::fs::create_dir_all(dir)?;
        let body = serde_json::to_vec_pretty(self)
            .map_err(|error| std::io::Error::new(std::io::ErrorKind::InvalidData, error))?;
        let tmp = dir.join(INDEX_FILE.to_string() + ".tmp");
        {
            let mut file = std::fs::File::create(&tmp)?;
            file.write_all(&body)?;
            file.sync_all()?;
        }
        std::fs::rename(&tmp, dir.join(INDEX_FILE))
    }

    pub fn get(&self, id: &str) -> Option<&Kept> {
        self.entries.iter().find(|entry| entry.id == id)
    }

    /// One row per song: keeping the same song twice replaces the row instead of adding one.
    pub fn insert(&mut self, entry: Kept) {
        match self.entries.iter_mut().find(|row| row.id == entry.id) {
            Some(row) => *row = entry,
            None => self.entries.push(entry),
        }
    }

    pub fn remove(&mut self, id: &str) -> Option<Kept> {
        let at = self.entries.iter().position(|row| row.id == id)?;
        Some(self.entries.remove(at))
    }

    pub fn total_bytes(&self) -> u64 {
        self.entries.iter().map(|row| row.bytes).sum()
    }
}

/// Whether an id can become a file name. Digits, letters, `-` and `_` only — the same shapes
/// Subsonic ids take — and nothing that could climb out of the folder.
pub fn safe_id(id: &str) -> bool {
    !id.is_empty()
        && id.len() <= MAX_ID
        && id
            .chars()
            .all(|c| c.is_ascii_alphanumeric() || c == '-' || c == '_')
}

/// The file a kept song lives in.
pub fn file_name(id: &str, suffix: &str) -> String {
    let extension = suffix.trim_start_matches('.').to_ascii_lowercase();
    let extension: String = extension
        .chars()
        .filter(|c| c.is_ascii_alphanumeric())
        .take(8)
        .collect();
    if extension.is_empty() {
        format!("{id}.bin")
    } else {
        format!("{id}.{extension}")
    }
}

/// The extension to store a stream under, from what the server said it is.
pub fn suffix_from(content_type: &str, fallback: &str) -> String {
    let kind = content_type
        .split(';')
        .next()
        .unwrap_or("")
        .trim()
        .to_ascii_lowercase();
    let picked = match kind.as_str() {
        "audio/mpeg" | "audio/mp3" => "mp3",
        "audio/mp4" | "audio/aac" | "audio/x-m4a" | "audio/m4a" => "m4a",
        "audio/flac" | "audio/x-flac" => "flac",
        "audio/ogg" | "audio/vorbis" => "ogg",
        "audio/opus" => "opus",
        "audio/wav" | "audio/x-wav" => "wav",
        "audio/webm" => "webm",
        _ => "",
    };
    if picked.is_empty() {
        let cleaned: String = fallback
            .trim()
            .trim_start_matches('.')
            .chars()
            .filter(|c| c.is_ascii_alphanumeric())
            .take(8)
            .collect();
        if cleaned.is_empty() {
            "bin".to_string()
        } else {
            cleaned.to_ascii_lowercase()
        }
    } else {
        picked.to_string()
    }
}

/// What one request asks for.
#[derive(Debug, PartialEq)]
pub enum Wanted {
    /// No `Range`: the whole file, 200.
    Whole,
    /// `Range: bytes=…`: these inclusive byte positions, 206.
    Part { start: u64, end: u64 },
    /// A range this file cannot answer, 416.
    Impossible,
}

/// `Range` arithmetic. Only the single-range form is answered, which is what a media element
/// sends; anything else falls back to the whole file rather than guessing.
pub fn parse_range(header: Option<&str>, size: u64) -> Wanted {
    let Some(raw) = header else { return Wanted::Whole };
    let Some(rest) = raw.trim().strip_prefix("bytes=") else {
        return Wanted::Whole;
    };
    if rest.contains(',') {
        return Wanted::Whole;
    }
    let Some((from, to)) = rest.split_once('-') else {
        return Wanted::Whole;
    };
    let (from, to) = (from.trim(), to.trim());
    if size == 0 {
        return Wanted::Impossible;
    }
    if from.is_empty() {
        // `bytes=-500`: the last 500 bytes.
        let Ok(tail) = to.parse::<u64>() else { return Wanted::Whole };
        if tail == 0 {
            return Wanted::Impossible;
        }
        let start = size.saturating_sub(tail);
        return Wanted::Part { start, end: size - 1 };
    }
    let Ok(start) = from.parse::<u64>() else { return Wanted::Whole };
    if start >= size {
        return Wanted::Impossible;
    }
    let end = if to.is_empty() {
        size - 1
    } else {
        match to.parse::<u64>() {
            Ok(value) => value.min(size - 1),
            Err(_) => return Wanted::Whole,
        }
    };
    if end < start {
        return Wanted::Impossible;
    }
    Wanted::Part { start, end }
}

/// Seconds since the epoch, for a kept-at stamp.
pub fn now() -> u64 {
    SystemTime::now()
        .duration_since(UNIX_EPOCH)
        .map(|elapsed| elapsed.as_secs())
        .unwrap_or(0)
}

/// The folder kept songs live in, under whatever data directory the platform gave us.
pub fn keep_dir(data: &Path) -> PathBuf {
    data.join("kept")
}

/* ---- the loopback server --------------------------------------------------------- */

/// A request line and headers, read as plain text. Media elements speak HTTP/1.1 and ask for
/// one thing; a request this small does not need a parser crate.
pub fn read_request(stream: &mut TcpStream) -> Option<(String, Vec<(String, String)>)> {
    let mut raw = Vec::new();
    let mut buf = [0u8; 1024];
    loop {
        let read = stream.read(&mut buf).ok()?;
        if read == 0 {
            break;
        }
        raw.extend_from_slice(&buf[..read]);
        if raw.windows(4).any(|w| w == b"\r\n\r\n") || raw.len() > 16384 {
            break;
        }
    }
    let text = String::from_utf8(raw).ok()?;
    let mut lines = text.split("\r\n");
    let request = lines.next()?.to_string();
    let headers = lines
        .take_while(|line| !line.is_empty())
        .filter_map(|line| {
            let (name, value) = line.split_once(':')?;
            Some((name.trim().to_ascii_lowercase(), value.trim().to_string()))
        })
        .collect();
    Some((request, headers))
}

/// Answer one request from one connection, then close it. The page's `<audio>` sends a `GET`
/// with an optional `Range`; everything else is a bad request rather than a guess.
pub fn answer(stream: &mut TcpStream, dir: &Path) -> std::io::Result<()> {
    let Some((request, headers)) = read_request(stream) else {
        return reply(stream, 400, "Bad Request", &[], b"", None);
    };
    let mut parts = request.split_whitespace();
    let method = parts.next().unwrap_or("");
    let target = parts.next().unwrap_or("/");
    if method != "GET" && method != "HEAD" {
        return reply(stream, 405, "Method Not Allowed", &[], b"", None);
    }
    // `/42?x` is the same song as `/42`; a query is not part of the name.
    let id = target
        .trim_start_matches('/')
        .split(['?', '#'])
        .next()
        .unwrap_or("");
    let index = Index::load(dir);
    let Some(row) = index.get(id).filter(|row| safe_id(&row.id)) else {
        return reply(stream, 404, "Not Found", &[], b"", None);
    };
    let file = dir.join(file_name(&row.id, &row.suffix));
    let Ok(meta) = std::fs::metadata(&file) else {
        return reply(stream, 404, "Not Found", &[], b"", None);
    };
    let size = meta.len();
    let range = headers
        .iter()
        .find(|(name, _)| name == "range")
        .map(|(_, value)| value.as_str());
    let content_type = format!("audio/{}", suffix_from("", &row.suffix));
    let mut file = std::fs::File::open(&file)?;
    match parse_range(range, size) {
        Wanted::Impossible => reply(
            stream,
            416,
            "Range Not Satisfiable",
            &[("Content-Range".into(), format!("bytes */{size}"))],
            b"",
            None,
        ),
        Wanted::Whole => {
            if method == "HEAD" {
                return reply(stream, 200, "OK", &[], b"", Some((&content_type, size)));
            }
            let mut body = Vec::with_capacity(size as usize);
            file.read_to_end(&mut body)?;
            reply(
                stream,
                200,
                "OK",
                &[
                    ("Content-Type".into(), content_type),
                    ("Content-Length".into(), size.to_string()),
                    ("Accept-Ranges".into(), "bytes".into()),
                ],
                &body,
                None,
            )
        }
        Wanted::Part { start, end } => {
            let length = end - start + 1;
            if method == "HEAD" {
                return reply(
                    stream,
                    206,
                    "Partial Content",
                    &[("Content-Range".into(), format!("bytes {start}-{end}/{size}"))],
                    b"",
                    Some((&content_type, length)),
                );
            }
            file.seek(SeekFrom::Start(start))?;
            let mut body = vec![0u8; length as usize];
            file.read_exact(&mut body)?;
            reply(
                stream,
                206,
                "Partial Content",
                &[
                    ("Content-Type".into(), content_type),
                    ("Content-Range".into(), format!("bytes {start}-{end}/{size}")),
                    ("Content-Length".into(), length.to_string()),
                    ("Accept-Ranges".into(), "bytes".into()),
                ],
                &body,
                None,
            )
        }
    }
}

/// Write the status line, the headers and the body. `length` overrides `Content-Length` for a
/// `HEAD`, where the answer describes a body it does not send.
fn reply(
    stream: &mut TcpStream,
    status: u16,
    reason: &str,
    headers: &[(String, String)],
    body: &[u8],
    length: Option<(&str, u64)>,
) -> std::io::Result<()> {
    let mut head = format!("HTTP/1.1 {status} {reason}\r\n");
    for (name, value) in headers {
        head.push_str(&format!("{name}: {value}\r\n"));
    }
    match length {
        Some((kind, value)) => {
            head.push_str(&format!("Content-Type: {kind}\r\n"));
            head.push_str(&format!("Content-Length: {value}\r\n"));
        }
        None => {
            if !headers.iter().any(|(name, _)| name == "Content-Length") {
                head.push_str(&format!("Content-Length: {}\r\n", body.len()));
            }
        }
    }
    head.push_str("Connection: close\r\n\r\n");
    stream.write_all(head.as_bytes())?;
    if !body.is_empty() {
        stream.write_all(body)?;
    }
    stream.flush()?;
    let _ = stream.shutdown(std::net::Shutdown::Write);
    Ok(())
}

/* ---- the commands the page calls -------------------------------------------------- */

use std::collections::HashSet;

use tauri::{AppHandle, Emitter, Manager, State};

use crate::accounts::Vault;
use crate::subsonic::{self, Failure, Registry};

/// The event the page listens on for progress. One name, both sides.
pub const KEEP_EVENT: &str = "hl:keep";

/// How often a progress event is worth sending. A chunk is a few kilobytes; one event per
/// chunk would be thousands of messages for one song.
const PROGRESS_STEP: u64 = 128 * 1024;

/// The deadline for one song's fetch. Not the shared client's 30 seconds: that is a JSON call's
/// deadline, and this feature exists for the connection where 30 seconds is not enough.
const KEEP_TIMEOUT: std::time::Duration = std::time::Duration::from_secs(15 * 60);

/// Which songs are being fetched right now, so a double tap cannot fetch one twice.
pub struct KeepState(Mutex<HashSet<String>>);

impl KeepState {
    pub fn new() -> KeepState {
        KeepState(Mutex::new(HashSet::new()))
    }
}

/// What the page knows about a song when it asks for it to be kept.
#[derive(Deserialize, Clone, Debug)]
#[serde(rename_all = "camelCase")]
pub struct KeepSong {
    pub id: String,
    #[serde(default)]
    pub title: String,
    #[serde(default)]
    pub artist: String,
    #[serde(default)]
    pub album: String,
    #[serde(default)]
    pub suffix: String,
}

#[derive(Serialize, Clone)]
#[serde(rename_all = "camelCase")]
struct Progress {
    id: String,
    received: u64,
    total: Option<u64>,
}

/// The folder this build keeps songs in: the platform's data directory, then `kept/`.
fn dir_for(app: &AppHandle) -> Result<PathBuf, Failure> {
    let data = app
        .path()
        .app_data_dir()
        .map_err(|error| Failure::config(format!("This build has no data folder — {error}")))?;
    let dir = keep_dir(&data);
    std::fs::create_dir_all(&dir)
        .map_err(|error| Failure::config(format!("The keep folder could not be made — {error}")))?;
    Ok(dir)
}

/// Keep one song. The address comes from the bound account, never from the page.
#[tauri::command(rename_all = "camelCase")]
pub async fn keep_add(
    app: AppHandle,
    registry: State<'_, Registry>,
    vault: State<'_, Vault>,
    busy: State<'_, KeepState>,
    account_id: String,
    song: KeepSong,
) -> Result<Kept, Failure> {
    if !safe_id(&song.id) {
        return Err(Failure::config("That song id cannot be kept"));
    }
    {
        let mut running = busy
            .0
            .lock()
            .map_err(|_| Failure::config("The keep queue is unusable"))?;
        if !running.insert(song.id.clone()) {
            return Err(Failure::config("That song is already being kept"));
        }
    }
    let outcome = fetch_into(&app, &registry, &vault, &account_id, &song).await;
    if let Ok(mut running) = busy.0.lock() {
        running.remove(&song.id);
    }
    outcome
}

/// The body of `keep_add`, taking the states rather than the command's wrapper.
async fn fetch_into(
    app: &AppHandle,
    registry: &Registry,
    vault: &Vault,
    account_id: &str,
    song: &KeepSong,
) -> Result<Kept, Failure> {
    let dir = dir_for(app)?;
    let url = subsonic::stream_url(registry, vault, account_id, &song.id)?;

    let response = subsonic::client()
        .get(&url)
        // The shared client's 30-second deadline is a JSON call's deadline. A song is megabytes,
        // and the whole point of this feature is a bad connection: a 10 MB track at 100 KB/s
        // would be killed at 30 s and never kept. Fifteen minutes bounds a hung socket without
        // failing a slow one.
        .timeout(KEEP_TIMEOUT)
        .header("Accept", "audio/*")
        .send()
        .await
        .map_err(|error| Failure::transport(format!("The song could not be fetched — {}", subsonic::reason(&error))))?;
    if !response.status().is_success() {
        return Err(Failure::http(
            response.status().as_u16(),
            format!("The server answered {} for that song", response.status().as_u16()),
        ));
    }
    let content_type = response
        .headers()
        .get(reqwest::header::CONTENT_TYPE)
        .and_then(|value| value.to_str().ok())
        .unwrap_or("")
        .to_string();
    let suffix = suffix_from(&content_type, &song.suffix);
    let total = response.content_length();

    let part = dir.join(file_name(&song.id, "part"));
    let fin = dir.join(file_name(&song.id, &suffix));
    let mut body = response;
    let mut written = 0u64;
    let mut announced = 0u64;
    {
        let mut file = std::fs::File::create(&part)
            .map_err(|error| Failure::config(format!("The song could not be written — {error}")))?;
        loop {
            let chunk = body
                .chunk()
                .await
                .map_err(|error| Failure::transport(format!("The song stopped early — {}", subsonic::reason(&error))))?;
            let Some(chunk) = chunk else { break };
            file.write_all(&chunk)
                .map_err(|error| Failure::config(format!("The song could not be written — {error}")))?;
            written += chunk.len() as u64;
            if written - announced >= PROGRESS_STEP {
                announced = written;
                let _ = app.emit(
                    KEEP_EVENT,
                    Progress { id: song.id.clone(), received: written, total },
                );
            }
        }
        file.sync_all()
            .map_err(|error| Failure::config(format!("The song could not be saved — {error}")))?;
    }
    if written == 0 {
        let _ = std::fs::remove_file(&part);
        return Err(Failure::transport("The server sent no audio for that song"));
    }
    std::fs::rename(&part, &fin)
        .map_err(|error| Failure::config(format!("The song could not be finished — {error}")))?;

    let row = Kept {
        id: song.id.clone(),
        title: song.title.clone(),
        artist: song.artist.clone(),
        album: song.album.clone(),
        suffix,
        bytes: written,
        kept_at: now(),
    };
    let mut index = Index::load(&dir);
    // A re-keep can change the extension (a server that now serves flac), and the old file
    // would then be an orphan beside the new one.
    if let Some(old) = index.get(&row.id).cloned() {
        if old.suffix != row.suffix {
            let _ = std::fs::remove_file(dir.join(file_name(&old.id, &old.suffix)));
        }
    }
    index.insert(row.clone());
    index
        .save(&dir)
        .map_err(|error| Failure::config(format!("The keep index could not be written — {error}")))?;
    let _ = app.emit(
        KEEP_EVENT,
        Progress { id: song.id.clone(), received: written, total: Some(written) },
    );
    Ok(row)
}

/// What this device holds.
#[tauri::command]
pub fn keep_list(app: AppHandle) -> Vec<Kept> {
    dir_for(&app).map(|dir| Index::load(&dir).entries).unwrap_or_default()
}

/// Forget a song and free its bytes.
#[tauri::command(rename_all = "camelCase")]
pub fn keep_remove(app: AppHandle, song_id: String) -> Result<(), Failure> {
    let dir = dir_for(&app)?;
    let mut index = Index::load(&dir);
    let Some(row) = index.remove(&song_id) else {
        return Ok(());
    };
    let _ = std::fs::remove_file(dir.join(file_name(&row.id, &row.suffix)));
    index
        .save(&dir)
        .map_err(|error| Failure::config(format!("The keep index could not be written — {error}")))?;
    Ok(())
}

/// Forget everything.
#[tauri::command]
pub fn keep_clear(app: AppHandle) -> Result<(), Failure> {
    let dir = dir_for(&app)?;
    let index = Index::load(&dir);
    for row in &index.entries {
        let _ = std::fs::remove_file(dir.join(file_name(&row.id, &row.suffix)));
    }
    Index::default()
        .save(&dir)
        .map_err(|error| Failure::config(format!("The keep index could not be written — {error}")))?;
    Ok(())
}

/// Where the page can play a kept song from, or `None` when it is not held here.
///
/// The address is this process's own loopback listener, so the page never learns a file path
/// and the platform's `media-src` rule needs nothing new.
#[tauri::command(rename_all = "camelCase")]
pub fn keep_source(app: AppHandle, song_id: String) -> Option<String> {
    let dir = dir_for(&app).ok()?;
    let index = Index::load(&dir);
    let row = index.get(&song_id)?;
    if !dir.join(file_name(&row.id, &row.suffix)).exists() {
        return None;
    }
    let port = listen(&app, dir)?;
    Some(format!("http://127.0.0.1:{port}/{}", row.id))
}

/// Start the loopback listener once, and remember its port.
pub fn listen(app: &AppHandle, dir: PathBuf) -> Option<u16> {
    let state = app.try_state::<Listener>()?;
    let mut port = state.0.lock().ok()?;
    if let Some(port) = *port {
        return Some(port);
    }
    let listener = TcpListener::bind("127.0.0.1:0").ok()?;
    let bound = listener.local_addr().ok()?.port();
    std::thread::spawn(move || {
        for stream in listener.incoming() {
            let Ok(mut stream) = stream else { continue };
            let dir = dir.clone();
            std::thread::spawn(move || {
                let _ = answer(&mut stream, &dir);
            });
        }
    });
    *port = Some(bound);
    Some(bound)
}

/// The listener's port, once one exists.
pub struct Listener(Mutex<Option<u16>>);

impl Listener {
    pub fn new() -> Listener {
        Listener(Mutex::new(None))
    }
}

/* ---- tests ----------------------------------------------------------------------- */

#[cfg(test)]
mod tests {
    use super::*;
    use std::io::Write;
    use std::net::TcpListener;

    fn temp_dir(name: &str) -> PathBuf {
        let dir = std::env::temp_dir().join(format!("hl-keep-test-{name}-{}", std::process::id()));
        let _ = std::fs::remove_dir_all(&dir);
        std::fs::create_dir_all(&dir).expect("a scratch folder");
        dir
    }

    fn entry(id: &str) -> Kept {
        Kept {
            id: id.to_string(),
            title: "A song".into(),
            artist: "Someone".into(),
            album: "Somewhere".into(),
            suffix: "mp3".into(),
            bytes: 10,
            kept_at: 7,
        }
    }

    #[test]
    fn an_index_round_trips_through_its_file() {
        let dir = temp_dir("round-trip");
        let mut index = Index::default();
        index.insert(entry("42"));
        index.save(&dir).expect("saved");
        assert_eq!(Index::load(&dir).entries, index.entries);
        assert!(!dir.join(INDEX_FILE.to_string() + ".tmp").exists(), "no temporary file is left behind");
    }

    #[test]
    fn a_missing_or_corrupt_index_is_empty_not_an_error() {
        let dir = temp_dir("corrupt");
        assert_eq!(Index::load(&dir).entries.len(), 0);
        std::fs::write(dir.join(INDEX_FILE), b"{ this is not json").expect("written");
        assert_eq!(Index::load(&dir).entries.len(), 0);
    }

    #[test]
    fn one_row_per_song_and_removal_is_the_row() {
        let mut index = Index::default();
        index.insert(entry("42"));
        let mut again = entry("42");
        again.bytes = 99;
        index.insert(again);
        assert_eq!(index.entries.len(), 1, "keeping a song twice replaces its row");
        assert_eq!(index.get("42").map(|row| row.bytes), Some(99));
        assert_eq!(index.total_bytes(), 99);
        assert!(index.remove("42").is_some());
        assert!(index.get("42").is_none());
        assert!(index.remove("42").is_none());
    }

    #[test]
    fn an_id_that_could_leave_the_folder_is_refused() {
        for good in ["42", "abc-1_2", "0"] {
            assert!(safe_id(good), "{good} is a normal id");
        }
        for bad in ["", "../etc/passwd", "a/b", "a\\b", "a b", "a.b", &"9".repeat(MAX_ID + 1)] {
            assert!(!safe_id(bad), "{bad:?} must be refused");
        }
    }

    #[test]
    fn a_file_name_is_the_id_and_a_plain_extension() {
        assert_eq!(file_name("42", "mp3"), "42.mp3");
        assert_eq!(file_name("42", ".FLAC"), "42.flac");
        assert_eq!(file_name("42", "m4a/.."), "42.m4a");
        assert_eq!(file_name("42", ""), "42.bin");
    }

    #[test]
    fn the_stored_extension_follows_what_the_server_said() {
        assert_eq!(suffix_from("audio/mpeg", "flac"), "mp3");
        assert_eq!(suffix_from("audio/mp4; codecs=mp4a", ""), "m4a");
        assert_eq!(suffix_from("audio/flac", "mp3"), "flac");
        assert_eq!(suffix_from("audio/ogg", ""), "ogg");
        assert_eq!(suffix_from("application/octet-stream", "M4A"), "m4a");
        assert_eq!(suffix_from("application/octet-stream", ""), "bin");
    }

    #[test]
    fn a_range_is_answered_for_the_part_it_names() {
        assert_eq!(parse_range(None, 1000), Wanted::Whole);
        assert_eq!(parse_range(Some("bytes=0-99"), 1000), Wanted::Part { start: 0, end: 99 });
        assert_eq!(parse_range(Some("bytes=100-"), 1000), Wanted::Part { start: 100, end: 999 });
        assert_eq!(parse_range(Some("bytes=-50"), 1000), Wanted::Part { start: 950, end: 999 });
        assert_eq!(parse_range(Some("bytes=900-5000"), 1000), Wanted::Part { start: 900, end: 999 });
        assert_eq!(parse_range(Some("bytes=0-0"), 10), Wanted::Part { start: 0, end: 0 });
    }

    #[test]
    fn a_range_this_file_cannot_answer_is_impossible() {
        assert_eq!(parse_range(Some("bytes=1000-"), 1000), Wanted::Impossible);
        assert_eq!(parse_range(Some("bytes=-0"), 1000), Wanted::Impossible);
        assert_eq!(parse_range(Some("bytes=5-1"), 1000), Wanted::Impossible);
        assert_eq!(parse_range(Some("bytes=0-"), 0), Wanted::Impossible);
        // Anything unreadable asks for the whole file instead of guessing at a slice.
        assert_eq!(parse_range(Some("items=0-9"), 1000), Wanted::Whole);
        assert_eq!(parse_range(Some("bytes=0-9,20-29"), 1000), Wanted::Whole);
        assert_eq!(parse_range(Some("bytes=abc-def"), 1000), Wanted::Whole);
    }

    #[test]
    fn the_keep_folder_sits_under_the_data_directory() {
        assert_eq!(keep_dir(Path::new("/data")), PathBuf::from("/data/kept"));
    }

    /* ---- over a real socket, because that is what the media element talks to ---- */

    fn serve_one(dir: PathBuf, request: &str) -> String {
        let listener = TcpListener::bind("127.0.0.1:0").expect("a port");
        let port = listener.local_addr().expect("an address").port();
        let handle = std::thread::spawn(move || {
            let (mut stream, _) = listener.accept().expect("a connection");
            let _ = answer(&mut stream, &dir);
        });
        let mut client = TcpStream::connect(("127.0.0.1", port)).expect("connected");
        client.write_all(request.as_bytes()).expect("sent");
        client.flush().expect("flushed");
        let mut answer = Vec::new();
        client.read_to_end(&mut answer).expect("read");
        handle.join().expect("the server thread");
        String::from_utf8_lossy(&answer).to_string()
    }

    fn kept_file(name: &str, bytes: &[u8]) -> (PathBuf, Index) {
        let dir = temp_dir(name);
        std::fs::write(dir.join("42.mp3"), bytes).expect("a file");
        let mut index = Index::default();
        let mut row = entry("42");
        row.bytes = bytes.len() as u64;
        index.insert(row);
        index.save(&dir).expect("saved");
        (dir, index)
    }

    #[test]
    fn a_whole_file_answers_200_with_its_bytes() {
        let (dir, _) = kept_file("whole", b"0123456789");
        let raw = serve_one(dir, "GET /42 HTTP/1.1\r\nHost: 127.0.0.1\r\n\r\n");
        assert!(raw.starts_with("HTTP/1.1 200 OK\r\n"), "{raw}");
        assert!(raw.contains("Accept-Ranges: bytes"), "{raw}");
        assert!(raw.contains("Content-Length: 10"), "{raw}");
        assert!(raw.ends_with("0123456789"), "{raw}");
    }

    #[test]
    fn a_range_answers_206_with_the_part_and_its_total() {
        let (dir, _) = kept_file("part", b"0123456789");
        let raw = serve_one(dir, "GET /42 HTTP/1.1\r\nRange: bytes=2-4\r\n\r\n");
        assert!(raw.starts_with("HTTP/1.1 206 Partial Content\r\n"), "{raw}");
        assert!(raw.contains("Content-Range: bytes 2-4/10"), "{raw}");
        assert!(raw.contains("Content-Length: 3"), "{raw}");
        assert!(raw.ends_with("234"), "{raw}");
    }

    #[test]
    fn a_range_past_the_end_answers_416_with_the_real_length() {
        let (dir, _) = kept_file("past", b"0123456789");
        let raw = serve_one(dir, "GET /42 HTTP/1.1\r\nRange: bytes=99-\r\n\r\n");
        assert!(raw.starts_with("HTTP/1.1 416 Range Not Satisfiable\r\n"), "{raw}");
        assert!(raw.contains("Content-Range: bytes */10"), "{raw}");
    }

    #[test]
    fn a_song_that_is_not_kept_answers_404() {
        let (dir, _) = kept_file("missing", b"0123456789");
        let raw = serve_one(dir, "GET /nope HTTP/1.1\r\n\r\n");
        assert!(raw.starts_with("HTTP/1.1 404 Not Found\r\n"), "{raw}");
    }
}