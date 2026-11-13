// The first-run wizard's four steps, driven without a window.
//
// `desktop/ui/wizard.js` holds the shell's decisions for HOST-002: which step is open, what the
// next button says, what is refused and with which sentence, and — the rule the card turns on —
// whether a second launch shows the shell instead of the wizard. The crate cannot be compiled on
// this machine, so this file is the executable half of the proof; `tests/test_host_wizard.py` is
// the other half, and the two are compared here rather than assumed to agree.
//
// The harness is the same shape as `tests/test_desktop_state.cjs`: load the shipped file, drive it
// with plain objects, assert on what a person would see.
const fs = require("fs");
const path = require("path");
const { execFileSync } = require("child_process");

const DESKTOP = path.join(__dirname, "..", "desktop");
const UI = path.join(DESKTOP, "ui");
const SETUP = path.join(DESKTOP, "host", "setup.py");
const source = fs.readFileSync(path.join(UI, "wizard.js"), "utf8");
const Wizard = require(path.join(UI, "wizard.js"));

let failures = 0;
function check(name, ok, detail) {
  if (ok) console.log("PASS " + name);
  else { failures++; console.log("FAIL " + name + (detail ? " — " + detail : "")); }
}

// ---- the Python copy, asked once per question ------------------------------------------------
// The window repeats four rules so a typo is answered without a round trip. A second copy is only
// safe while it is measured against the first, so every number and every sentence is compared.
function python(args, input) {
  return execFileSync("python3", [SETUP].concat(args), {
    cwd: path.join(__dirname, ".."),
    input: input,
    encoding: "utf8"
  });
}
const pythonRules = JSON.parse(python(["--rules"]));
const pythonDefaults = JSON.parse(python(["--defaults", "/home/example"]));

// ---- the rules the window keeps ---------------------------------------------------------------
{
  Object.keys(pythonRules).forEach(function (key) {
    check("wizard.js repeats the Python " + key,
      JSON.stringify(Wizard.RULES[key]) === JSON.stringify(pythonRules[key]),
      JSON.stringify(Wizard.RULES[key]) + " vs " + JSON.stringify(pythonRules[key]));
  });
  check("the four steps are the card's four questions",
    JSON.stringify(Wizard.RULES.steps) === JSON.stringify(["music", "data", "name", "admin"]),
    JSON.stringify(Wizard.RULES.steps));

  const defaults = Wizard.defaults("/home/example");
  check("the suggestions are the Python suggestions",
    defaults.music_dir === pythonDefaults.music_dir && defaults.data_dir === pythonDefaults.data_dir,
    JSON.stringify(defaults));
  check("a trailing slash does not double up",
    Wizard.defaults("/home/example/").music_dir === defaults.music_dir);
}

// ---- the refusals, sentence for sentence ------------------------------------------------------
{
  // Path *existence* is the shell's Rust half (it asks the operating system); the Python validator
  // would say "does not exist" for any path this test invents. So the table covers the answers
  // that are rules, plus the empty path answers.
  const table = [
    { field: "music_dir", value: "" },
    { field: "data_dir", value: "   " },
    { field: "server_name", value: "" },
    { field: "server_name", value: "  " },
    { field: "server_name", value: "x".repeat(65) },
    { field: "server_name", value: "two\nlines" },
    { field: "server_name", value: "  Listening at home  " },
    { field: "admin_email", value: "" },
    { field: "admin_email", value: "a@b.co" },
    { field: "admin_email", value: "A@B.CO" },
    { field: "admin_email", value: " a@b.co " },
    { field: "admin_email", value: "a@@b.co" },
    { field: "admin_email", value: "a b@c.de" },
    { field: "admin_email", value: "a@.co" },
    { field: "admin_email", value: "a@b." },
    { field: "admin_email", value: "@b.co" },
    { field: "admin_email", value: "nope" },
    { field: "password", value: "" },
    { field: "password", value: "short" },
    { field: "password", value: "x".repeat(10) },
    { field: "password", value: "x".repeat(1025) }
  ];
  const verdicts = JSON.parse(python(["--check-table"], JSON.stringify(table)));
  table.forEach(function (row, index) {
    const ours = Wizard.checkField(row.field, row.value);
    const theirs = verdicts[index];
    const same = ours.ok === theirs.ok &&
      (ours.ok ? ours.value === theirs.value : ours.detail === theirs.detail);
    check("the window and the server refuse " + row.field + " " + JSON.stringify(row.value) + " alike",
      same,
      JSON.stringify({ ours: ours, theirs: theirs }));
  });
}

// ---- a first launch: four steps, one at a time ------------------------------------------------
function fresh(options) {
  return Wizard.initial({}, options || { home: "/home/example" });
}

function save(step) {
  // What the window does: `next`, then `setup_save`, then the saved state comes back.
  const payload = Wizard.payload(step);
  return { state: Wizard.reduce(step, { type: "saved", state: payload }), payload: payload };
}

{
  const state = fresh();
  const view = Wizard.view(state);
  check("a first launch opens the wizard", view.show === "wizard", view.show);
  check("on the music folder", view.step === "music", view.step);
  check("the counter is one of four", view.index === 1 && view.total === 4, view.index + "/" + view.total);
  check("the music folder is pre-filled, not empty",
    view.fields[0].value === "/home/example/Music/HardlyListening", view.fields[0].value);
  check("Back is not offered on the first step", !view.buttons.back.enabled);
  check("Choose folder… is offered for a path", view.buttons.pick.enabled);
  check("the step names itself", /music/i.test(view.title), view.title);

  const empty = Wizard.reduce(
    Wizard.reduce(state, { type: "edit", field: "music_dir", value: "   " }),
    { type: "next" });
  check("an empty folder is refused without a round trip",
    empty.error === "Choose a folder for your music" && !empty.saving, empty.error);
  check("the refusal names the field", empty.field === "music_dir", empty.field);
  check("a refusal keeps the person on the step",
    Wizard.view(empty).step === "music", Wizard.view(empty).step);
}

// ---- the four steps, in order ----------------------------------------------------------------
{
  let state = fresh();
  check("step 1 continues", Wizard.view(Wizard.reduce(state, { type: "next" })).buttons.next.label === "Continue");

  const stepBeforeData = Wizard.reduce(state, { type: "next" });
  let result = save(stepBeforeData);
  state = result.state;
  check("the music folder is saved in the payload",
    result.payload.music_dir === "/home/example/Music/HardlyListening", result.payload.music_dir);
  check("finishing a step opens the next one", state.step === "data", state.step);
  check("the completed list starts with music",
    JSON.stringify(result.payload.completed) === JSON.stringify(["music"]),
    JSON.stringify(result.payload.completed));
  check("the payload carries no password", !("password" in result.payload));
  check("the data step is a path with a picker",
    Wizard.view(state).buttons.pick.enabled && state.step === "data", state.step);

  result = save(Wizard.reduce(state, { type: "next" }));
  state = result.state;
  check("the data folder is second, before the account is written into it",
    result.payload.completed.join(",") === "music,data", result.payload.completed.join(","));

  const blankName = Wizard.reduce(
    Wizard.reduce(state, { type: "edit", field: "server_name", value: "  " }),
    { type: "next" });
  check("a blank server name is refused", /server name/i.test(blankName.error), blankName.error);

  result = save(Wizard.reduce(
    Wizard.reduce(state, { type: "edit", field: "server_name", value: "  Listening at home  " }),
    { type: "next" }));
  state = result.state;
  check("a name is trimmed before it is saved", result.payload.server_name === "Listening at home",
    result.payload.server_name);
  check("the account step is last", state.step === "admin", state.step);

  const view = Wizard.view(state);
  check("the account step asks for an email and a password",
    JSON.stringify(view.fields.map(field => field.name)) === JSON.stringify(["admin_email", "password"]),
    JSON.stringify(view.fields.map(field => field.name)));
  check("the account step shows what was chosen",
    view.summary.length === 3 && view.summary[0].label === "Music", JSON.stringify(view.summary));
  check("no folder picker on the account step", !view.buttons.pick.enabled);
  check("the last button creates the account",
    view.buttons.next.label === "Create the account", view.buttons.next.label);
}

// ---- the account step ------------------------------------------------------------------------
{
  let state = fresh();
  state = save(Wizard.reduce(state, { type: "next" })).state;
  state = save(Wizard.reduce(state, { type: "next" })).state;
  state = save(Wizard.reduce(state, { type: "next" })).state;

  let edited = Wizard.reduce(state, { type: "edit", field: "admin_email", value: "Owner@Example.COM" });
  edited = Wizard.reduce(edited, { type: "edit", field: "password", value: "short" });
  const refused = Wizard.reduce(edited, { type: "next" });
  check("a short password is refused before the server is asked",
    refused.error === "Password must be at least 10 characters" && !refused.saving, refused.error);
  check("the password is never part of the saved answers",
    !("password" in Wizard.payload(refused)), JSON.stringify(Wizard.payload(refused)));

  let ready = Wizard.reduce(edited, { type: "edit", field: "password", value: "a long enough password" });
  ready = Wizard.reduce(ready, { type: "next" });
  check("a valid answer is handed to the server, not saved yet",
    ready.saving === true && ready.step === "admin", ready.saving + "/" + ready.step);

  const created = Wizard.reduce(ready, { type: "created", email: "owner@example.com" });
  check("the created address is the server's, normalized", created.values.admin_email === "owner@example.com",
    created.values.admin_email);
  check("the password is dropped as soon as the account exists",
    created.password === "" && !("password" in Wizard.payload(created)), created.password);

  const result = save(created);
  check("the finished state records the account",
    result.payload.admin_created === true, JSON.stringify(result.payload));
  check("all four steps are recorded",
    result.payload.completed.join(",") === "music,data,name,admin", result.payload.completed.join(","));
  check("the wizard hands over to the shell", Wizard.view(result.state).show === "shell",
    Wizard.view(result.state).show);
  check("and it is complete by the same rule Python uses", Wizard.isComplete(result.payload));

  // The refusal path: the server says no, and the step stays open with the sentence.
  const rejected = Wizard.reduce(ready, {
    type: "rejected", field: "password", detail: "That email is already in use"
  });
  check("a server refusal is shown on the step",
    rejected.error === "That email is already in use" && rejected.step === "admin", rejected.error);
  check("a server refusal does not finish the step", !rejected.account && !rejected.saving);
}

// ---- resumes: the second launch ---------------------------------------------------------------
{
  const complete = {
    version: Wizard.RULES.version,
    music_dir: "/music",
    data_dir: "/data",
    server_name: "Home",
    admin_email: "owner@example.com",
    admin_created: true,
    completed: ["music", "data", "name", "admin"]
  };
  const second = Wizard.initial(complete, { home: "/home/example" });
  check("a second launch with a valid config opens the shell, not the wizard",
    second.show === "shell" && Wizard.view(second).show === "shell", second.show);
  check("the saved answers are the ones shown", second.values.server_name === "Home");

  const partial = Wizard.initial({
    version: 1,
    music_dir: "/music",
    data_dir: "/data",
    server_name: "Home",
    admin_email: "owner@example.com",
    completed: ["music", "data"]
  }, { home: "/home/example" });
  check("an unfinished run reopens where it stopped",
    partial.show === "wizard" && partial.step === "name", partial.show + "/" + partial.step);
  check("and keeps the answers already given", partial.values.music_dir === "/music");
  check("a step marked done without a value is not done",
    Wizard.firstUnfinished({ completed: ["music"], music_dir: "  " }) === "music");

  const broken = Wizard.initial(null, { home: "/home/example", problem: "/tmp/setup.json is not JSON" });
  check("a broken state file is explained, not fatal",
    broken.notice === "/tmp/setup.json is not JSON" && broken.show === "wizard", broken.notice);
  check("the explanation is a warning", Wizard.view(broken).noticeKind === "warn",
    Wizard.view(broken).noticeKind);

  const wrongVersion = Wizard.initial({ version: 99, music_dir: "/m" }, { home: "/home" });
  check("a state from another version is not trusted",
    wrongVersion.show === "wizard" && !Wizard.isComplete({ version: 99, completed: ["music"] }));
}

// ---- Back, and the reducer's manners ----------------------------------------------------------
{
  let state = fresh();
  state = save(Wizard.reduce(state, { type: "next" })).state;
  const back = Wizard.reduce(state, { type: "back" });
  check("Back returns to the previous step", back.step === "music", back.step);
  check("Back does not forget what was answered", back.values.music_dir === "/music" ||
    back.values.music_dir === "/home/example/Music/HardlyListening", back.values.music_dir);
  check("Back on the first step is harmless",
    Wizard.reduce(fresh(), { type: "back" }).step === "music");

  const before = fresh();
  const snapshot = JSON.stringify(before);
  Wizard.reduce(before, { type: "next" });
  check("reducing returns a new state, it does not edit the old one",
    JSON.stringify(before) === snapshot);

  const picked = Wizard.reduce(
    Wizard.reduce(fresh(), { type: "picked", path: "/chosen/music" }), { type: "next" });
  check("a picked folder is the answer", picked.values.music_dir === "/chosen/music",
    picked.values.music_dir);
  const cancelled = Wizard.reduce(fresh(), { type: "picked", path: "", detail: "No folder dialog was available" });
  check("a cancelled dialog says so and changes nothing",
    cancelled.error === "No folder dialog was available" && cancelled.values.music_dir !== "", cancelled.error);
}

// ---- the page's own rules ---------------------------------------------------------------------
{
  const app = fs.readFileSync(path.join(UI, "app.js"), "utf8");
  const calls = [...source.matchAll(/invoke\('([a-z_]+)'/g)].map(match => match[1]);
  const allowed = new Set(["setup_state", "setup_save", "setup_admin", "setup_pick_folder"]);
  const stray = calls.filter(name => !allowed.has(name));
  check("the wizard calls only its own four commands", stray.length === 0, stray.join(", "));
  check("and reads no server endpoint",
    (source.match(/\/api\/[A-Za-z0-9_/]/g) || []).length === 0);
  check("no inline styles: the shell's CSP is style-src 'self'",
    !/style\s*=|createElement\(['"]style['"]\)|cssText/.test(source));
  check("no inline script or eval", !/eval\(|new Function\(/.test(source));
  check("the DOM half is guarded, so plain Node can load the file",
    /typeof document !== 'undefined'/.test(source));
  check("the password ordinary answer is not written by the module",
    !/localStorage|sessionStorage|document\.cookie/.test(source));
}

if (failures) { console.log(`\n${failures} failure(s)`); process.exit(1); }