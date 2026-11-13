// The connect screen is the shell's first promise: it either proves the server answers Subsonic
// or it says precisely why not, and it keeps the password in memory for one session. `fetch` is
// stubbed here, so what is exercised is the screen's own logic — address normalisation, the
// envelope, the protocol's error codes — not a network or a browser.
const fs = require("fs");
const path = require("path");
const vm = require("vm");

const SOURCE = fs.readFileSync(path.join(__dirname, "..", "player", "shell", "connect.js"), "utf8");

let failures = 0;
function check(name, ok, detail) {
  if (ok) console.log("PASS " + name);
  else { failures++; console.log("FAIL " + name + (detail ? " — " + detail : "")); }
}

function jsonResponse(body, status = 200) {
  return {
    ok: status >= 200 && status < 300,
    status,
    headers: { get: () => "application/json" },
    json: async () => body,
  };
}

function pageResponse(html, status = 200) {
  return {
    ok: status >= 200 && status < 300,
    status,
    headers: { get: () => "text/html" },
    json: async () => { throw new SyntaxError("Unexpected token < in JSON"); },
  };
}

// What this server writes (listening.py's envelope): the fields the screen reports back.
function envelope(extra = {}) {
  return jsonResponse({ "subsonic-response": Object.assign({
    status: "ok", version: "1.16.1", type: "HardlyListening", serverVersion: "scoped-1",
    openSubsonic: true,
  }, extra) });
}

function refused(code, message) {
  return jsonResponse({ "subsonic-response": {
    status: "failed", version: "1.16.1", type: "HardlyListening", serverVersion: "scoped-1",
    error: { code, message },
  } });
}

// Each sandbox is one page load: its own session, its own recorded requests.
function load(respond) {
  const calls = [];
  const ctx = {
    URL, URLSearchParams, Promise, console, SyntaxError,
    fetch: async (url, options) => { calls.push({ url, options }); return respond(url, options); },
  };
  vm.createContext(ctx);
  vm.runInContext(SOURCE, ctx, { filename: "connect.js" });
  const api = vm.runInContext("this.PlayerConnect", ctx);
  return { api, calls };
}

const CREDENTIALS = { server: "https://music.example.com", username: "bazzite", password: "s3cret-pass" };

async function main() {
  // ---- the address -------------------------------------------------------
  {
    const { api, calls } = load(() => envelope());
    const result = await api.connect(Object.assign({}, CREDENTIALS, { server: "" }));
    check("an empty address is refused before any request",
      !result.ok && /server address/i.test(result.error) && calls.length === 0, result.error);
  }
  {
    const { api, calls } = load(() => envelope());
    const result = await api.connect(Object.assign({}, CREDENTIALS, { server: "music.example.com" }));
    check("an address without a scheme is refused, not guessed",
      !result.ok && /scheme/i.test(result.error) && calls.length === 0, result.error);
  }
  {
    const { api, calls } = load(() => envelope());
    const result = await api.connect(Object.assign({}, CREDENTIALS, { server: "file:///etc/passwd" }));
    check("only http(s) addresses are accepted",
      !result.ok && /http/i.test(result.error) && calls.length === 0, result.error);
  }
  {
    const { api, calls } = load(() => envelope());
    const result = await api.connect(Object.assign({}, CREDENTIALS, { server: "https://music.example.com/" }));
    check("a trailing slash asks the same URL, once",
      result.ok && calls.length === 1 && calls[0].url ===
        "https://music.example.com/rest/ping.view?u=bazzite&p=s3cret-pass&v=1.16.1&c=hardlylistening-player&f=json",
      calls.map(call => call.url).join(" "));
  }
  {
    const { api, calls } = load(() => envelope());
    const result = await api.connect(Object.assign({}, CREDENTIALS, { server: "https://host.example/subsonic/" }));
    check("a server behind a path keeps the path",
      result.ok && calls[0].url.startsWith("https://host.example/subsonic/rest/ping.view?"),
      calls[0] && calls[0].url);
  }
  {
    const { api, calls } = load(() => envelope());
    const result = await api.connect(Object.assign({}, CREDENTIALS, { server: "https://host.example/rest" }));
    check("a pasted /rest suffix is named rather than doubled",
      !result.ok && /\/rest/.test(result.error) && calls.length === 0, result.error);
  }

  // ---- the credentials ---------------------------------------------------
  {
    const { api, calls } = load(() => envelope());
    const missingUser = await api.connect({ server: "https://h.example", password: "x" });
    const missingPassword = await api.connect({ server: "https://h.example", username: "u" });
    check("a username and a password are both required",
      !missingUser.ok && !missingPassword.ok && calls.length === 0, missingUser.error);
  }

  // ---- the answer --------------------------------------------------------
  {
    const { api } = load(() => jsonResponse({ hello: "world" }));
    const result = await api.connect(CREDENTIALS);
    check("a 200 that is not a Subsonic envelope is refused",
      !result.ok && /not as a Subsonic server/i.test(result.error), result.error);
  }
  {
    const { api } = load(() => pageResponse("<!doctype html><title>Sign in</title>"));
    const result = await api.connect(CREDENTIALS);
    check("an HTML page is refused as JSON",
      !result.ok && /JSON/i.test(result.error), result.error);
  }
  {
    const { api } = load(() => refused(40, "Invalid device credentials"));
    const result = await api.connect(CREDENTIALS);
    check("code 40 says wrong credentials",
      !result.ok && result.code === 40 && /wrong username or password/i.test(result.error), result.error);
  }
  {
    const { api } = load(() => refused(30, "Incompatible Subsonic REST protocol version"));
    const result = await api.connect(CREDENTIALS);
    check("code 30 names the protocol, not a network fault",
      !result.ok && result.code === 30 && /version/i.test(result.error), result.error);
  }
  {
    const { api } = load(() => jsonResponse({}, 401));
    const result = await api.connect(CREDENTIALS);
    check("an HTTP 401 is the same answer as code 40",
      !result.ok && result.code === 40 && /wrong username or password/i.test(result.error), result.error);
  }
  {
    const { api } = load(() => jsonResponse({}, 502));
    const result = await api.connect(CREDENTIALS);
    check("a proxy error reports the status",
      !result.ok && /502/.test(result.error), result.error);
  }
  {
    const { api } = load(() => { throw new TypeError("NetworkError when attempting to fetch resource."); });
    const result = await api.connect(CREDENTIALS);
    check("a refused connection says which address it could not reach",
      !result.ok && /Could not reach https:\/\/music\.example\.com/.test(result.error), result.error);
  }
  {
    const { api, calls } = load(() => envelope({ status: "failed" }));
    const result = await api.connect(CREDENTIALS);
    check("a failed status without an error code is still a refusal",
      !result.ok && calls.length === 1, result.error);
  }

  // ---- success, and the memory-only session ------------------------------
  {
    const { api, calls } = load(() => envelope());
    check("no session exists before a server answers", api.current() === null,
      JSON.stringify(api.current()));
    const result = await api.connect(CREDENTIALS);
    const session = api.current();
    check("a good ping is a success with the server's identity",
      result.ok && result.server.type === "HardlyListening"
        && result.server.serverVersion === "scoped-1" && result.server.openSubsonic === true,
      JSON.stringify(result));
    check("the password is held in memory and never in the answer",
      !!session && session.credentials.password === "s3cret-pass"
        && !JSON.stringify(result).includes("s3cret-pass"),
      JSON.stringify(session && session.credentials));
    check("the session names the normalised address",
      !!session && session.server.url === "https://music.example.com", JSON.stringify(session && session.server));
    api.forget();
    check("forget() drops the password", api.current() === null, JSON.stringify(api.current()));
    check("one attempt is one request", calls.length === 1, String(calls.length));
  }
  {
    const { api } = load(() => refused(40, "Invalid device credentials"));
    await api.connect(CREDENTIALS);
    check("a refused attempt leaves no session behind", api.current() === null,
      JSON.stringify(api.current()));
  }

  // ---- the promise the screen makes about storage ------------------------
  {
    // Comments may name what is absent; code may not reach for it. Scan the statements.
    const code = SOURCE.replace(/\/\*[\s\S]*?\*\//g, "").replace(/^\s*\/\/.*$/gm, "");
    const storage = /localStorage|sessionStorage|indexedDB|document\.cookie|setItem|writeFile/i;
    check("the connect screen touches no storage at all", !storage.test(code),
      (code.match(storage) || [])[0]);
  }

  if (failures) {
    console.log(`${failures} failed`);
    process.exit(1);
  }
  console.log("all connect checks passed");
}

main().catch(error => {
  console.log("FAIL the harness threw — " + (error && error.stack || error));
  process.exit(1);
});