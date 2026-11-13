"""HOST-007: the dependency points one way — Desktop depends on Server, never the reverse.

`tests/test_headless.py` asks whether the core needs a GUI toolkit. This file asks the other
question the board states: **the server runs with the Desktop package absent**, and the only edge
between the two trees is the one artefact the shell bundles — `bin/hardlylistening-server`.

Four assertions, all over the tree rather than over intentions:

  * **The runtime path names no host.** Not "the Python is toolchain-free": the import closure is
    computed from the real entry point (`bin/hardlylistening-server` → `cli.py`), so a module
    reachable through three imports is scanned too, and `web/`, `bin/`, `deploy/`, `Dockerfile`,
    `install-service.sh` and `start.sh` are read as text. A nested `import` inside a function that
    never runs fails here, which a grep for the word would miss.
  * **The copy boots.** The repository is copied to a temporary directory with exactly one
    directory removed — `desktop/` — and the copy's own launcher is started there. Absence is
    literal, not modelled. Why this and not a weaker version: purging `sys.modules['desktop']` or
    dropping `desktop` from `sys.path` proves something about the import system, and nothing about
    the two ways a server really reaches a shell — an `open()`/`Path.exists()` probe, which simply
    answers False when the file is gone, and a `subprocess` exec, which happens outside the import
    system entirely. Copying the tree reproduces the absent-desktop install exactly, down to the
    failed probe. An audit hook installed through `sitecustomize` covers what absence cannot: an
    absolute path back into the checkout that started the test (in the copy, `desktop/` is gone,
    so a hard-coded one finds the developer's tree instead), failing at the open or directory
    listing with the path in the error. `os.stat` is not an audited event, so a bare `exists()` is
    answered by absence itself rather than by the hook — the two cover different halves and the
    docstring of `assert_tripwire_is_armed` pins that the tripwire is loaded before its silence is
    trusted.
  * **The one edge is the launcher.** `desktop/tools/stage_sidecar.py` stages a *copy of the
    canonical launcher*, so the staged file is compared byte for byte with
    `bin/hardlylistening-server`, and a hash manifest of the whole core is compared before and
    after staging: the shell reads the core and writes into its own `src-tauri/bin`, nothing else.
  * **The packaged installs carry no shell.** The Dockerfile's instructions and both systemd units
    (the committed one and the one `install-service.sh` writes) are parsed; every `ExecStart` is
    resolved back into the tree, so a unit cannot name a runtime that is not the server.

Nothing here starts the desktop, compiles Rust or touches a real install. The only process
spawned is the copied server, on a loopback port with a temporary data directory.
"""
import ast
import hashlib
import json
import os
import re
import shutil
import socket
import subprocess
import sys
import tempfile
import time
import unittest
from pathlib import Path

APP_DIR = Path(__file__).resolve().parents[1]
DESKTOP = APP_DIR / 'desktop'
LAUNCHER = APP_DIR / 'bin' / 'hardlylistening-server'
ENTRY_MODULE = 'cli'                       # what `bin/hardlylistening-server` execs

#: A host's vocabulary. Each pattern is a *mention*, in code or in a comment: a docstring that
#: says "copied to `desktop/tools`" is still the server knowing a shell exists.
#:
#: `node` is deliberately not a bare `\\bnode\\b`: this server has federation *nodes* and XML
#: *nodes*, and a scan that failed on them would be switched off rather than fixed. What is caught
#: instead is every shape that means the toolchain — `node_modules`, a `node script.js`
#: invocation, a node shebang — next to the names that are unambiguous (`npm`, `pnpm`, `cargo`,
#: `tauri`, `src-tauri`, `package.json`, `desktop/`).
HOST_TOKENS = {
    'desktop path': re.compile(r'desktop[/\\]'),
    'src-tauri': re.compile(r'src[-_]tauri', re.I),
    'tauri': re.compile(r'\btauri\b', re.I),
    'cargo': re.compile(r'\bcargo\b', re.I),
    'npm': re.compile(r'\bnpm\b'),
    'pnpm': re.compile(r'\bpnpm\b'),
    'npx': re.compile(r'\bnpx\b'),
    'yarn': re.compile(r'\byarn\b'),
    'package.json': re.compile(r'package\.json'),
    'node_modules': re.compile(r'node_modules'),
    'node invocation': re.compile(r'\bnode(?:\.exe)?\b\s+\S+\.(?:js|cjs|mjs)\b'),
    'node shebang': re.compile(r'^#!.*\bnode\b', re.M),
}

#: Files whose lines may exclude a host artefact instead of requiring one. `node_modules` in
#: `.dockerignore` and in `deploy/install.sh`'s `--exclude=` list is the *opposite* of a dependency,
#: and saying so is more honest than a blanket word list that would force those two lines out.
IGNORE_FILES = {'.dockerignore', '.gitignore'}

TEXT_SUFFIXES = {'.py', '.html', '.css', '.js', '.cjs', '.mjs', '.json', '.md', '.txt',
                 '.svg', '.sh', '.service', '.toml', '.rs', '.webmanifest'}

#: Skipped when the repository is copied for the boot test: `.git` is not the application, and a
#: checkout's live database, caches and backups are state. `tests` and `docs` **are** copied —
#: `desktop/` must be the only thing missing, or the experiment is not the one being claimed.
COPY_IGNORE = ('desktop', '.git', '__pycache__', '*.pyc', '.cache', 'artist-art', 'backups',
               'hardlylistening.db', 'hardlylistening.db-wal', 'hardlylistening.db-shm')

#: Installed through a `sitecustomize` on the child's `PYTHONPATH`. It guards both possible
#: desktop paths — the checkout's and the copy's (which does not exist) — so a hard-coded absolute
#: path fails loudly and a relative `desktop/...` probe fails with a readable message instead of a
#: bare FileNotFoundError the server might swallow.
GUARD_SOURCE = '''\
import os
import sys

_FORBIDDEN = [os.path.normpath(os.path.abspath(part))
              for part in os.environ.get("HARDLYLISTENING_HOST007_GUARD", "").split(os.pathsep)
              if part]
if _FORBIDDEN:

    def _normal(value):
        # No realpath(): it resolves symlinks by stat-ing, and doing filesystem work inside an
        # audit hook is how a tripwire becomes a crash. normpath is enough here.
        text = os.fsdecode(os.fspath(value))
        if not os.path.isabs(text):
            text = os.path.join(os.getcwd(), text)
        return os.path.normpath(text)

    def _hook(event, args):
        # `open`, listings and creation are audited events. `os.stat` is not one in CPython, so a
        # bare `Path.exists()` is invisible here — and in the copy it is answered the way a real
        # desktop-free install answers it, with False. The guard exists for the paths absence
        # cannot cover: an absolute path back into the checkout that started the test.
        if event not in ("open", "os.listdir", "os.scandir", "os.mkdir", "os.remove",
                         "os.rename"):
            return
        for value in args:
            if not isinstance(value, (str, bytes, os.PathLike)):
                continue
            try:
                real = _normal(value)
            except (TypeError, ValueError, OSError):
                continue
            if any(real == root or real.startswith(root + os.sep) for root in _FORBIDDEN):
                raise RuntimeError("HOST-007 guard: the server touched " + real)

    sys.addaudithook(_hook)
'''


# -- the tree ----------------------------------------------------------------------------------

def module_path(name):
    """Where `import <name>` resolves inside this tree, or None when it is stdlib or a wheel."""
    stem = name.replace('.', '/')
    flat = APP_DIR / f'{stem}.py'
    if flat.is_file():
        return flat
    package = APP_DIR / stem / '__init__.py'
    return package if package.is_file() else None


def imported_names(tree):
    """Every module name a module imports, however deep the statement sits.

    `import a.b` names `a.b`; `from a import b` names `a.b` (the submodule) *and* `a` (the
    package), because either may be the file that exists. `__import__('a.b')` and
    `importlib.import_module('a.b')` with a literal are included: `db.py` reaches
    `downloaders.ytdlp` that way, and a closure that skipped it would be smaller than the truth.
    """
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            for alias in node.names:
                yield alias.name
        elif isinstance(node, ast.ImportFrom):
            if node.level:                      # relative: the core's own code, already walked
                continue
            if node.module:
                yield node.module
                for alias in node.names:
                    if alias.name != '*':
                        yield f'{node.module}.{alias.name}'
        elif isinstance(node, ast.Call):
            name = node.func
            called = name.id if isinstance(name, ast.Name) else getattr(name, 'attr', '')
            if called in ('__import__', 'import_module') and node.args:
                first = node.args[0]
                if isinstance(first, ast.Constant) and isinstance(first.value, str):
                    yield first.value
                elif (isinstance(first, ast.BinOp)
                      and isinstance(first.left, ast.Constant) and isinstance(first.left.value, str)):
                    yield first.left.value


def import_closure(root=ENTRY_MODULE):
    """Every module the entry point can import, found by walking imports from `cli`.

    Static by necessity: importing the tree here would execute its module-level code inside the
    suite's own process, and a single broken module would fail this test instead of its own. The
    boot test runs the same tree for real, which is what covers a module reached only by a computed
    import name.
    """
    seen, todo = {}, [root]
    while todo:
        name = todo.pop()
        if name in seen:
            continue
        path = module_path(name)
        if path is None:                        # stdlib, `yt_dlp`, or a typo in an unused branch
            continue
        tree = ast.parse(path.read_text(encoding='utf-8'), filename=str(path))
        seen[name] = path
        for imported in imported_names(tree):
            candidate = imported.partition('.')[0]
            if module_path(imported) or module_path(candidate):
                todo.append(imported)
    return seen


def text_files(root):
    for path in sorted(Path(root).rglob('*')):
        if path.is_file() and path.suffix.lower() in TEXT_SUFFIXES:
            yield path


def runtime_text_files():
    """The server's shipped surface: its Python, its web UI, and the files that start it."""
    for path in sorted(import_closure().values()):
        yield path
    yield from text_files(APP_DIR / 'web')
    yield from text_files(APP_DIR / 'bin')
    yield from text_files(APP_DIR / 'deploy')
    for name in ('Dockerfile', '.dockerignore', 'install-service.sh', 'start.sh', 'VERSION'):
        path = APP_DIR / name
        if path.is_file():
            yield path


def host_tokens_in(path, line):
    """(token, match) for every host mention on one line, exclusions excepted.

    An exclusion is not a dependency: `.dockerignore`'s `node_modules` and `deploy/install.sh`'s
    `--exclude=` list are keeping the artefact out. `IGNORE_FILES` covers the ignore-list shape,
    `exclud` the command-line shape.
    """
    ignore_file = path.name in IGNORE_FILES
    for token, pattern in HOST_TOKENS.items():
        match = pattern.search(line)
        if not match:
            continue
        if 'exclud' in line.lower():
            continue
        if ignore_file and line.strip() in (token, match.group(0), match.group(0) + '/'):
            continue
        yield token, match


def host_mentions(path):
    """(token, line number, line) for every host mention in one file."""
    try:
        text = path.read_text(encoding='utf-8')
    except UnicodeDecodeError:
        return
    for number, line in enumerate(text.splitlines(), 1):
        for token, _match in host_tokens_in(path, line):
            yield token, number, line.strip()


def manifest(root, skip=('desktop',)):
    """Path → SHA-256 for every file under `root`, minus the shell and VCS metadata."""
    digests = {}
    for path in sorted(Path(root).rglob('*')):
        if not path.is_file():
            continue
        relative = path.relative_to(root)
        if relative.parts[0] in ('.git', '__pycache__') or relative.parts[0] in skip:
            continue
        digests[str(relative)] = hashlib.sha256(path.read_bytes()).hexdigest()
    return digests


def free_port():
    with socket.socket() as sock:
        sock.bind(('127.0.0.1', 0))
        return sock.getsockname()[1]


def docker_instructions(text):
    """Dockerfile instructions as single joined strings, comments dropped."""
    instructions, current = [], ''
    for raw in text.splitlines():
        line = raw.rstrip()
        if not current and (not line.strip() or line.lstrip().startswith('#')):
            continue
        stripped = line.strip()
        if stripped.endswith('\\'):
            current += stripped[:-1] + ' '
            continue
        current += stripped
        instructions.append(' '.join(current.split()))
        current = ''
    if current.strip():
        instructions.append(' '.join(current.split()))
    return instructions


def unit_sections(text):
    """The systemd unit's `Key=Value` settings, and the paths it names."""
    settings = {}
    for line in text.splitlines():
        line = line.strip()
        if not line or line.startswith(('#', '[', ';')) or '=' not in line:
            continue
        key, _, value = line.partition('=')
        settings.setdefault(key.strip(), []).append(value.strip())
    return settings


# -- 1. the runtime path names no host ---------------------------------------------------------

class NoHostInTheRuntimePath(unittest.TestCase):
    def test_the_closure_is_the_real_runtime_and_not_an_empty_set(self):
        # Without this, a walker broken by a refactor would pass everything below by scanning
        # nothing. The entry point is the launcher's target, and `server.py` is what it starts.
        closure = import_closure()
        for name in (ENTRY_MODULE, 'server', 'security', 'subsonic', 'listening', 'paths'):
            self.assertIn(name, closure, f'{name} is missing from the import closure')
        self.assertGreater(len(closure), 30, 'the closure is suspiciously small')
        # The entry point the closure starts from is the file the packaged installs run.
        self.assertIn(ENTRY_MODULE + '.py', LAUNCHER.read_text())

    def test_no_runtime_module_mentions_the_shell_or_a_js_toolchain(self):
        offenders = []
        for name, path in sorted(import_closure().items()):
            for token, number, line in host_mentions(path):
                offenders.append(f'{path.relative_to(APP_DIR)}:{number}: {token} — {line[:90]}')
        self.assertEqual(offenders, [],
                         'the server imports this module; it must not know a desktop exists')

    def test_web_bin_deploy_and_the_start_scripts_are_clean_too(self):
        # `web/` is served as written, so any string in it is shipped; `bin/` and the start
        # scripts are what systemd and Docker execute. None of them may need a host.
        offenders = []
        for path in runtime_text_files():
            for token, number, line in host_mentions(path):
                offenders.append(f'{path.relative_to(APP_DIR)}:{number}: {token} — {line[:90]}')
        self.assertEqual(offenders, [])

    def test_the_only_toolchain_words_present_are_exclusions(self):
        # The honest statement, kept as a positive check: `.dockerignore` and `deploy/install.sh`
        # name `node_modules` because they keep it *out*. If that ever becomes a requirement
        # instead of an exclusion, this fails and the two files above are where to look.
        exclusions = []
        for path in runtime_text_files():
            text = path.read_text(encoding='utf-8', errors='replace')
            for number, line in enumerate(text.splitlines(), 1):
                if 'node_modules' in line:
                    exclusions.append((path, number, line.strip()))
        for path, number, line in exclusions:
            self.assertEqual(list(host_tokens_in(path, line)), [],
                             f'{path.relative_to(APP_DIR)}:{number}: {line}')
            self.assertTrue('exclud' in line.lower() or path.name in IGNORE_FILES,
                            f'{path.relative_to(APP_DIR)}:{number} names node_modules: {line}')


# -- 2. the server boots with the shell absent -------------------------------------------------

class BootsWithTheShellAbsent(unittest.TestCase):
    """The copy is the repository minus `desktop/`, run by its own launcher."""

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory(prefix='hl-host007-')
        self.addCleanup(self.tmp.cleanup)
        self.app = Path(self.tmp.name) / 'app'
        shutil.copytree(APP_DIR, self.app, ignore=shutil.ignore_patterns(*COPY_IGNORE),
                        symlinks=True)
        self.data = Path(self.tmp.name) / 'data'
        self.data.mkdir()
        self.guard = Path(self.tmp.name) / 'guard'
        self.guard.mkdir()
        (self.guard / 'sitecustomize.py').write_text(GUARD_SOURCE)
        self.port = free_port()

    def environment(self):
        env = {k: v for k, v in os.environ.items()
               if k not in ('DISPLAY', 'WAYLAND_DISPLAY', 'XDG_SESSION_TYPE', 'PYTHONPATH',
                            'HARDLYLISTENING_DATA_DIR', 'HARDLYLISTENING_DB')}
        env.update(
            HARDLYLISTENING_DATA_DIR=str(self.data),
            HARDLYLISTENING_DB=str(self.data / 'hardlylistening.db'),
            HARDLYLISTENING_AUTH_REQUIRED='0',
            HARDLYLISTENING_PYTHON=sys.executable,
            # Both the checkout's desktop and the copy's (absent) one.
            HARDLYLISTENING_HOST007_GUARD=os.pathsep.join(
                [str(DESKTOP), str(self.app / 'desktop')]),
            PYTHONPATH=str(self.guard),
            PYTHONDONTWRITEBYTECODE='1',
            PYTHONUNBUFFERED='1',
        )
        return env

    def assert_tripwire_is_armed(self, env):
        """Prove the guard loads and fires before anything is concluded from its silence.

        Without this, a `sitecustomize` that never imported would make the boot test pass for the
        wrong reason. The probe is the smallest audited desktop read: opening its README.
        """
        probe = subprocess.run(
            [sys.executable, '-c',
             'import pathlib, sys; print(pathlib.Path(sys.argv[1]).read_text()[:1])',
             str(DESKTOP / 'README.md')],
            env=env, capture_output=True, text=True)
        self.assertIn('HOST-007 guard', probe.stderr,
                      f'the audit hook did not load: {probe.stderr[:300]}')

    def start(self, env):
        # The copy's own `bin/hardlylistening-server`, exactly as systemd's `ExecStart`, the
        # Dockerfile's ENTRYPOINT and the shell's sidecar run it.
        proc = subprocess.Popen(
            [str(self.app / 'bin' / 'hardlylistening-server'), 'run', '--port', str(self.port)],
            cwd=self.app, env=env, stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
            text=True, start_new_session=True)

        def stop():
            if proc.poll() is None:
                proc.terminate()
                try:
                    proc.wait(timeout=5)
                except subprocess.TimeoutExpired:
                    proc.kill()
                    proc.wait(timeout=5)
            proc.stdout.close()

        self.addCleanup(stop)
        output = ''
        deadline = time.time() + 60
        while time.time() < deadline:
            line = proc.stdout.readline()
            if not line:
                break
            output += line
            if 'HardlyListening on' in output:
                break
        self.assertIn('HardlyListening on', output, output)
        return proc, output

    def get(self, path):
        import urllib.error
        import urllib.request
        request = urllib.request.Request(f'http://127.0.0.1:{self.port}{path}')
        try:
            with urllib.request.urlopen(request, timeout=10) as response:
                return response.status, response.read()
        except urllib.error.HTTPError as error:
            return error.code, error.read()

    def test_health_answers_from_a_tree_with_no_desktop_directory(self):
        self.assertFalse((self.app / 'desktop').exists(), 'the copy must not hold the shell')
        # Not a sanity check on the copy alone: `web/` and `tests/` are present, so "absent"
        # means exactly one directory was removed.
        self.assertTrue((self.app / 'web' / 'index.html').is_file())
        self.assertTrue((self.app / 'server.py').is_file())

        before = manifest(DESKTOP, skip=())
        env = self.environment()
        self.assert_tripwire_is_armed(env)
        proc, output = self.start(env)

        status, body = self.get('/api/health')
        self.assertEqual(status, 200, body[:200])
        self.assertTrue(json.loads(body)['ok'])
        # The web UI is served too: the copy is a whole install, not just a health endpoint.
        self.assertEqual(self.get('/')[0], 200)
        # The process used the environment this test handed it, which is what makes it a run of
        # the copy and not of some other checkout that happened to be answering.
        self.assertTrue((self.data / 'hardlylistening.db').is_file())
        self.assertNotIn('HOST-007 guard', output,
                         'the server reached for a desktop/ directory')
        self.assertIsNone(proc.poll(), 'the server must not exit with the shell absent')
        self.assertEqual(manifest(DESKTOP, skip=()), before,
                         'the boot wrote into the shell it does not depend on')


# -- 3. the one edge is the launcher ------------------------------------------------------------

class TheOnlyEdgeIsTheLauncher(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp(prefix='hl-host007-stage-'))
        self.addCleanup(shutil.rmtree, self.tmp, True)
        self.script = DESKTOP / 'tools' / 'stage_sidecar.py'

    def stage(self, *args):
        return subprocess.run([sys.executable, str(self.script), '--out', str(self.tmp), *args],
                              capture_output=True, text=True, cwd=str(APP_DIR))

    def test_the_staged_sidecar_is_the_canonical_launcher_byte_for_byte(self):
        triple = 'x86_64-unknown-linux-gnu'
        result = self.stage('--triple', triple)
        self.assertEqual(result.returncode, 0, result.stderr)
        staged = self.tmp / f'{LAUNCHER.name}-{triple}'
        self.assertTrue(staged.is_file(), result.stdout)
        expected = hashlib.sha256(LAUNCHER.read_bytes()).hexdigest()
        self.assertEqual(hashlib.sha256(staged.read_bytes()).hexdigest(), expected,
                         'the bundle would ship a different runtime under the same name')
        self.assertIn(expected, result.stdout, 'the script reports the digest it wrote')

    def test_the_bundle_declares_the_same_file_the_script_stages(self):
        conf = json.loads((DESKTOP / 'src-tauri' / 'tauri.conf.json').read_text())
        declared = conf['bundle']['externalBin']
        self.assertEqual(declared, ['bin/' + LAUNCHER.name],
                         'the bundle names one launcher, and it is the server entry point')
        source = self.script.read_text()
        # The source of the copy is resolved from the shell's own module, not spelled out again:
        # a literal here is how a fork starts.
        self.assertIn('resolve.launcher()', source)
        self.assertIn('resolve.LAUNCHER_NAME', source)
        self.assertIn('shutil.copyfile', source)
        for core in ('server.py', 'cli.py', 'web/', 'db.py', 'subsonic.py'):
            self.assertNotIn(core, source,
                             f'stage_sidecar.py names {core}: the sidecar is a copy, not a build')

    def test_staging_writes_nothing_into_the_core(self):
        # The manifest is the core, minus `desktop/`. Staging reads the launcher and writes the
        # copy; anything else — a rewritten `bin/`, a cache beside `server.py`, a generated file
        # in `web/` — shows up as a changed or added path.
        before = manifest(APP_DIR)
        result = self.stage('--triple', 'aarch64-unknown-linux-gnu')
        self.assertEqual(result.returncode, 0, result.stderr)
        after = manifest(APP_DIR)
        self.assertEqual(sorted(after), sorted(before), 'staging added or removed a core file')
        changed = [name for name in before if before[name] != after[name]]
        self.assertEqual(changed, [], f'staging rewrote core files: {changed}')

    def test_the_shell_has_no_write_into_the_server_outside_that_copy(self):
        # Static companion to the hash check, for the parts this machine cannot run: desktop
        # Python may write only into its own tree or an operator-supplied `--out`, and the Rust
        # may not write at all (it starts a process and polls it).
        writers = {'write_text', 'write_bytes', 'copyfile', 'copy', 'copy2', 'mkdir', 'chmod',
                   'rename', 'replace', 'unlink', 'rmdir', 'touch'}
        forbidden = r'parents\[\d+\]|APP_DIR|ROOT|/bin|bin/hardlylistening-server'
        for path in sorted(DESKTOP.rglob('*.py')):
            tree = ast.parse(path.read_text(), filename=str(path))
            for node in ast.walk(tree):
                if not isinstance(node, ast.Call):
                    continue
                name = node.func.attr if isinstance(node.func, ast.Attribute) else ''
                if name not in writers:
                    continue
                # Receiver *and* arguments: `shutil.copyfile(source, target)` puts the destination
                # second, and `(root / 'x').write_text(...)` puts it outside the call entirely.
                pieces = [ast.unparse(node.func.value)] if isinstance(node.func, ast.Attribute) else []
                pieces += [ast.unparse(argument) for argument in node.args]
                target = ' | '.join(pieces)
                self.assertNotRegex(
                    target, forbidden,
                    f'{path.relative_to(APP_DIR)}: {name}() targets {target}')
        for path in sorted((DESKTOP / 'src-tauri').rglob('*.rs')):
            text = path.read_text()
            for write in ('fs::write', 'fs::copy', 'File::create', 'fs::remove', 'fs::rename',
                          'fs::create_dir', 'OpenOptions'):
                self.assertNotIn(write, text, f'{path.relative_to(APP_DIR)}: {write} writes to disk')


# -- 4. the packaged installs carry no shell ----------------------------------------------------

class ThePackagedInstallsCarryNoShell(unittest.TestCase):
    def test_the_docker_image_starts_the_server_and_never_a_host(self):
        text = (APP_DIR / 'Dockerfile').read_text()
        instructions = docker_instructions(text)
        self.assertTrue(instructions)
        for instruction in instructions:
            for token, pattern in HOST_TOKENS.items():
                self.assertIsNone(pattern.search(instruction),
                                  f'Dockerfile {token}: {instruction[:100]}')
        # The runtimes are the server's, not a host toolchain: no npm/cargo installs at all.
        runs = [line for line in instructions if line.startswith('RUN ')]
        self.assertTrue(runs)
        self.assertFalse(any('curl' in line or 'git clone' in line for line in runs),
                         'the image is built from this tree, not from a fetched toolchain')
        self.assertIn('ENTRYPOINT ["/app/bin/hardlylistening-server"]', instructions)
        self.assertIn('CMD ["run"]', instructions)
        # Resolved back into the tree, so the entrypoint cannot name a file that is not there.
        entrypoint = '/app/bin/hardlylistening-server'
        target = APP_DIR / entrypoint[len('/app/'):]
        self.assertTrue(target.is_file(), 'the image entrypoint is not a file in this tree')
        self.assertTrue(os.access(target, os.X_OK))
        health = [line for line in instructions if line.startswith('HEALTHCHECK')]
        self.assertTrue(health)
        self.assertIn('/api/health', health[0], 'health means the server answered')
        # Stated, not hidden: `COPY . /app` carries the whole build context, so `desktop/` would
        # land in the image as inert data unless `.dockerignore` excluded it. What this test can
        # honestly pin is that no instruction *uses* it; see docs/desktop/dependency.md.
        copies = [line for line in instructions if line.startswith(('COPY ', 'ADD '))]
        self.assertEqual(copies, ['COPY . /app'])

    def test_the_committed_unit_starts_the_launcher_and_names_no_shell(self):
        path = APP_DIR / 'deploy' / 'hardlylistening.service'
        text = path.read_text()
        settings = unit_sections(text)
        self.assertIn('ExecStart', settings)
        self.assertEqual(settings['ExecStart'], ['/opt/hardlylistening/bin/hardlylistening-server run'])
        for line in text.splitlines():
            for token, pattern in HOST_TOKENS.items():
                self.assertIsNone(pattern.search(line), f'{path.name} {token}: {line.strip()}')
        # Every path it runs or enters maps onto this tree: the unit cannot start a runtime that
        # the server does not contain.
        for key in ('ExecStart', 'ExecStartPre', 'WorkingDirectory'):
            for value in settings.get(key, []):
                argv = value.split()
                first = argv[0].replace('/opt/hardlylistening', str(APP_DIR))
                self.assertTrue(Path(first).exists(), f'{key} names {argv[0]}, which is absent')
        self.assertEqual(settings['WorkingDirectory'], ['/opt/hardlylistening'])
        # Writable paths are state and media only — never the application tree, where a shell
        # would have to live to be written into.
        for value in settings.get('ReadWritePaths', []):
            self.assertNotEqual(value, '/opt/hardlylistening')
            self.assertFalse(value.startswith('/opt/hardlylistening/'))

    def test_the_per_user_unit_install_service_writes_names_no_shell(self):
        text = (APP_DIR / 'install-service.sh').read_text()
        for line in text.splitlines():
            for token, pattern in HOST_TOKENS.items():
                self.assertIsNone(pattern.search(line), f'install-service.sh {token}: {line.strip()}')
        # The unit is written into a heredoc; take the `ExecStart=` line out of it and check it
        # starts the server that is in this tree, not a host and not a wrapper around one.
        unit = text[text.index('cat > "$UNIT" <<EOF'):]
        unit = unit[:unit.index('\nEOF')]
        exec_start = next(line for line in unit.splitlines() if line.startswith('ExecStart='))
        self.assertIn('server.py', exec_start)
        self.assertTrue((APP_DIR / 'server.py').is_file())
        self.assertNotIn('desktop', unit)
        # WorkingDirectory is the checkout root, which is where `server.py` is.
        working = next(line for line in unit.splitlines() if line.startswith('WorkingDirectory='))
        self.assertEqual(working, 'WorkingDirectory=$HERE')


if __name__ == '__main__':
    unittest.main()