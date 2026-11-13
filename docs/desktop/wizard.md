# The first-run wizard (HOST-002)

The four questions a HardlyListening install cannot answer for itself, asked once, in the window
`desktop/` already is. Nothing here is a second runtime: the wizard writes one small state file,
creates one data folder, and asks the server to create one administrator through the server's own
account code.

```
desktop/
├── host/setup.py          every rule, tested: validation, state, the server's env/argv
├── ui/wizard.js           the four steps, driven by the rules Python proved
├── src-tauri/src/wizard.rs  folder dialog, atomic save, the account one-shot
└── docs/desktop/wizard.md   this file
```

## The four steps

| step | question | what is checked |
| --- | --- | --- |
| `music` | Where is your music? | the folder exists, is a folder, and can be read. It is never created and never written to — a library is the person's, not the installer's |
| `data` | Where should the server keep its own files? | the folder is writable, or its nearest existing ancestor is, in which case it is created (0700) when the step is saved |
| `name` | What is this server called? | 1–64 characters after trimming, no control characters |
| `admin` | Your administrator account | an email and a password, held to the server's own rules (below) |

The order is not decoration: the account is written into the database inside the data folder, so
the data folder comes first. The music folder is only read, so it can be answered first and
changed later from the server's own settings.

### What the server name is, and is not

The server has no name setting today. The name is the **shell's** label: it titles the window and
tells two installs on two machines apart. Nothing is sent to the server and no hostname is
replaced. If the server grows an instance name later, this is the value it starts from; until
then, calling it anything else would be inventing a setting the server does not read.

## The account is the server's, not the wizard's

The wizard never writes SQL. The `admin` step runs one short program in a **server process**
(`ADMIN_SCRIPT` in `desktop/host/setup.py`, mirrored byte for byte in `wizard.rs`) which calls:

* `Security.validate_email` and `Security.validate_password` — the server's own rules, checked
  *before* anything is created;
* `Security.create_user(store, {'role': 'admin'}, None, 'admin')` — the server's account path: it
  applies the auth schema and mints the member;
* `Security.set_password(store, member_id, password, email)` — the server's address check, its
  scrypt profile and its "that email is already in use" refusal.

So a rule that changes in `security.py` changes here on the next run, and the wizard cannot
disagree with the server about what a valid account is. The window's own copy of the rules exists
only to answer before a round trip; `tests/test_host_wizard.py` compares it to `security.py` value
by value.

Two refusals are worth knowing about, because they look like free choices and are not:

* **A name for the account is not offered.** `Security.create_user` refuses a display name — new
  accounts are unnamed placeholders and the person chooses their own name on first sign-in. The
  wizard does not pretend otherwise.
* **The server's existing local bootstrap, `auth_admin.py`, is not used.** Its `--name` path is
  refused by that same guard today, and its printed permanent link needs a configured
  `HARDLYLISTENING_PUBLIC_ORIGIN`, which a loopback first run does not have. That is a core
  observation, not something this card fixes; the account one-shot above is the path that works
  without changing the server.

### Where the password goes

`password` is read in the window, sent to `setup_admin`, written to the child's **stdin**, and
dropped. It is not in the argv (a `ps` would show it), not in the state file, and `setup_save`
refuses any state that carries a `password` key. A lost laptop loses a login, not a credential; the
account's permanent link remains the recovery path, as described in `SECURITY.md`.

## The state file, and how a run resumes

`<XDG_STATE_HOME>/hardlylistening-desktop/setup.json`, beside the shell's `host.json` and behind the
same override (`HARDLYLISTENING_DESKTOP_CONFIG` names the config file; the wizard's state is its
sibling). It is written **after every completed step**, atomically — a temporary file, `0600` from
the first byte, then a rename — so a window killed mid-write reopens a step rather than reading
half an object.

```json
{
  "version": 1,
  "music_dir": "/home/someone/Music/HardlyListening",
  "data_dir": "/home/someone/.local/share/hardlylistening",
  "server_name": "HardlyListening",
  "admin_email": "owner@example.com",
  "admin_created": true,
  "completed": ["music", "data", "name", "admin"]
}
```

* **A second launch with all four steps answered opens the shell, not the wizard.** The rule is one
  function — `setup.is_complete` in Python, `Wizard.isComplete` in JavaScript, compared by the
  tests.
* **Anything less reopens the wizard at the first unfinished step**, with the answers already given
  still in place.
* **A malformed or foreign-version file is reported and ignored**, exactly as `host.json` is: the
  person sees one sentence and is asked the four questions again. A window that will not open
  because a JSON file lost a brace is worse than one that says so.

## What the server is handed

The wizard does not choose an address — `desktop/host/resolve.py` and HOST-005 own that — so the
only things it adds are the three variables a packaged install already uses:

```
HARDLYLISTENING_DATA_DIR=<data folder>
HARDLYLISTENING_DB=<data folder>/hardlylistening.db
HARDLYLISTENING_MUSIC_DIR=<music folder>
```

`HARDLYLISTENING_DB` is named explicitly so the account step and a later start cannot disagree
about which file they mean (`paths.py`: an explicit database path beats the data directory).
`HARDLYLISTENING_MUSIC_DIR` only seeds a library that has no chosen root yet — after that the
database wins, which is what stops a restart from re-pointing a library.

`wizard::server_env()` is the one exported helper: `lib.rs` asks it for those three lines before
it spawns the server, so a run of the wizard takes effect without reopening the window. With no
usable `setup.json` it returns nothing and the server's own defaults decide.

`check` is the readiness command (`setup.check_argv()`): the same one `deploy/install.sh` runs
before it enables anything. It is informational — it also looks for `yt-dlp` and `ffmpeg`, which
the wizard does not install — and it is the honest way to show what a first start will say without
starting anything.

Nothing about the server's headless path changes. The variables are the ones `paths.py` and
`config.py` already read, so an install the wizard configured can be reproduced by hand in systemd
or Docker with the same three lines.

## The commands the window calls

| command | what it does |
| --- | --- |
| `setup_state` | the state file, the problem with it if any, the home directory and the pre-filled defaults |
| `setup_pick_folder` | one folder dialog, from `zenity`, `kdialog`, `osascript` or `powershell` — no Tauri plugin, no new capability file. A miss returns a sentence, and typing the path always works |
| `setup_save` | validate the answers present, create the data folder if it is the one missing, write the state atomically. A refusal names the field and the sentence |
| `setup_admin` | run the account one-shot with the server's environment; return `{created, member_id, email}` or the server's last sentence |

## Tests

```sh
python3 -m unittest tests.test_host_wizard     # rules, state, resume, the account one-shot
node tests/test_host_wizard.cjs                # the four steps, resume, and the JS/Python drift
rustfmt --edition 2021 --check desktop/src-tauri/src/*.rs
```

The Python test executes the account one-shot against a temporary database and asks
`Security.authenticate_password` whether the result can sign in. It also proves the refusals: a
short password, a bad address and a duplicate email are refused by the server, and a refusal leaves
no half-created administrator behind.

## What is not verified, plainly

* **The Rust has never been compiled.** `webkit2gtk-4.1`, `gtk+-3.0` and `libsoup-3.0` are not
  installed here and adding them to an immutable host is an OS layering change plus a reboot.
  `rustfmt` proves `wizard.rs` parses; the decisions it renders are proven in Python and Node.
* **The folder dialog is inferred, not observed.** `zenity`/`kdialog`/`osascript`/`powershell` are
  the helpers a desktop normally has; which one answers, and what it returns for a cancelled
  dialog, has not been run here. A cancel is treated as "nothing chosen", and the path field stays
  typeable for exactly that reason.
* **The bundled bundle is not exercised.** `desktop/host/setup.py` is never shipped — the window
  runs the Rust rendering, and the account one-shot needs the *installed* server's `db.py` and
  `security.py` beside the launcher. A bundle without a server installation fails the account step
  with "the server's application folder was not found", which is the same dependency HOST-001
  documents.
* **`setup_admin` is unverified against a real desktop WebView.** The `invoke` payload carries the
  password from the WebView to Rust; nothing here proves the CSP or the bridge in a shipped build.
* **Windows and macOS paths are a rendering, not a test.** The validators use Unix permission bits
  where the tests run; the Windows fallback (`readonly()`) has never executed.
* **A second run against the same data folder can create a second administrator** with a different
  email; the duplicate-email refusal is the only guard. That is deliberate — the alternative was a
  wizard that refuses to finish a half-finished run — and it is one more reason the state file is
  written after every step.