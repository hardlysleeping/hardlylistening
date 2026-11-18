//! Native networking: the side that knows the address and the secret.
//!
//! The page asks for an endpoint by name and hands over the endpoint's parameters. Everything a
//! proxy would need to be dangerous — the host, the scheme, the credential — is decided here,
//! from the record [`subsonic_url::Registry`] was given when the account was bound, and from the
//! secret the vault holds under that account id. The page is never told either one back.
//!
//! Two consequences worth stating plainly:
//!
//! - **The secret does not cross the bridge.** `accounts_secret` still exists for the web
//!   transport (a browser build has no keychain and no Rust), but on this path it is never
//!   called: the password is read here, used to build one URL, and dropped.
//! - **That last sentence is not quite complete.** `subsonic_source` returns a *stream URL*, and
//!   a Subsonic stream URL carries its authentication in the query string, because an `<audio>`
//!   element cannot send headers. So one request-scoped credential — `u`, and either `p` or a
//!   salted `t` — does reach the page for as long as the web audio engine needs a URL. The
//!   password itself still does not. P3's native audio engine plays from Rust and removes this
//!   hand-off; see `docs/player/transport.md`.
//!
//! The pure half of this file — the address validation, the endpoint and parameter checks, the
//! token — lives in `subsonic_url.rs` and is tested on hosts that cannot build the shell. What is
//! left here is glue: read the record, call `reqwest`, shape the answer. That glue is unverified
//! without a real build (this host has no WebKitGTK development packages), and is written to be
//! as small as the job allows for that reason.

use std::collections::HashMap;
use std::sync::OnceLock;
use std::time::Duration;

use serde::Serialize;
use serde_json::Value;
use tauri::State;

use crate::accounts::Vault;
pub use crate::subsonic_url::Registry;
use crate::subsonic_url::{self as address, Bound};

/// A request that has not been answered in this long is not going to be.
const TIMEOUT: Duration = Duration::from_secs(30);

/// What a refusal looks like on the bridge.
///
/// `kind` is the part the page branches on, and the four kinds match the four errors the web
/// transport can raise, so nothing above this layer has to know which transport answered:
/// `config` is a `ClientError`, `http` with 401/403 is the protocol's code 40, `json` is
/// "answered, but not Subsonic", and everything else is a transport failure.
#[derive(Serialize, Debug)]
pub struct Failure {
    kind: &'static str,
    status: Option<u16>,
    message: String,
}

impl Failure {
    pub(crate) fn config(message: impl Into<String>) -> Failure {
        Failure {
            kind: "config",
            status: None,
            message: message.into(),
        }
    }

    pub(crate) fn http(status: u16, message: impl Into<String>) -> Failure {
        Failure {
            kind: "http",
            status: Some(status),
            message: message.into(),
        }
    }

    pub(crate) fn json(message: impl Into<String>) -> Failure {
        Failure {
            kind: "json",
            status: None,
            message: message.into(),
        }
    }

    pub(crate) fn transport(message: impl Into<String>) -> Failure {
        Failure {
            kind: "transport",
            status: None,
            message: message.into(),
        }
    }
}

/// The one client this process uses. Building it is the expensive part; the pool is the point.
fn client() -> &'static reqwest::Client {
    static CLIENT: OnceLock<reqwest::Client> = OnceLock::new();
    CLIENT.get_or_init(|| {
        reqwest::Client::builder()
            .timeout(TIMEOUT)
            .user_agent(address::CLIENT_NAME)
            .build()
            .unwrap_or_default()
    })
}

/// One request, resolved to the parsed JSON body.
async fn envelope(
    registry: &Registry,
    vault: &Vault,
    account_id: &str,
    endpoint: &str,
    params: Vec<(String, String)>,
) -> Result<Value, Failure> {
    if !address::endpoint_ok(endpoint) {
        return Err(Failure::config(format!(
            "\"{endpoint}\" is not a Subsonic endpoint name"
        )));
    }
    address::params_ok(&params).map_err(Failure::config)?;

    let bound = registry
        .bound(account_id)
        .ok_or_else(|| Failure::config("That account is not connected"))?;
    let secret = vault
        .get(account_id)
        .ok_or_else(|| Failure::config("The keychain has no password for that account"))?;

    let url = address::request_url(&bound, &secret, &address::salt(), endpoint, &params);
    // `secret` is dropped here and never stored; `url` is the only thing that travels.

    let response = client()
        .get(&url)
        .header("Accept", "application/json")
        .send()
        .await
        .map_err(|error| {
            Failure::transport(format!("Could not reach {} — {error}", bound.origin))
        })?;

    let status = response.status();
    if !status.is_success() {
        let code = status.as_u16();
        if code == 401 || code == 403 {
            return Err(Failure::http(code, "Wrong username or password"));
        }
        return Err(Failure::http(
            code,
            format!("{} answered HTTP {code}", bound.origin),
        ));
    }

    let body = response
        .text()
        .await
        .map_err(|error| Failure::transport(format!("The answer was cut short — {error}")))?;
    serde_json::from_str(&body).map_err(|_| {
        Failure::json(format!(
            "{} answered, but not with JSON — that address is probably not a Subsonic server",
            bound.origin
        ))
    })
}

/// The stream address for one song. See the module note: this is the one value that carries a
/// credential to the page, and it exists because a web `<audio>` element cannot send headers.
#[derive(Serialize)]
pub struct StreamAddress {
    url: String,
    transcoded: bool,
}

/* ---- the commands the player's page calls ---------------------------------- */

/// Pin an account's address and form, after the vault already holds its secret.
///
/// The vault is checked here rather than at the first request so a person learns "the password
/// was not stored" on the connect screen instead of when the first page of albums fails.
#[tauri::command(rename_all = "camelCase")]
pub fn subsonic_bind(
    registry: State<'_, Registry>,
    vault: State<'_, Vault>,
    account_id: String,
    origin: String,
    username: String,
    form: String,
) -> Result<(), Failure> {
    let bound = Bound::new(&origin, &username, &form).map_err(Failure::config)?;
    if vault.get(&account_id).is_none() {
        return Err(Failure::config(
            "The keychain has no password for that account. Sign in again",
        ));
    }
    registry.bind(&account_id, bound).map_err(Failure::config)
}

/// Forget an account's address. Called when a person signs out or removes an account.
#[tauri::command(rename_all = "camelCase")]
pub fn subsonic_unbind(registry: State<'_, Registry>, account_id: String) {
    registry.forget(&account_id);
}

#[tauri::command(rename_all = "camelCase")]
pub async fn subsonic_request(
    registry: State<'_, Registry>,
    vault: State<'_, Vault>,
    account_id: String,
    endpoint: String,
    params: HashMap<String, String>,
) -> Result<Value, Failure> {
    // Sorted, so the same request is the same URL and a log can be compared line for line.
    let mut ordered: Vec<(String, String)> = params.into_iter().collect();
    ordered.sort();
    envelope(&registry, &vault, &account_id, &endpoint, ordered).await
}

#[tauri::command(rename_all = "camelCase")]
pub async fn subsonic_source(
    registry: State<'_, Registry>,
    vault: State<'_, Vault>,
    account_id: String,
    song_id: String,
    max_bit_rate: Option<u32>,
    format: Option<String>,
) -> Result<StreamAddress, Failure> {
    if song_id.trim().is_empty() {
        return Err(Failure::config("A stream needs a song"));
    }
    let mut params = vec![("id".to_string(), song_id)];
    if let Some(rate) = max_bit_rate {
        params.push(("maxBitRate".to_string(), rate.to_string()));
    }
    if let Some(format) = format.filter(|value| !value.trim().is_empty()) {
        params.push(("format".to_string(), format));
    }
    address::params_ok(&params).map_err(Failure::config)?;

    let bound = registry
        .bound(&account_id)
        .ok_or_else(|| Failure::config("That account is not connected"))?;
    let secret = vault
        .get(&account_id)
        .ok_or_else(|| Failure::config("The keychain has no password for that account"))?;
    let transcoded = params.len() > 1;
    let url = address::request_url(&bound, &secret, &address::salt(), "stream", &params);
    Ok(StreamAddress { url, transcoded })
}

#[tauri::command(rename_all = "camelCase")]
pub async fn subsonic_cover(
    registry: State<'_, Registry>,
    vault: State<'_, Vault>,
    account_id: String,
    cover_art_id: String,
    size: u32,
) -> Result<String, Failure> {
    if cover_art_id.trim().is_empty() {
        return Err(Failure::config("Artwork needs an id"));
    }
    let params = vec![
        ("id".to_string(), cover_art_id),
        ("size".to_string(), size.max(32).min(2048).to_string()),
    ];
    address::params_ok(&params).map_err(Failure::config)?;

    let bound = registry
        .bound(&account_id)
        .ok_or_else(|| Failure::config("That account is not connected"))?;
    let secret = vault
        .get(&account_id)
        .ok_or_else(|| Failure::config("The keychain has no password for that account"))?;
    Ok(address::request_url(
        &bound,
        &secret,
        &address::salt(),
        "getCoverArt",
        &params,
    ))
}

/// One of the server's own public documents: the capability probe, or the activity feed.
///
/// This exists because the window's CSP forbids the page from fetching a host: `connect-src` is
/// the asset origin and IPC, so a `fetch()` to the music server is refused by the platform before
/// it leaves. The page still names what it wants — `capabilities` or `activity` — and the address
/// comes from the bound account here, exactly as it does for `/rest`. Neither document takes a
/// credential, which is why this path never reads the vault.
#[tauri::command(rename_all = "camelCase")]
pub async fn subsonic_public(
    registry: State<'_, Registry>,
    account_id: String,
    resource: String,
    limit: Option<u32>,
) -> Result<Value, Failure> {
    public_document(&registry, &account_id, &resource, limit).await
}

/// The body of the command, taking the registry rather than Tauri's handle so a test can call
/// it: the address still comes from the bound account, which is the part that matters.
async fn public_document(
    registry: &Registry,
    account_id: &str,
    resource: &str,
    limit: Option<u32>,
) -> Result<Value, Failure> {
    let bound = registry
        .bound(account_id)
        .ok_or_else(|| Failure::config("That account is not connected"))?;
    // Refused before anything is sent: an unknown name has no path, so no request exists to make.
    let url = address::public_url(&bound, resource, limit).map_err(Failure::config)?;

    let response = client()
        .get(&url)
        .header("Accept", "application/json")
        .send()
        .await
        .map_err(|error| {
            Failure::transport(format!("Could not reach {} — {error}", bound.origin))
        })?;

    let status = response.status();
    if !status.is_success() {
        let code = status.as_u16();
        return Err(Failure::http(
            code,
            format!("{} answered HTTP {code}", bound.origin),
        ));
    }

    let body = response
        .text()
        .await
        .map_err(|error| Failure::transport(format!("The answer was cut short — {error}")))?;
    serde_json::from_str(&body)
        .map_err(|_| Failure::json(format!("{} answered, but not with JSON", bound.origin)))
}

#[cfg(test)]
mod tests {
    /* The public read, exercised against a socket rather than described.
     *
     * A one-request stub answers whatever this module asks, records the request line it got, and
     * stops. That is enough to hold four things: the URL is the account's own address plus the
     * document's path, no credential travels, an HTTP refusal is a refusal, and a document that
     * is not JSON is not "no feed" by accident.
     */
    use std::io::{BufRead, BufReader, Write};
    use std::net::TcpListener;
    use std::thread;

    use super::*;
    use crate::subsonic_url::Bound;

    /// Serve exactly one request, answering with `status`, `content_type` and `body`.
    fn once(status: &str, content_type: &str, body: &str) -> (String, thread::JoinHandle<String>) {
        let listener = TcpListener::bind("127.0.0.1:0").expect("a port");
        let origin = format!("http://{}", listener.local_addr().expect("an address"));
        let status = status.to_string();
        let content_type = content_type.to_string();
        let body = body.to_string();
        let handle = thread::spawn(move || {
            let (stream, _) = listener.accept().expect("a connection");
            let mut reader = BufReader::new(stream.try_clone().expect("a clone"));
            let mut first = String::new();
            reader.read_line(&mut first).expect("a request line");
            // Read the headers out so the response is not sent into a half-read request.
            loop {
                let mut line = String::new();
                if reader.read_line(&mut line).unwrap_or(0) == 0 || line.trim_end().is_empty() {
                    break;
                }
            }
            let reply = format!(
                "HTTP/1.1 {status}\r\nContent-Type: {content_type}\r\nContent-Length: {}\r\nConnection: close\r\n\r\n{body}",
                body.len()
            );
            let mut writer = stream;
            writer.write_all(reply.as_bytes()).expect("a reply");
            first.trim().to_string()
        });
        (origin, handle)
    }

    fn registry_for(origin: &str) -> Registry {
        let registry = Registry::new();
        registry
            .bind(
                "acc-1",
                Bound::new(origin, "alex", "token").expect("a normal address"),
            )
            .expect("a binding");
        registry
    }

    fn runtime() -> tokio::runtime::Runtime {
        tokio::runtime::Builder::new_current_thread()
            .enable_all()
            .build()
            .expect("a runtime")
    }

    #[test]
    fn the_read_asks_the_accounts_own_address_for_the_document_and_carries_no_credential() {
        let (origin, served) = once("200 OK", "application/json", r#"{"api":"v1"}"#);
        let registry = registry_for(&origin);

        let value = runtime()
            .block_on(public_document(&registry, "acc-1", "capabilities", None))
            .expect("the document");

        assert_eq!(value["api"], "v1");
        let request = served.join().expect("the stub");
        assert_eq!(request, "GET /api/v1/capabilities HTTP/1.1");
        for key in ["u=", "t=", "s=", "p=", "v=", "c="] {
            assert!(!request.contains(key), "the request line carries {key}");
        }
    }

    #[test]
    fn the_feed_is_asked_for_with_its_size() {
        let (origin, served) = once("200 OK", "application/json", r#"{"data":[]}"#);
        let registry = registry_for(&origin);

        runtime()
            .block_on(public_document(&registry, "acc-1", "activity", Some(25)))
            .expect("the feed");

        assert_eq!(
            served.join().expect("the stub"),
            "GET /api/v1/social/activity?limit=25 HTTP/1.1"
        );
    }

    #[test]
    fn a_refusal_keeps_its_status_and_is_not_read_as_an_empty_feed() {
        let (origin, served) = once("500 Internal Server Error", "text/plain", "nope");
        let registry = registry_for(&origin);

        let failure = runtime()
            .block_on(public_document(&registry, "acc-1", "capabilities", None))
            .expect_err("a 500 is a refusal");

        assert_eq!(failure.kind, "http");
        assert_eq!(failure.status, Some(500));
        assert!(
            failure.message.contains("answered HTTP 500"),
            "{}",
            failure.message
        );
        served.join().expect("the stub");
    }

    #[test]
    fn a_document_that_is_not_json_says_so_rather_than_claiming_there_is_no_feed() {
        let (origin, served) = once("200 OK", "text/html", "<html>a login page</html>");
        let registry = registry_for(&origin);

        let failure = runtime()
            .block_on(public_document(&registry, "acc-1", "capabilities", None))
            .expect_err("HTML is not a capability document");

        assert_eq!(failure.kind, "json");
        served.join().expect("the stub");
    }

    #[test]
    fn an_unknown_document_and_an_unbound_account_are_refused_without_a_request() {
        let registry = registry_for("http://127.0.0.1:1");

        let unknown = runtime()
            .block_on(public_document(&registry, "acc-1", "tracks", None))
            .expect_err("no such document");
        assert_eq!(unknown.kind, "config");
        assert!(
            !unknown.message.contains("127.0.0.1"),
            "{}",
            unknown.message
        );

        let unbound = runtime()
            .block_on(public_document(&registry, "acc-2", "capabilities", None))
            .expect_err("no address");
        assert_eq!(unbound.kind, "config");
    }
}
