// Accounts: many servers, one active, and a secret the list never sees.
//
// `accounts.js` is loaded in a bare sandbox with a vault passed in, so what is exercised is the
// model's own rules — the round trip, the switch, the redaction, the form a server accepted —
// and not a keychain, a file or a browser, none of which are involved here. Every secret below
// is a fixture: nothing in this file is, or was, a real credential, and no request is made.
const fs = require("fs");
const path = require("path");
const vm = require("vm");

const SOURCE = fs.readFileSync(path.join(__dirname, "..", "player", "shell", "accounts.js"), "utf8");

let failures = 0;
function check(name, ok, detail) {
  if (ok) console.log("PASS " + name);
  else { failures++; console.log("FAIL " + name + (detail ? " — " + detail : "")); }
}

// Each sandbox is one page load: its own module state, its own vault. A vault may be passed in
// so two loads in one test act like two launches against the same keychain.
function sandbox() {
  const ctx = { URL, URLSearchParams, Promise, console };
  vm.createContext(ctx);
  vm.runInContext(SOURCE, ctx, { filename: "accounts.js" });
  return { api: vm.runInContext("this.PlayerAccounts", ctx) };
}

function load(options) {
  const box = sandbox();
  const vault = options && options.vault;
  return { api: box.api, store: box.api.open(vault ? { vault } : {}) };
}

const ONE = { server: "https://one.example", username: "bazzite", accepted: "password", secret: "s3cret-one" };
const TWO = { server: "https://two.example/sub/", username: "bazzite", accepted: "token", secret: "s3cret-two" };

// A store holding both servers, and the vault they were stored in. Runs inside one realm so the
// objects the model sees are its own.
async function populated() {
  const { api, store } = load();
  await store.add(ONE);
  await store.add(TWO);
  return { api, store, vault: store.vault };
}

async function main() {
  // ---- the address never carries credentials -----------------------------
  {
    const { api, store } = load();
    const pasted = api.origin("https://user:secret-pass@music.example.com");
    check("an address with a password in it is refused, not stripped",
      !!pasted.error && !/secret-pass/.test(pasted.error), pasted.error);
    const added = await store.add(Object.assign({}, ONE, { server: "https://u:p@one.example" }));
    check("that address cannot become an account",
      !added.ok && store.list().length === 0, JSON.stringify(added));
    check("a trailing slash is the same server",
      api.origin("https://one.example/").url === "https://one.example",
      JSON.stringify(api.origin("https://one.example/")));
    check("a pasted /rest suffix is named rather than doubled",
      !!api.origin("https://one.example/rest").error);
    check("a missing scheme is refused, never guessed",
      !!api.origin("one.example").error);
  }

  // ---- many servers, one active ------------------------------------------
  {
    const { api, store, vault } = await populated();
    check("both servers are in the list", store.list().length === 2, JSON.stringify(store.list()));
    check("the address is stored normalised",
      store.list()[1].server === "https://two.example/sub", store.list()[1].server);
    check("the first server stayed the active one",
      store.list()[0].active === true && store.list().filter(a => a.active).length === 1,
      JSON.stringify(store.list()));
    check("each account's form is the one its server accepted",
      store.list()[0].form === "password" && store.list()[1].form === "token",
      JSON.stringify(store.list().map(a => a.form)));
    check("a second server keeps the first server's secret",
      await vault.get(store.list()[0].id) === ONE.secret
        && await vault.get(store.list()[1].id) === TWO.secret);
    check("an account's id is derived from its server and username",
      store.list()[0].id !== store.list()[1].id
        && api.accountId("https://one.example", "bazzite") === store.list()[0].id,
      store.list()[0].id);
    check("signing in again updates that account instead of adding a copy",
      (await store.add(Object.assign({}, ONE, { secret: "s3cret-one-again" }))).ok
        && store.list().length === 2 && await vault.get(store.list()[0].id) === "s3cret-one-again");
  }

  // ---- switching the active server --------------------------------------
  {
    const { store, vault } = await populated();
    const before = store.list().map(a => [a.id, a.server, a.form, a.username].join("|"));
    const stored = [await vault.get(store.list()[0].id), await vault.get(store.list()[1].id)];
    const switched = store.use(store.list()[1].id);
    const after = store.list().map(a => [a.id, a.server, a.form, a.username].join("|"));
    check("switching names the new active server",
      switched.ok && switched.active.active === true, JSON.stringify(switched));
    check("switching changes nothing about either account",
      JSON.stringify(before) === JSON.stringify(after), JSON.stringify(after));
    check("switching does not touch the stored secrets",
      await vault.get(store.list()[0].id) === stored[0]
        && await vault.get(store.list()[1].id) === stored[1]);
    check("the other server's secret is still readable after the switch",
      await store.secret(store.list()[0].id) === ONE.secret);
    check("switching to an unknown account is refused",
      store.use("acct_nope").ok === false, JSON.stringify(store.use("acct_nope")));
    check("exactly one account is ever active",
      store.list().filter(a => a.active).length === 1);
  }

  // ---- the list round-trips without any secret ---------------------------
  {
    const { store } = await populated();
    store.use(store.list()[1].id);
    const saved = store.serialise();
    check("the serialised list has one active server and two records",
      saved.accounts.length === 2 && saved.activeId === store.list()[1].id,
      JSON.stringify(saved));
    check("the serialised list carries no secret",
      !JSON.stringify(saved).includes("s3cret"), JSON.stringify(saved));
    check("a record has no field a secret could hide in",
      Object.keys(saved.accounts[0]).sort().join(",") === "added,form,id,label,server,username",
      Object.keys(saved.accounts[0]).join(","));

    const fresh = load();
    const read = fresh.store.restore(saved);
    check("a saved list reloads with both accounts",
      read.ok && read.count === 2 && fresh.store.list().length === 2, JSON.stringify(read));
    check("the reload reads a list, never a secret: nothing is stored yet",
      await fresh.store.secret(fresh.store.list()[0].id) === null);
    check("the reloaded list keeps the active server",
      fresh.store.active().server === "https://two.example/sub",
      JSON.stringify(fresh.store.active()));
    check("the reload reports nothing dropped and nothing refused",
      read.ignored.length === 0 && read.refused.length === 0, JSON.stringify(read));
    check("what is written back is the same public list",
      JSON.stringify(fresh.store.serialise().accounts) === JSON.stringify(saved.accounts));
  }

  // ---- a secret in a config file is dropped by name ----------------------
  {
    const { store } = load();
    const smuggled = {
      version: 1,
      activeId: "",
      password: "s3cret-top",
      accounts: [
        { id: "acct_x", server: "https://one.example", username: "bazzite", label: "One",
          form: "password", added: "2026-01-01T00:00:00.000Z", password: "s3cret-row" },
        { server: "ftp://two.example", username: "bazzite", form: "password" },
      ],
    };
    const read = store.restore(smuggled);
    check("a saved list is read through an allow-list", read.ok && read.count === 1,
      JSON.stringify(read));
    check("the fields it dropped are named, never echoed",
      read.ignored.includes("password") && !JSON.stringify(read).includes("s3cret"),
      JSON.stringify(read));
    check("a record with an address that is not a server is refused with the reason",
      read.refused.length === 1 && /http/i.test(read.refused[0]), JSON.stringify(read.refused));
    check("nothing smuggled survives the read",
      !JSON.stringify(store.serialise()).includes("s3cret")
        && !JSON.stringify(store.list()).includes("s3cret"));
    check("an empty activeId falls back to the first account",
      store.active() !== null && store.active().active === true);
    const newer = store.restore({ version: 99, accounts: [] });
    check("a list from a newer player is refused rather than half-read",
      !newer.ok && /version/i.test(newer.error), newer.error);
    check("garbage is refused, not thrown",
      store.restore("not json").ok === false && store.restore(null).ok === false);
  }

  // ---- the rendered text and a log line ----------------------------------
  {
    const { api, store } = await populated();
    const rendered = store.render();
    check("the list renders both servers",
      rendered.includes("one.example") && rendered.includes("two.example"), rendered);
    check("the rendered text holds no secret",
      !rendered.includes(ONE.secret) && !rendered.includes(TWO.secret), rendered);
    check("the rendered text marks the active server and the form",
      rendered.split("\n")[0].startsWith("* ") && rendered.includes("(token + salt)"), rendered);

    const line = store.log("connected to https://two.example/rest/ping.view?u=bazzite&p=" + TWO.secret);
    check("a log line scrubs a secret the page has handled",
      !line.includes(TWO.secret) && line.includes("***"), line);

    const params = api.authParams(store.list()[1], TWO.secret, "abc123").params;
    const url = "https://two.example/rest/ping.view?" + new URLSearchParams(params).toString();
    check("a token request carries t= and s=",
      /\bt=[0-9a-f]{32}\b/.test(url) && /\bs=abc123\b/.test(url), url);
    check("a log line scrubs a token the page never held",
      !store.log(url).includes(params.t), store.log(url));
    check("a log line scrubs a password parameter it never held",
      !store.log("https://h.example/rest/ping.view?p=unseen-secret").includes("unseen-secret"),
      store.log("https://h.example/rest/ping.view?p=unseen-secret"));
    check("the username is not treated as a secret",
      store.log("user bazzite on one.example").includes("bazzite"));
    check("lock() drops the copy kept only for redaction",
      store.lock().ok === true
        && !store.log("https://two.example/rest/ping.view?p=" + TWO.secret).includes(TWO.secret));
  }

  // ---- the form a server accepted, and no other --------------------------
  {
    const { api, store } = load();
    const wrong = await store.add(Object.assign({}, ONE, { accepted: "carrier-pigeon" }));
    check("an account cannot claim a form a server does not take",
      !wrong.ok && store.list().length === 0, JSON.stringify(wrong));
    const silent = await store.add(Object.assign({}, ONE, { accepted: undefined }));
    check("a form has to be stated, not assumed",
      !silent.ok && store.list().length === 0, JSON.stringify(silent));
    const empty = await store.add(Object.assign({}, ONE, { secret: "" }));
    check("an account without a secret is not added",
      !empty.ok && store.list().length === 0, JSON.stringify(empty));
    check("every form the model accepts has a name a person can read",
      api.FORM_IDS.length === 3
        && api.FORM_IDS.every(id => typeof api.AUTH_FORMS[id].label === "string"
          && api.AUTH_FORMS[id].label.length > 0),
      JSON.stringify(Object.keys(api.AUTH_FORMS)));

    await store.add(ONE);
    await store.add(TWO);
    await store.add({ server: "https://three.example", username: "hl_invite", accepted: "device-invite",
      secret: "s3cret-three" });
    const byForm = {};
    for (const account of store.list()) {
      byForm[account.form] = api.authParams(account, "s3cret", "salty").params;
    }
    check("the password form sends u= and p=",
      Object.keys(byForm.password).sort().join(",") === "p,u"
        && byForm.password.u === "bazzite" && byForm.password.p === "s3cret",
      JSON.stringify(byForm.password));
    check("the token form sends u=, t= as md5(secret + salt) and s=",
      Object.keys(byForm.token).sort().join(",") === "s,t,u"
        && byForm.token.s === "salty" && byForm.token.t === api.md5Hex("s3cretsalty"),
      JSON.stringify(byForm.token));
    check("a device invite sends the credential the server issued",
      byForm["device-invite"].p === "s3cret" && byForm["device-invite"].s === undefined,
      JSON.stringify(byForm["device-invite"]));
    check("token auth without a salt is refused rather than sent wrong",
      !!api.authParams({ username: "u", form: "token" }, "s3cret", "").error);
    check("a form the model does not know is refused, not guessed",
      !!api.authParams({ username: "u", form: "md5" }, "s3cret", "s").error);
  }

  // ---- md5, because token auth is md5 ------------------------------------
  {
    const { api } = load();
    const vectors = [
      ["", "d41d8cd98f00b204e9800998ecf8427e"],
      ["abc", "900150983cd24fb0d6963f7d28e17f72"],
      ["The quick brown fox jumps over the lazy dog", "9e107d9d372bb6826bd81d3542a419d6"],
      ["pässwörd", "12841e4ba5e37d2fbfc78458c6714ade"],
      ["x".repeat(100), "aed563ecafb4bcc5654c597a421547b2"],
    ];
    for (const [input, expected] of vectors) {
      check("md5 of " + JSON.stringify(input) + " is the published digest",
        api.md5Hex(input) === expected, api.md5Hex(input));
    }
  }

  // ---- PLAYER-001's one-session shape ------------------------------------
  {
    const { store } = load();
    const session = {
      server: { url: "https://music.example.com", type: "HardlyListening", openSubsonic: true },
      credentials: { username: "bazzite", password: "s3cret-migrated" },
    };
    const moved = await store.migrate(session);
    check("the in-memory session becomes an account",
      moved.ok && moved.migratedFrom === "PLAYER-001" && store.list().length === 1,
      JSON.stringify(moved));
    check("it is a password account, because that is what the ping used",
      moved.account.form === "password" && moved.account.server === "https://music.example.com",
      JSON.stringify(moved.account));
    check("its secret went to the vault, not the list",
      await store.secret(moved.account.id) === "s3cret-migrated"
        && !JSON.stringify(store.list()).includes("s3cret-migrated"));
    check("there is nothing to migrate from an empty session",
      (await store.migrate(null)).ok === false && (await store.migrate({})).ok === false
        && store.list().length === 1);
  }

  // ---- the in-memory fallback is honest ----------------------------------
  {
    const { api } = load();
    const fallback = api.memoryVault();
    check("the fallback says it is memory",
      fallback.mode() === "memory" && fallback.persists === false,
      fallback.mode() + "|" + fallback.persists);
    const first = api.open({ vault: fallback });
    const added = await first.add(ONE);
    check("it still stores a secret for this run",
      await first.secret(added.account.id) === ONE.secret);
    check("its status carries the warning, not a promise",
      first.status().mode === "memory" && first.status().persists === false,
      JSON.stringify(first.status()));
    const nextLaunch = api.open({ vault: api.memoryVault() });
    nextLaunch.restore(first.serialise());
    check("a restart asks again, because nothing was written",
      await nextLaunch.secret(added.account.id) === null
        && (await nextLaunch.hasSecret(added.account.id)) === false);
    check("the list itself survived, which is the public half",
      nextLaunch.list().length === 1 && nextLaunch.list()[0].server === "https://one.example");
  }

  // ---- the keychain path, as the stub sees it ----------------------------
  {
    const calls = [];
    const memory = new Map();
    const invoke = async (command, args) => {
      calls.push([command, args]);
      if (command === "accounts_mode") return "keychain";
      if (command === "accounts_put") { memory.set(args.account, args.secret); return "keychain"; }
      if (command === "accounts_secret") {
        return memory.has(args.account) ? memory.get(args.account) : null;
      }
      if (command === "accounts_forget") { memory.delete(args.account); return null; }
      throw new Error("unknown command " + command);
    };
    const { api } = load();
    const keychain = api.keychainVault(invoke);
    check("a keychain vault claims nothing before it is asked",
      keychain.mode() === "pending" && keychain.persists === false, keychain.mode());
    check("the probe settles the mode", (await keychain.probe()).mode() === "keychain");
    const store = api.open({ vault: keychain });
    await store.add(ONE);
    check("the secret crossed the bridge, and not the list",
      calls.some(([command, args]) => command === "accounts_put" && args.secret === ONE.secret)
        && !JSON.stringify(store.list()).includes(ONE.secret));
    check("a stored secret is read back through the bridge",
      await store.secret(store.list()[0].id) === ONE.secret);
    await store.remove(store.list()[0].id);
    check("removing an account forgets its secret",
      memory.size === 0 && store.list().length === 0);
    check("with no bridge there is no keychain to pretend to have",
      api.keychainVault(null).mode() === "memory");
    check("the commands are the ones the Rust module declares",
      ["accounts_mode", "accounts_put", "accounts_secret", "accounts_forget"]
        .every(name => calls.some(([command]) => command === name)),
      calls.map(([command]) => command).join(","));
  }

  // ---- removal keeps the other server ------------------------------------
  {
    const { store, vault } = await populated();
    const firstId = store.list()[0].id;
    const secondId = store.list()[1].id;
    store.use(secondId);
    const gone = await store.remove(secondId);
    check("removing the active server falls back to one that is left",
      gone.ok && gone.activeId === firstId && store.list().length === 1, JSON.stringify(gone));
    check("the removed server's secret is gone", await vault.get(secondId) === null);
    check("the other server's secret is untouched and still active",
      await vault.get(firstId) === ONE.secret && store.active().id === firstId);
  }

  if (failures) {
    console.log(`${failures} failed`);
    process.exit(1);
  }
  console.log("all account checks passed");
}

main().catch(error => {
  console.log("FAIL the harness threw — " + (error && error.stack || error));
  process.exit(1);
});