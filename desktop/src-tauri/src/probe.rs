// Two HTTP GETs, on purpose: the shell asks the server's own API and infers nothing.
//
// `reqwest` or `tauri-plugin-http` would bring a TLS stack and a runtime to a window that only
// ever speaks plain HTTP to loopback. `std::net` plus the code below is the smaller thing to
// review; the price is that this client understands `http://` only, which is all it is given.
//
// What a reviewer should check here is the failure behaviour, not the happy path: every error
// is a string the window can show. A server that is starting, stopped, or holding a port and
// not answering must all arrive in the window as "down, and here is why" — never as a hang.

use std::io::{Read, Write};
use std::net::{TcpStream, ToSocketAddrs};
use std::time::Duration;

pub struct Response {
    pub status: u16,
    pub body: String,
}

pub fn get(url: &str, timeout: Duration) -> Result<Response, String> {
    let rest = url
        .strip_prefix("http://")
        .ok_or_else(|| format!("not an http URL: {url}"))?;
    let (authority, path) = match rest.find('/') {
        Some(index) => (&rest[..index], &rest[index..]),
        None => (rest, "/"),
    };
    let (host, port) = split_authority(authority)?;
    let target = (host.as_str(), port)
        .to_socket_addrs()
        .map_err(|error| format!("{host}:{port}: {error}"))?
        .next()
        .ok_or_else(|| format!("{host}:{port} did not resolve"))?;
    let mut stream = TcpStream::connect_timeout(&target, timeout)
        .map_err(|error| format!("{host}:{port}: {error}"))?;
    let _ = stream.set_read_timeout(Some(timeout));
    let _ = stream.set_write_timeout(Some(timeout));

    // HTTP/1.1 with `Connection: close`: one request per connection, read to EOF, no keep-alive
    // state to leak between polls.
    let request = format!(
        "GET {path} HTTP/1.1\r\nHost: {authority}\r\nAccept: application/json\r\nConnection: close\r\n\r\n"
    );
    stream
        .write_all(request.as_bytes())
        .map_err(|error| error.to_string())?;
    let mut raw = Vec::new();
    stream
        .read_to_end(&mut raw)
        .map_err(|error| error.to_string())?;
    parse(&raw)
}

fn split_authority(authority: &str) -> Result<(String, u16), String> {
    if let Some(rest) = authority.strip_prefix('[') {
        let (host, tail) = rest
            .split_once(']')
            .ok_or_else(|| format!("bad authority: {authority}"))?;
        let port = tail.strip_prefix(':').unwrap_or("80");
        return Ok((
            host.to_string(),
            port.parse().map_err(|_| format!("bad port: {port}"))?,
        ));
    }
    match authority.rsplit_once(':') {
        Some((host, port)) => Ok((
            host.to_string(),
            port.parse().map_err(|_| format!("bad port: {port}"))?,
        )),
        None => Ok((authority.to_string(), 80)),
    }
}

fn parse(raw: &[u8]) -> Result<Response, String> {
    let split = raw
        .windows(4)
        .position(|window| window == b"\r\n\r\n")
        .ok_or_else(|| "the response had no header block".to_string())?;
    let head = String::from_utf8_lossy(&raw[..split]);
    let mut lines = head.lines();
    let status_line = lines
        .next()
        .ok_or_else(|| "the response had no status line".to_string())?;
    let status = status_line
        .split_whitespace()
        .nth(1)
        .and_then(|value| value.parse::<u16>().ok())
        .ok_or_else(|| format!("bad status line: {status_line}"))?;
    let chunked = lines.any(|line| {
        let lower = line.to_ascii_lowercase();
        lower.starts_with("transfer-encoding:") && lower.contains("chunked")
    });
    let body = &raw[split + 4..];
    let body = if chunked {
        dechunk(body)?
    } else {
        body.to_vec()
    };
    Ok(Response {
        status,
        body: String::from_utf8_lossy(&body).to_string(),
    })
}

fn dechunk(raw: &[u8]) -> Result<Vec<u8>, String> {
    let mut out = Vec::new();
    let mut rest = raw;
    loop {
        let end = rest
            .windows(2)
            .position(|window| window == b"\r\n")
            .ok_or_else(|| "truncated chunk header".to_string())?;
        let header = String::from_utf8_lossy(&rest[..end]);
        let size = usize::from_str_radix(header.split(';').next().unwrap_or("").trim(), 16)
            .map_err(|_| format!("bad chunk size: {header}"))?;
        rest = &rest[end + 2..];
        if size == 0 {
            return Ok(out);
        }
        if rest.len() < size + 2 {
            return Err("truncated chunk".to_string());
        }
        out.extend_from_slice(&rest[..size]);
        rest = &rest[size + 2..];
    }
}
