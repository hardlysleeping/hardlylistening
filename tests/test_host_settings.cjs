// HOST-005's panel, driven without a window.
//
// `desktop/ui/settings.js` holds the panel's decisions: what a change means, whether a restart is
// needed, and which fields are refused because the server has no switch for them. The Rust side
// only reads and writes files, and the crate cannot be compiled on this machine, so this is the
// executable proof that the panel cannot promise something the server does not do.
//
// Same harness as `tests/test_desktop_state.cjs`: read the shipped file, run it in a `vm`
// context, drive it with plain objects, assert on what a person would see.
const fs = require("fs");
const path = require("path");
const vm = require("vm");

const UI = path.join(__dirname, "..", "desktop", "ui");
const source = fs.readFileSync(path.join(UI, "settings.js"), "utf8");

let failures = 0;
function check(name, ok, detail) {
  if (ok) console.log("PASS " + name);
  else { failures++; console.log("FAIL " + name + (detail ? " — " + detail : "")); }
}

function load() {
  const ctx = { console };
  vm.createContext(ctx);
  vm.runInContext(source, ctx);
  if (!ctx.Settings) throw new Error("settings.js did not publish Settings");
  return ctx.Settings;
}
const Settings = load();

// The shape `settings_read` and `host_env` hand the panel.
function addressOf(host, port) {
  return { address: { host: host, port: port, source: "desktop-config" } };
}

// ---- the untouched panel -------------------------------------------------
{
  const m = Settings.model({});
  check("with nothing stored the panel shows the server's defaults",
        m.host === "127.0.0.1" && m.port === 8470, m.host + ":" + m.port);
  check("the default exposure is local", m.scope === Settings.LOCAL, m.scope);
  check("the address is printed the way the server prints it", m.listen === "127.0.0.1:8470", m.listen);

  const d = Settings.draft(m);
  const view = Settings.decide(m, d);
  check("nothing changed: Save is disabled", !view.save.enabled && !view.dirty);
  check("a disabled Save says why", view.save.hint === "Nothing has changed.", view.save.hint);
  check("the local exposure is the active one", view.scope.local.active && !view.scope.lan.active);
  check("autostart starts off", view.autostart.off.active && !view.autostart.on.active);
  check("nothing to apply is not a restart", !Settings.diff(m, d).restartRequired);
}

// ---- local only vs the LAN is one bind address, nothing invented --------
{
  check("127.0.0.1 is local", Settings.scopeOf("127.0.0.1") === Settings.LOCAL);
  check("localhost is local", Settings.scopeOf("localhost") === Settings.LOCAL);
  check("0.0.0.0 is the LAN exposure", Settings.scopeOf("0.0.0.0") === Settings.LAN);
  check("a literal interface address is a LAN exposure",
        Settings.scopeOf("192.168.1.50") === Settings.LAN);
  check("local and LAN map to the only bind control the server has",
        Settings.hostForScope(Settings.LOCAL) === "127.0.0.1" &&
        Settings.hostForScope(Settings.LAN) === "0.0.0.0");

  const m = Settings.model(addressOf("127.0.0.1", 8470));
  const lan = Settings.setScope(Settings.draft(m), Settings.LAN);
  const view = Settings.decide(m, lan);
  const pending = Settings.diff(m, lan);
  check("choosing the LAN makes the Save button live", view.save.enabled && view.dirty);
  check("a new bind address is a restart", pending.restartRequired && pending.restart[0] === "host",
        JSON.stringify(pending.restart));
  check("the Save hint warns before the click, not after",
        /Restart/.test(view.save.hint) || /restart/.test(view.save.hint), view.save.hint);
  check("the diff names both addresses",
        pending.before === "127.0.0.1:8470" && pending.after === "0.0.0.0:8470",
        pending.before + " -> " + pending.after);
  check("the summary says where the server keeps answering",
        /127\.0\.0\.1:8470/.test(pending.summary) && /0\.0\.0\.0:8470/.test(pending.summary),
        pending.summary);
  check("only the exposure changed", pending.changes.length === 1 &&
        pending.changes[0].field === "host", JSON.stringify(pending.changes));
}

// ---- the port is a setting too, and also a restart ----------------------
{
  const m = Settings.model(addressOf("127.0.0.1", 8470));
  const d = Object.assign(Settings.draft(m), { port: 9000 });
  const pending = Settings.diff(m, d);
  check("a new port is a restart", pending.restartRequired && pending.restart[0] === "port",
        JSON.stringify(pending.restart));
  check("the port change is labelled for a person", pending.changes[0].label === "Port",
        pending.changes[0].label);
}

// ---- the web UI and OpenSubsonic have no switch ------------------------
{
  const rows = Settings.rows(Settings.model({}));
  const byKey = {};
  rows.forEach(row => { byKey[row.key] = row; });
  check("the panel describes exactly the card's four questions plus the port",
        JSON.stringify(rows.map(r => r.key)) ===
        JSON.stringify(["host", "port", "web_ui", "subsonic", "autostart"]),
        rows.map(r => r.key).join(","));
  check("the web UI is a fact, not a toggle",
        byKey.web_ui.supported === false && byKey.web_ui.editable === false);
  check("its reason names the code that would have to change",
        /web\//.test(byKey.web_ui.detail), byKey.web_ui.detail);
  check("OpenSubsonic is a fact, not a toggle",
        byKey.subsonic.supported === false && byKey.subsonic.editable === false);
  check("its reason names the /rest/ surface",
        /\/rest\//.test(byKey.subsonic.detail), byKey.subsonic.detail);
  check("neither claims a value that would imply a switch",
        byKey.web_ui.value === "Always served" && byKey.subsonic.value === "Always on",
        byKey.web_ui.value + " / " + byKey.subsonic.value);
  check("both unsupported rows carry a reason long enough to be a sentence",
        byKey.web_ui.detail.length > 60 && byKey.subsonic.detail.length > 60);
  check("the two unsupported settings are exactly those two",
        JSON.stringify(Object.keys(Settings.UNSUPPORTED).sort()) ===
        JSON.stringify(["subsonic", "web_ui"]));
}

// ---- autostart is only ever an explicit choice -------------------------
{
  const m = Settings.model(Object.assign(addressOf("127.0.0.1", 8470), { autostart: false }));
  const untouched = Settings.draft(m);
  check("an untouched draft never asks for the entry to move",
        Settings.diff(m, untouched).changes.length === 0);

  const on = Settings.setAutostart(untouched, true);
  const pending = Settings.diff(m, on);
  check("turning it on is a change", Settings.dirty(m, on));
  check("turning it on does not ask for a server restart", !pending.restartRequired,
        JSON.stringify(pending.restart));
  check("the entry change is labelled, not hidden", pending.changes.length === 1 &&
        pending.changes[0].field === "autostart", JSON.stringify(pending.changes));
  check("opting in reports 'Saved', not a restart", pending.summary === "Saved.", pending.summary);

  const already = Settings.model(Object.assign(addressOf("127.0.0.1", 8470), { autostart: true }));
  check("toggling it back off is a change too", Settings.dirty(already,
        Settings.setAutostart(Settings.draft(already), false)));

  const unsupported = Settings.model(Object.assign(addressOf("127.0.0.1", 8470),
    { autostartSupported: false }));
  const view = Settings.decide(unsupported, Settings.draft(unsupported));
  check("on a platform with no entry, the buttons are refused",
        !view.autostart.on.enabled && !view.autostart.off.enabled);
  const row = Settings.rows(unsupported).filter(r => r.key === "autostart")[0];
  check("and the row says why rather than looking broken",
        row.supported === false && /XDG/.test(row.detail), row.detail);
}

// ---- the environment wins, and the panel says so ----------------------
{
  const m = Settings.model(Object.assign(addressOf("0.0.0.0", 8470),
    { locked: ["host"], source: "environment" }));
  const view = Settings.decide(m, Settings.setScope(Settings.draft(m), Settings.LOCAL));
  check("a locked exposure cannot be chosen", !view.scope.local.enabled && !view.scope.lan.enabled);
  check("a locked exposure is not dirty either: the click changes nothing",
        !view.dirty && !view.save.enabled);
  check("the hint names the variable that wins",
        /HARDLYLISTENING_HOST/.test(view.save.hint), view.save.hint);
  check("the row is marked locked", Settings.rows(m)[0].locked === true);
}

// ---- what the window says after a write ------------------------------
{
  const refused = Settings.outcome({ saved: false, refused: [{ field: "web_ui", reason: "…" }] });
  check("a refused field is named, not silently skipped", /web_ui/.test(refused.text), refused.text);
  check("a refusal is a warning", refused.kind === "warn", refused.kind);

  const restarted = Settings.outcome({
    saved: true,
    restart_required: true,
    settings: { host: "0.0.0.0", port: 8470 },
    before: { host: "127.0.0.1", port: 8470 }
  });
  check("a saved address change asks for the restart in the same sentence",
        /0\.0\.0\.0:8470/.test(restarted.text) && /127\.0\.0\.1:8470/.test(restarted.text),
        restarted.text);

  const started = Settings.outcome({ saved: true, autostart: "written" });
  check("an installed entry says it starts the server, not the window",
        /not this window/.test(started.text), started.text);

  const stopped = Settings.outcome({ saved: true, autostart: "removed" });
  check("removing it says so", /now off/.test(stopped.text), stopped.text);

  const nothing = Settings.outcome({ saved: false, autostart: "unchanged", refused: [] });
  check("a save that changed nothing says that", /Nothing has been changed/.test(nothing.text),
        nothing.text);
}

// ---- problems are shown, and never swallowed ---------------------------
{
  const m = Settings.model(Object.assign(addressOf("127.0.0.1", 8470),
    { problem: "/tmp/host.json is not JSON: line 1" }));
  check("a malformed config is reported to the panel", m.problems.length === 1 &&
        /not JSON/.test(m.problems[0]), m.problems.join(" | "));
}

// ---- the panel cannot promise a server capability it does not know -----
{
  const invoked = (source.match(/invoke\('([a-z_]+)'/g) || [])
    .map(call => call.replace(/invoke\('|'$/g, ""));
  const allowed = ["settings_read", "settings_write"];
  const stray = [...new Set(invoked)].filter(name => allowed.indexOf(name) === -1);
  check("the panel calls only the two settings commands", stray.length === 0,
        stray.join(", ") || invoked.join(", "));
  check("the panel fetches nothing: the shell's commands are the only data source",
        !/\bfetch\s*\(/.test(source));
  check("the panel writes no HTML", !/innerHTML/.test(source));
  const m = Settings.model({});
  check("the model carries no web_ui or subsonic field, so a patch cannot save one",
        m.web_ui === undefined && m.subsonic === undefined,
        Object.keys(m).join(","));
  const d = Settings.draft(m);
  check("the draft carries only the address and the entry's choice",
        Object.keys(d).sort().join(",") === "autostart,autostartTouched,port,scope",
        Object.keys(d).join(","));
}

if (failures) { console.log(`\n${failures} failure(s)`); process.exit(1); }