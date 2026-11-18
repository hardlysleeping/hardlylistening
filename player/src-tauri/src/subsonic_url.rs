//! The address a native request goes to, and the credential that goes with it.
//!
//! This file is deliberately free of Tauri and of the network. It answers one question — "given
//! an account, an endpoint name and that endpoint's parameters, what is the URL?" — because that
//! is the question a hostile page would like to influence and the one worth testing on a machine
//! that cannot build the shell at all.
//!
//! Three rules, each of which a caller cannot work around:
//!
//! - **The origin is the account's.** It is validated once, when the account is bound, and the
//!   request builder takes it from the bound record. There is no argument by which a caller
//!   supplies a host.
//! - **The endpoint is a name, not a path.** `getAlbumList2` and nothing else: no `/`, no `..`,
//!   no second host, no query string of its own.
//! - **The protocol's parameters are ours.** `u`, `p`, `t`, `s`, `v`, `c` and `f` are written by
//!   [`auth_query`], and [`params_ok`] refuses any caller that tries to write them.
//!
//! The salt is fresh per request and comes from the OS random source, so the token is not a
//! reusable password — the reason the front end never needs to see the secret once the vault has
//! it.

use std::collections::HashMap;
use std::sync::Mutex;

use md5::{Digest, Md5};

/// The protocol version this client speaks, and the name it reports. The same two constants the
/// front end's `auth.ts` carries; a request built here must look like a request built there.
pub const API_VERSION: &str = "1.16.1";
pub const CLIENT_NAME: &str = "hardlylistening-player";

/// The forms a Subsonic server can accept. Recorded when the server accepted one, never guessed.
#[derive(Clone, Copy, PartialEq, Eq, Debug)]
pub enum AuthForm {
    /// `u` + `p`. The password crosses the wire, so it is only used when the server refused token.
    Password,
    /// `u` + `t` + `s`: the md5 of the password and a fresh salt.
    Token,
    /// The host app's issued device credential. It is sent as a password.
    DeviceInvite,
}

impl AuthForm {
    pub fn parse(raw: &str) -> Result<AuthForm, String> {
        match raw {
            "password" => Ok(AuthForm::Password),
            "token" => Ok(AuthForm::Token),
            "device-invite" => Ok(AuthForm::DeviceInvite),
            other => Err(format!("\"{other}\" is not a form this player knows")),
        }
    }

    pub fn as_str(self) -> &'static str {
        match self {
            AuthForm::Password => "password",
            AuthForm::Token => "token",
            AuthForm::DeviceInvite => "device-invite",
        }
    }
}

/// `endpoint` is a Subsonic view name: one ASCII letter, then letters and digits.
pub fn endpoint_ok(endpoint: &str) -> bool {
    let mut chars = endpoint.chars();
    matches!(chars.next(), Some(first) if first.is_ascii_alphabetic())
        && endpoint.len() <= 64
        && chars.all(|c| c.is_ascii_alphanumeric())
}

/// The parameters the protocol owns. A caller that sets one is choosing a credential.
const RESERVED: [&str; 7] = ["u", "p", "t", "s", "v", "c", "f"];

/// Whether a caller may send this parameter name and value.
pub fn params_ok(params: &[(String, String)]) -> Result<(), String> {
    for (key, _) in params {
        if RESERVED.contains(&key.as_str()) {
            return Err(format!(
                "the player writes \"{key}\" itself; a caller may not override it"
            ));
        }
        if !endpoint_ok(key) {
            return Err(format!("\"{key}\" is not a parameter name"));
        }
    }
    Ok(())
}

/// A server address: scheme, host, optional port and optional path — the part *before* `/rest`.
///
/// Trailing slashes are dropped so `…/` and `…` ask the same URL. A missing scheme is refused
/// rather than invented, an address carrying `user:pass@` is refused rather than stripped, and a
/// pasted `/rest` is refused with the fix in the message instead of being silently trimmed.
pub fn normalise_origin(raw: &str) -> Result<String, String> {
    let value = raw.trim();
    if value.is_empty() {
        return Err("Enter the server address — for example https://music.example.com".to_string());
    }
    let lower = value.to_ascii_lowercase();
    let scheme_len = if lower.starts_with("https://") {
        "https://".len()
    } else if lower.starts_with("http://") {
        "http://".len()
    } else {
        return Err(
            "That address is missing its scheme. Try https://music.example.com".to_string(),
        );
    };

    let after = &value[scheme_len..];
    if after.is_empty() {
        return Err("That address has no host".to_string());
    }
    if value.chars().any(|c| c.is_control() || c.is_whitespace()) {
        return Err("That address contains a space or a control character".to_string());
    }

    let authority_end = after
        .find(|c| c == '/' || c == '?' || c == '#')
        .unwrap_or(after.len());
    let authority = &after[..authority_end];
    if authority.is_empty() {
        return Err("That address has no host".to_string());
    }
    if authority.contains('@') {
        return Err(
            "That address carries a username or password. Remove the part before @".to_string(),
        );
    }
    let authority_ok = authority
        .chars()
        .all(|c| c.is_ascii_alphanumeric() || matches!(c, '.' | '-' | ':' | '[' | ']' | '_'));
    if !authority_ok {
        return Err("That address has a character a host cannot contain".to_string());
    }

    let mut path = after[authority_end..].to_string();
    if path.contains('?') || path.contains('#') {
        return Err(
            "That address carries a query or a fragment. Use the server's address only".to_string(),
        );
    }
    while path.ends_with('/') {
        path.pop();
    }
    let trimmed_path = path.to_ascii_lowercase();
    if trimmed_path == "/rest" || trimmed_path.starts_with("/rest/") {
        return Err("Drop the /rest from that address — the player adds it".to_string());
    }
    if trimmed_path.ends_with("/rest") {
        return Err("Drop the /rest from that address — the player adds it".to_string());
    }

    Ok(format!("{}{}{}", &value[..scheme_len], authority, path))
}

/// One bound account: the address and the name the credential belongs to. The secret is not
/// here — it stays in the vault and is read per request, so this record is safe to keep in memory
/// and to log.
#[derive(Clone, Debug, PartialEq, Eq)]
pub struct Bound {
    pub origin: String,
    pub username: String,
    pub form: AuthForm,
}

impl Bound {
    pub fn new(origin: &str, username: &str, form: &str) -> Result<Bound, String> {
        let username = username.trim();
        if username.is_empty() {
            return Err("Enter the username the server gave you".to_string());
        }
        Ok(Bound {
            origin: normalise_origin(origin)?,
            username: username.to_string(),
            form: AuthForm::parse(form)?,
        })
    }
}

/// The accounts this process has bound, by the id the vault filed their secret under.
///
/// This is what "pinned" means in practice: a request looks its address up here by account id,
/// and an id nobody bound has no address to ask. Signing out forgets the record, so the pin lasts
/// exactly as long as the session that made it.
pub struct Registry {
    bound: Mutex<HashMap<String, Bound>>,
}

impl Registry {
    pub fn new() -> Registry {
        Registry {
            bound: Mutex::new(HashMap::new()),
        }
    }

    /// Bind (or rebind) one account. Rebinding is how a server that refused token authentication
    /// gets the same account with the password form; there is still one address per id.
    pub fn bind(&self, account: &str, bound: Bound) -> Result<(), String> {
        self.bound
            .lock()
            .map_err(|_| "the bound-account table is unusable".to_string())?
            .insert(account.to_string(), bound);
        Ok(())
    }

    pub fn bound(&self, account: &str) -> Option<Bound> {
        self.bound.lock().ok()?.get(account).cloned()
    }

    /// Forget an account. An id that was never bound is the state the caller asked for.
    pub fn forget(&self, account: &str) {
        if let Ok(mut map) = self.bound.lock() {
            map.remove(account);
        }
    }
}

/// Sixteen random bytes, hexadecimal. Fresh for every request, so a token is not a password.
pub fn salt() -> String {
    let mut bytes = [0u8; 16];
    // The OS source is the only acceptable one here: a predictable salt turns the token back
    // into a reusable credential.
    getrandom::fill(&mut bytes).expect("the OS random source is unavailable");
    to_hex(&bytes)
}

fn to_hex(bytes: &[u8]) -> String {
    const DIGITS: &[u8; 16] = b"0123456789abcdef";
    let mut out = String::with_capacity(bytes.len() * 2);
    for byte in bytes {
        out.push(DIGITS[(byte >> 4) as usize] as char);
        out.push(DIGITS[(byte & 0x0f) as usize] as char);
    }
    out
}

/// Percent-encoding for a query value. Unreserved characters pass; everything else becomes
/// `%XX` of its UTF-8 bytes, which is what `URLSearchParams` does on the web path.
pub fn encode(value: &str) -> String {
    let mut out = String::with_capacity(value.len());
    for byte in value.as_bytes() {
        let unreserved = byte.is_ascii_alphanumeric() || matches!(byte, b'-' | b'.' | b'_' | b'~');
        if unreserved {
            out.push(*byte as char);
        } else {
            out.push('%');
            out.push_str(&format!("{byte:02X}"));
        }
    }
    out
}

/// The protocol and credential parameters for one request: `u`, whichever secret parameter the
/// bound form calls for, `v`, `c`, `f`.
///
/// The secret never appears in the result for token form — only the md5 of it and a fresh salt.
pub fn auth_query(bound: &Bound, secret: &str, salt: &str) -> Vec<(String, String)> {
    let mut out = vec![
        ("u".to_string(), bound.username.clone()),
        ("v".to_string(), API_VERSION.to_string()),
        ("c".to_string(), CLIENT_NAME.to_string()),
        ("f".to_string(), "json".to_string()),
    ];
    match bound.form {
        AuthForm::Token => {
            let mut hasher = Md5::new();
            hasher.update(secret.as_bytes());
            hasher.update(salt.as_bytes());
            out.push(("t".to_string(), to_hex(&hasher.finalize())));
            out.push(("s".to_string(), salt.to_string()));
        }
        AuthForm::Password | AuthForm::DeviceInvite => {
            out.push(("p".to_string(), secret.to_string()));
        }
    }
    out
}

/* ---- the server's own public documents ------------------------------------ */

/// The two documents this build reads outside `/rest`: a capabilities probe and the feed.
const PUBLIC_CAPABILITIES: &str = "capabilities";
const PUBLIC_ACTIVITY: &str = "activity";
/// What the app's own feed asks for, and what the server clamps to.
pub const PUBLIC_ACTIVITY_LIMIT: u32 = 80;
const PUBLIC_ACTIVITY_MAX: u32 = 200;

/// The path for one public read, by the name the page uses.
///
/// The name is matched, never concatenated: a resource the page invents has no path, so this
/// function cannot be talked into reaching an address of the caller's choosing.
pub fn public_path(resource: &str) -> Result<&'static str, String> {
    match resource {
        PUBLIC_CAPABILITIES => Ok("/api/v1/capabilities"),
        PUBLIC_ACTIVITY => Ok("/api/v1/social/activity"),
        other => Err(format!(
            "The player reads two public documents, and \"{other}\" is neither of them"
        )),
    }
}

/// One public read's URL: the bound account's origin, the document's own path, and **no
/// credential** — both documents are public, and the page's probe deliberately sends none.
pub fn public_url(bound: &Bound, resource: &str, limit: Option<u32>) -> Result<String, String> {
    let path = public_path(resource)?;
    let mut url = format!("{}{}", bound.origin, path);
    if resource == PUBLIC_ACTIVITY {
        // The server clamps to 1..=200; asking for less is asking for less.
        let asked = limit
            .unwrap_or(PUBLIC_ACTIVITY_LIMIT)
            .clamp(1, PUBLIC_ACTIVITY_MAX);
        url.push_str(&format!("?limit={asked}"));
    }
    Ok(url)
}

/// The URL for one request. `params` were checked by [`params_ok`] before they got here; they are
/// encoded again regardless, because a value is data and not a fragment of the query.
pub fn request_url(
    bound: &Bound,
    secret: &str,
    salt: &str,
    endpoint: &str,
    params: &[(String, String)],
) -> String {
    let mut query = auth_query(bound, secret, salt);
    query.extend(params.iter().cloned());
    let encoded: Vec<String> = query
        .iter()
        .map(|(key, value)| format!("{}={}", encode(key), encode(value)))
        .collect();
    format!(
        "{}/rest/{}.view?{}",
        bound.origin,
        endpoint,
        encoded.join("&")
    )
}

#[cfg(test)]
mod tests {
    use super::*;

    fn pair(key: &str, value: &str) -> (String, String) {
        (key.to_string(), value.to_string())
    }

    fn bound(form: &str) -> Bound {
        Bound::new("https://music.example.com", "alex", form).expect("a normal address")
    }

    #[test]
    fn an_origin_is_a_scheme_a_host_and_an_optional_path() {
        assert_eq!(
            normalise_origin("https://music.example.com").unwrap(),
            "https://music.example.com"
        );
        // A trailing slash asks the same server, so it is the same address.
        assert_eq!(
            normalise_origin("  https://music.example.com/  ").unwrap(),
            "https://music.example.com"
        );
        // A server behind a proxy keeps its path prefix.
        assert_eq!(
            normalise_origin("http://host:4533/music/").unwrap(),
            "http://host:4533/music"
        );

        // The refusal says what to remove, not just that something is wrong.
        let userinfo = normalise_origin("https://user:pw@music.example.com").unwrap_err();
        assert!(userinfo.contains('@'), "{userinfo}");

        for refused in [
            "",
            "music.example.com",
            "ftp://music.example.com",
            "https://user:pw@music.example.com",
            "https://music.example.com/rest",
            "https://music.example.com/rest/",
            "https://music.example.com?x=1",
            "https://music.example.com/#x",
        ] {
            assert!(
                normalise_origin(refused).is_err(),
                "{refused:?} should have been refused"
            );
        }
    }

    #[test]
    fn an_endpoint_is_a_bare_name() {
        assert!(endpoint_ok("ping"));
        assert!(endpoint_ok("getAlbumList2"));
        assert!(endpoint_ok("getLyricsBySongId"));

        for refused in [
            "",
            "getAlbumList2.view",
            "getAlbumList2?x=1",
            "../ping",
            "rest/ping",
            "https://elsewhere.example/rest/ping",
            "get Album",
            "2albums",
        ] {
            assert!(
                !endpoint_ok(refused),
                "{refused:?} should have been refused"
            );
        }
        assert!(!endpoint_ok(&"a".repeat(65)));
    }

    #[test]
    fn a_caller_may_not_write_the_protocols_own_parameters() {
        for reserved in RESERVED {
            assert!(
                params_ok(&[pair(reserved, "x")]).is_err(),
                "{reserved} should have been refused"
            );
        }
        assert!(params_ok(&[]).is_ok());
        assert!(params_ok(&[pair("id", "42"), pair("size", "50")]).is_ok());
        assert!(params_ok(&[pair("a b", "1")]).is_err());
    }

    #[test]
    fn token_auth_carries_the_md5_of_the_secret_and_a_salt_never_the_secret() {
        let url = request_url(&bound("token"), "hunter2", "abc", "ping", &[]);

        // md5("hunter2abc") — the same value the front end's own md5 would produce.
        assert!(url.contains("t=defdf93cef7fa7a8ee88e65d0e277b99"), "{url}");
        assert!(url.contains("s=abc"), "{url}");
        assert!(url.contains("u=alex"), "{url}");
        assert!(url.contains("v=1.16.1"), "{url}");
        assert!(url.contains("c=hardlylistening-player"), "{url}");
        assert!(url.contains("f=json"), "{url}");
        assert!(
            !url.contains("hunter2"),
            "the secret must never be in the URL"
        );
        assert!(!url.contains("p="), "token form does not send a password");
    }

    #[test]
    fn password_auth_carries_the_password_as_a_value_and_not_as_text() {
        let url = request_url(&bound("password"), "hunter 2&x=1", "abc", "ping", &[]);

        assert!(url.contains("p=hunter%202%26x%3D1"), "{url}");
        assert!(!url.contains("t="), "password form does not send a token");
        assert!(!url.contains("s="), "password form does not send a salt");
        // The value did not become a parameter of its own.
        assert_eq!(url.matches("&x=").count(), 0, "{url}");
    }

    #[test]
    fn a_request_joins_the_path_without_doubling_the_slash() {
        let bound = Bound::new("https://music.example.com/music/", "alex", "token").unwrap();
        let url = request_url(
            &bound,
            "s",
            "abc",
            "getAlbumList2",
            &[pair("type", "alphabeticalByName")],
        );

        assert!(
            url.starts_with("https://music.example.com/music/rest/getAlbumList2.view?"),
            "{url}"
        );
        assert!(url.contains("type=alphabeticalByName"), "{url}");
        assert!(!url.contains("//rest"), "{url}");
    }

    #[test]
    fn a_bound_account_is_read_back_by_its_own_id_only() {
        let registry = Registry::new();
        registry
            .bind(
                "acc-1",
                Bound::new("https://one.example.com", "alex", "token").unwrap(),
            )
            .unwrap();

        assert_eq!(
            registry.bound("acc-1").unwrap().origin,
            "https://one.example.com"
        );
        // Nothing was invented for an account nobody bound.
        assert!(registry.bound("acc-2").is_none());

        registry.forget("acc-1");
        assert!(registry.bound("acc-1").is_none());
    }

    #[test]
    fn rebinding_an_account_replaces_its_address_rather_than_adding_one() {
        let registry = Registry::new();
        registry
            .bind(
                "acc-1",
                Bound::new("https://one.example.com", "alex", "token").unwrap(),
            )
            .unwrap();
        // The server refused token auth; the same account now uses the password form.
        registry
            .bind(
                "acc-1",
                Bound::new("https://one.example.com", "alex", "password").unwrap(),
            )
            .unwrap();

        assert_eq!(registry.bound("acc-1").unwrap().form, AuthForm::Password);
    }

    #[test]
    fn the_salt_is_fresh_every_time_and_hexadecimal() {
        let first = salt();
        let second = salt();

        assert_eq!(first.len(), 32);
        assert!(first.chars().all(|c| c.is_ascii_hexdigit()));
        assert_ne!(first, second, "two requests must not share a salt");
    }

    /* ---- the public reads ------------------------------------------------- */

    /// A bound account with the token form, for the public reads. The helper above takes a form;
    /// this one exists so these tests read as "the same account, three documents".
    fn an_account() -> Bound {
        bound("token")
    }

    #[test]
    fn a_public_read_names_the_document_and_carries_no_credential() {
        let url = public_url(&an_account(), "capabilities", None).unwrap();

        assert_eq!(url, "https://music.example.com/api/v1/capabilities");
        // The whole reason this path exists: nothing in it identifies anyone.
        for key in ["u=", "t=", "s=", "p=", "v=", "c=", "f="] {
            assert!(!url.contains(key), "{url} carries {key}");
        }
    }

    #[test]
    fn the_feed_says_how_much_it_wants_and_the_server_gets_the_last_word() {
        assert_eq!(
            public_url(&an_account(), "activity", Some(25)).unwrap(),
            "https://music.example.com/api/v1/social/activity?limit=25"
        );
        // Asked for nothing: the app's own default, not the server's maximum.
        assert_eq!(
            public_url(&an_account(), "activity", None).unwrap(),
            "https://music.example.com/api/v1/social/activity?limit=80"
        );
        // Out of range is clamped here as well as there, so a nonsense number never travels.
        assert_eq!(
            public_url(&an_account(), "activity", Some(10_000)).unwrap(),
            "https://music.example.com/api/v1/social/activity?limit=200"
        );
        assert_eq!(
            public_url(&an_account(), "activity", Some(0)).unwrap(),
            "https://music.example.com/api/v1/social/activity?limit=1"
        );
    }

    #[test]
    fn a_resource_nobody_named_is_refused_rather_than_turned_into_a_path() {
        for asked in [
            "",
            "/api/v1/capabilities",
            "../admin",
            "capabilities?x=1",
            "ACTIVITY",
        ] {
            assert!(
                public_url(&an_account(), asked, None).is_err(),
                "\"{asked}\" should not name a document"
            );
        }
        // The two names that do work are exactly the two the page can ask for.
        assert!(public_path("capabilities").is_ok());
        assert!(public_path("activity").is_ok());
    }

    #[test]
    fn a_public_read_keeps_an_address_that_lives_under_a_path() {
        // A reverse proxy is a normal deployment; the document is under the same base path.
        let sub = Bound::new("https://example.com/music", "alex", "token").unwrap();
        assert_eq!(
            public_url(&sub, "capabilities", None).unwrap(),
            "https://example.com/music/api/v1/capabilities"
        );
    }
}
