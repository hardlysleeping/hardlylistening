// HOST-006: the window's one line of truth about its address.
//
// `desktop/host/addresses.py` decides which address is usable (and is tested in
// `tests/test_host_addresses.py`). `desktop/ui/address.js` turns that decision into the string a
// person copies, the label saying which kind of address it is, and the reason it was chosen —
// or nothing at all. This file drives the renderer with the dictionaries the Python produces,
// including the malformed ones, because the interesting case is a window that has to stay quiet.
//
// The harness is the same shape as `tests/test_desktop_state.cjs`: read the shipped file, load
// it in a `vm` context, drive it with plain objects.
const fs = require("fs");
const path = require("path");
const vm = require("vm");

const UI = path.join(__dirname, "..", "desktop", "ui");
const source = fs.readFileSync(path.join(UI, "address.js"), "utf8");

let failures = 0;
function check(name, ok, detail) {
  if (ok) console.log("PASS " + name);
  else { failures++; console.log("FAIL " + name + (detail ? " — " + detail : "")); }
}

function load() {
  const ctx = { console };
  vm.createContext(ctx);
  vm.runInContext(source, ctx);
  if (!ctx.HostAddress) throw new Error("address.js did not publish HostAddress");
  return ctx.HostAddress;
}
const Address = load();

// The exact dictionary `addresses.detect(...).to_dict()` produces for a running tailnet.
function answer(primary, others, note) {
  return {
    bind: "0.0.0.0",
    scope: "any",
    tailscale: "up",
    tailscale_detail: "",
    primary: primary,
    others: others || [],
    note: note || ""
  };
}
const TAILNET = answer(
  { host: "studio.example.ts.net", kind: "magicdns", why: "Tailscale is running and gives this machine a name." },
  [
    { host: "100.101.102.103", kind: "tailscale", why: "Tailscale is running." },
    { host: "192.168.1.40", kind: "lan", why: "Devices on the same network can reach this." }
  ]
);

// ---- the four kinds, one label and one sentence each ---------------------
{
  const named = Address.describe(TAILNET, 8470);
  check("a tailnet name is what gets copied",
        named.url === "http://studio.example.ts.net:8470", named.url);
  check("the copy value is exactly the string shown, no punctuation",
        named.copy === named.url, named.copy);
  check("it says which kind of address it is", named.label === "Tailscale name", named.label);
  check("it carries the detector's reason", /gives this machine a name/.test(named.why), named.why);
  check("it is not silent", named.silent === false);
  check("it counts the other addresses without showing them",
        named.others === 2, String(named.others));

  const literal = Address.describe(answer({ host: "100.101.102.103", kind: "tailscale" }, []));
  check("a tailnet literal is labelled as one", literal.label === "Tailscale address", literal.label);
  check("a kind with no supplied reason still explains itself",
        literal.why.length > 20 && /tailnet/.test(literal.why), literal.why);

  const lan = Address.describe(answer({ host: "192.168.1.40", kind: "lan" }, []));
  check("a LAN address is labelled 'Local network'", lan.label === "Local network", lan.label);
  check("its default reason names the limitation, not a promise",
        /same network/.test(lan.why), lan.why);

  const bound = Address.describe(answer({ host: "192.168.1.50", kind: "bind" }, []));
  check("a configured bind is labelled as configured", bound.label === "As configured", bound.label);
}

// ---- ports ---------------------------------------------------------------
{
  check("the shell's port is used, not a guess",
        Address.describe(TAILNET, 9000).url === "http://studio.example.ts.net:9000",
        Address.describe(TAILNET, 9000).url);
  const fallback = Address.describe(answer({ host: "192.168.1.40", kind: "lan" }, []));
  check("a missing port falls back to the server's documented default",
        fallback.url === "http://192.168.1.40:8470", fallback.url);
  const wrong = Address.describe(answer({ host: "192.168.1.40", kind: "lan" }, []), "eighty");
  check("a nonsense port is the default, never a broken URL",
        wrong.url === "http://192.168.1.40:8470", wrong.url);
  const zero = Address.describe(answer({ host: "192.168.1.40", kind: "lan" }, []), 70000);
  check("an out-of-range port is refused the same way",
        zero.url === "http://192.168.1.40:8470", zero.url);
}

// ---- IPv6 ---------------------------------------------------------------
{
  const v6 = Address.describe(answer({ host: "fd7a:115c:a1e0::2", kind: "tailscale" }, []), 8471);
  check("IPv6 is bracketed in the URL", v6.url === "http://[fd7a:115c:a1e0::2]:8471", v6.url);
  check("the copied string keeps the brackets", v6.copy === v6.url, v6.copy);
  const zone = Address.describe(answer({ host: "fe80::1%wlp6s0", kind: "lan" }, []), 8470);
  check("a zone id is dropped from the URL", zone.url === "http://[fe80::1]:8470", zone.url);
  const bracketed = Address.describe(answer({ host: "[fd7a:115c:a1e0::2]", kind: "tailscale" }, []));
  check("an already-bracketed host is not double bracketed",
        bracketed.url === "http://[fd7a:115c:a1e0::2]:8470", bracketed.url);
}

// ---- silence -------------------------------------------------------------
{
  const loopback = Address.describe({ bind: "127.0.0.1", scope: "loopback", tailscale: "unread",
    primary: null, others: [], note: "This server is listening on this computer only." }, 8470);
  check("a loopback bind renders nothing to copy", loopback.silent === true && loopback.url === "",
        loopback.url);
  check("but its explanation survives",
        /this computer only/.test(loopback.note), loopback.note);
  check("a silent answer has no copy value", loopback.copy === "", loopback.copy);

  const empty = Address.describe({ primary: null, others: [], note: "" }, 8470);
  check("an answer with nothing in it is silent", empty.silent === true && empty.note === "");

  check("no answer at all is silent", Address.describe(null, 8470).silent === true);
  check("an undefined answer is silent", Address.describe(undefined).silent === true);
  check("a string answer is silent", Address.describe("http://10.0.0.1:8470").silent === true);

  const unknownKind = Address.describe(answer({ host: "192.168.1.40", kind: "carrier-pigeon" }, []));
  check("an unrecognised kind is not rendered with a guessed label",
        unknownKind.silent === true, unknownKind.label);

  const noHost = Address.describe(answer({ host: "", kind: "lan" }, []));
  check("a primary with no host is silent", noHost.silent === true);

  const loopbackAsLan = Address.describe(answer({ host: "127.0.0.1", kind: "lan" }, []));
  check("a loopback address wearing a LAN label is refused", loopbackAsLan.silent === true,
        loopbackAsLan.url);

  const anyAsLan = Address.describe(answer({ host: "0.0.0.0", kind: "lan" }, []));
  check("a listen-anywhere address is never presented as a destination",
        anyAsLan.silent === true, anyAsLan.url);

  const spaced = Address.describe(answer({ host: "not an address", kind: "lan" }, []));
  check("a host with whitespace is not a URL", spaced.silent === true, spaced.url);
}

// ---- the detector's own answer is what the window trusts -----------------
{
  // `addresses.py` never offers a loopback primary, and this renderer never unbinds a bind that
  // is not reachable. Both sides are read here so a kind renamed in one is caught in the other.
  const pythonSource = fs.readFileSync(path.join(__dirname, "..", "desktop", "host", "addresses.py"), "utf8");
  ["bind", "magicdns", "tailscale", "lan"].forEach(kind => {
    check("the window can label the detector's '" + kind + "' kind",
          pythonSource.indexOf("'" + kind + "'") !== -1 && !!Address.KINDS[kind]);
  });
}

// ---- the renderer does not mutate what it is given -----------------------
{
  const before = JSON.stringify(TAILNET);
  Address.describe(TAILNET, 8470);
  check("describing an answer leaves it untouched", JSON.stringify(TAILNET) === before);

  const odd = answer({ host: "192.168.1.40", kind: "lan" }, [null, {}, { host: "bad host" }]);
  const beforeOdd = JSON.stringify(odd);
  const described = Address.describe(odd, 8470);
  check("junk in the alternates is skipped, not thrown at the person",
        described.silent === false && described.others === 0, String(described.others));
  check("and the input is still untouched", JSON.stringify(odd) === beforeOdd);
}

// ---- alternates ----------------------------------------------------------
{
  const duplicated = answer({ host: "192.168.1.40", kind: "lan" },
    [{ host: "192.168.1.40", kind: "lan" }, { host: "10.0.0.5", kind: "lan" }]);
  const described = Address.describe(duplicated, 8470);
  check("the primary is not counted again among the alternates",
        described.others === 1, String(described.others));

  const sameName = answer({ host: "studio.example.ts.net", kind: "magicdns" },
    [{ host: "Studio.Example.TS.net", kind: "magicdns" }, { host: "100.1.2.3", kind: "tailscale" }]);
  check("alternate names are compared case-insensitively",
        Address.describe(sameName, 8470).others === 1,
        String(Address.describe(sameName, 8470).others));
}

// ---- the labels cover every kind ----------------------------------------
{
  const kinds = Object.keys(Address.KINDS);
  check("four kinds are labelled", kinds.length === 4, kinds.join(","));
  check("every kind has a non-empty label and a full sentence",
        kinds.every(kind => Address.KINDS[kind].label.length > 3 && Address.KINDS[kind].why.length > 20));
  check("an unknown kind has no label", Address.label("carrier-pigeon") === "");
  check("the renderer knows the default port", Address.DEFAULT_PORT === 8470);
}

if (failures) { console.log(`\n${failures} failure(s)`); process.exit(1); }