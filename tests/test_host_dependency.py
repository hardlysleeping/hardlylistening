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
    after staging: the shell reads the core and writes into its own `src-tauri/bin`, nowhere in
    the core. The static companion judges write *targets* rather than write calls — the shell owns
    its config/state directory, which HOST-003 and HOST-005 write, and may not write anywhere
    reachable from the server's tree; the rule and its negative controls are in
    `docs/desktop/dependency.md` §2.3.
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


# -- write targets: where the shell is allowed to put a file ------------------------------------
#
# The shell owns files: `host.json`, `lifecycle.json`, the autostart entry, a staged sidecar. What
# it may not do is write into the server's tree — the application directory, the database, the
# music root or `web/`. A blanket "no filesystem writes in `desktop/`" cannot say that: HOST-003
# legitimately writes its handoff record into the *config* directory, and HOST-005 writes the
# address file and the autostart entry. So writes are classified by their target instead of
# forbidden outright, and the two halves are separate assertions:
#
#   * nothing a write targets may be reachable from the server's tree (SERVER_TREE markers);
#   * everything a write targets must be reachable from a location the shell owns
#     (SHELL_RESOLVERS) — a write to a bare parameter counts only when every call site in the same
#     file hands that parameter such a location, which is what makes "the shell reads a path and
#     writes it" checkable without a type system.
#
# Text-level, not a compiler: locals are followed through single-name assignments, and a parameter
# is followed into its call sites in the same file, up to `CALL_DEPTH` frames. An alias built
# three functions away in another file is not traced — stated in docs/desktop/dependency.md, and
# covered for the runnable half by the staging manifest and the boot test.

#: Names that mean a path belonging to the server. A target whose resolved text contains one of
#: these is a dependency pointing the wrong way, whatever the variable is called.
SERVER_TREE = (
    'app_dir', 'APP_DIR', 'parents[', 'resource_dir', 'current_exe',
    'launcher', 'LAUNCHER_NAME', 'bin/hardlylistening-server',
    'server.py', 'cli.py', "'web'", '"web"', 'index.html',
    'HARDLYLISTENING_DB', 'HARDLYLISTENING_DATA_DIR', 'HARDLYLISTENING_MUSIC_DIR',
    'HARDLYLISTENING_SERVER', 'music_dir', '.db',
)

#: Resolvers that produce a location the shell owns: XDG state/config (where `host.json` and
#: `lifecycle.json` live), the autostart entry, and `desktop/`'s own build directory.
SHELL_RESOLVERS = (
    'state_path(', 'config_path(', 'autostart_path(', '.config_path', '.autostart_path',
    'HANDOFF_FILE', 'DESKTOP', 'args.out', 'src-tauri',
)

#: How many parameter frames a target is followed through, per file.
CALL_DEPTH = 3

#: Path methods that write. `str.replace` and `dataclasses.replace` are not file operations, so
#: `replace` is deliberately absent here; the file-level form, `os.replace`, is a module call.
PY_WRITE_METHODS = {'write_text', 'write_bytes', 'mkdir', 'unlink', 'rename', 'chmod', 'touch'}
PY_WRITE_CALLS = {('shutil', 'copyfile'), ('shutil', 'copy'), ('shutil', 'copy2'),
                  ('shutil', 'move'), ('os', 'replace'), ('os', 'rename'), ('os', 'chmod'),
                  ('os', 'remove'), ('os', 'unlink'), ('os', 'makedirs')}
PY_METHOD_DESTINATION = {'copyfile': -1, 'copy': -1, 'copy2': -1, 'move': -1,
                         'replace': -1, 'rename': -1, 'chmod': 0, 'remove': 0, 'unlink': 0,
                         'makedirs': 0}

RUST_WRITE = re.compile(
    r'\bfs::(write|rename|copy|create_dir|create_dir_all|remove_file|remove_dir|remove_dir_all'
    r'|set_permissions|hard_link)\s*\(|\bFile::create\s*\(')
RUST_DESTINATION_SECOND = {'rename', 'copy', 'hard_link'}
RUST_BINDING = re.compile(
    r'\b(?:let|if\s+let)\s+(?:Some\()?(?:mut\s+)?([a-z_][a-z0-9_]*)\)?\s*(?::[^=;]+)?='
    r'\s*(.+?)(?:;|\{|\n|$)', re.M)

BRACKETS = {'(': ')', '[': ']', '{': '}'}


def matching(text, start):
    """Index of the bracket closing the one at `start`, skipping string and char literals."""
    closer = BRACKETS[text[start]]
    depth = 0
    index = start
    while index < len(text):
        char = text[index]
        if char == '"':
            index += 1
            while index < len(text):
                if text[index] == '\\':
                    index += 2
                    continue
                if text[index] == '"':
                    break
                index += 1
        elif char == "'" and (text[index + 1:index + 2] == '\\'
                              or text[index + 2:index + 3] == "'"):
            index += 3                       # a char literal, not a Rust lifetime
            continue
        elif char == text[start]:
            depth += 1
        elif char == closer:
            depth -= 1
            if depth == 0:
                return index
        index += 1
    return -1


def arguments(text, open_index):
    """Top-level arguments of the call whose `(` sits at `open_index`."""
    close = matching(text, open_index)
    if close < 0:
        return []
    body = text[open_index + 1:close]
    pieces, depth, current, index = [], 0, '', 0
    while index < len(body):
        char = body[index]
        if char == '"':
            start = index
            index += 1
            while index < len(body):
                if body[index] == '\\':
                    index += 2
                    continue
                if body[index] == '"':
                    break
                index += 1
            current += body[start:index + 1]
        elif char in BRACKETS:
            depth += 1 if char in '([{' else -1
            current += char
        elif char == ',' and depth == 0:
            pieces.append(current.strip())
            current = ''
        else:
            current += char
        index += 1
    pieces.append(current.strip())
    return [piece for piece in pieces if piece]


def enclosing(functions, offset):
    """The name of the function whose body holds `offset`, or '' at module level."""
    for name, (start, end) in functions.items():
        if start <= offset <= end:
            return name
    return ''


class Source:
    """One shell source file, reduced to what a write-target analysis needs.

    `writes` are `(function, destination, arguments)`; `bindings` map `(function, name)` to the
    right-hand sides assigned in that function; `calls` map a callee name to `(caller, arguments)`
    so a parameter can be followed to the values it is given. Python is parsed; Rust is scanned,
    because there is no Rust parser in the standard library.
    """

    def __init__(self, label, text, language):
        self.label = label
        self.language = language
        self.functions = {}                  # name -> (start, end) offsets of the body
        self.params = {}                     # name -> parameter names, in order
        self.bindings = {}                   # (function, name) -> [right-hand side text]
        self.calls = {}                      # callee -> [(caller, [argument text])]
        self.writes = []                     # (function, destination, [argument text])
        if language == 'python':
            self._read_python(text)
        else:
            self._read_rust(text)

    # -- python -----------------------------------------------------------------------------
    def _read_python(self, text):
        tree = ast.parse(text, filename=self.label)
        module = self

        def visit(node, function):
            if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
                module.params[node.name] = [argument.arg for argument in node.args.args
                                            if argument.arg != 'self']
                module.functions.setdefault(node.name, (node.lineno, node.end_lineno))
                for child in ast.walk(node):
                    if isinstance(child, ast.Assign) and len(child.targets) == 1 \
                            and isinstance(child.targets[0], ast.Name):
                        module.bindings.setdefault(
                            (node.name, child.targets[0].id), []).append(
                            ast.unparse(child.value))
                    elif isinstance(child, ast.AnnAssign) and isinstance(child.target, ast.Name) \
                            and child.value is not None:
                        module.bindings.setdefault(
                            (node.name, child.target.id), []).append(ast.unparse(child.value))
                for child in node.body:
                    visit(child, node.name)
                return
            if isinstance(node, ast.Call):
                callee, destination = self._python_write(node)
                if callee is not None:
                    module.writes.append((function, destination,
                                          [ast.unparse(argument) for argument in node.args]))
                name = node.func.id if isinstance(node.func, ast.Name) else (
                    node.func.attr if isinstance(node.func, ast.Attribute) else '')
                if name in ('config_path', 'state_path', 'autostart_path') or name in (
                        'write_state', 'write_atomic', '_write_config', 'write_autostart',
                        '_backup_unreadable', 'write_config', 'set_autostart'):
                    module.calls.setdefault(name, []).append(
                        (function, [ast.unparse(argument) for argument in node.args]))
            for child in ast.iter_child_nodes(node):
                visit(child, function)

        visit(tree, '')

    @staticmethod
    def _python_write(node):
        """(callee, destination text) when this call writes, else (None, None)."""
        func = node.func
        if isinstance(func, ast.Attribute):
            # Module-qualified first: `os.replace` and `shutil.copyfile` are file operations, while
            # an unqualified `text.replace(...)` is a string method and must not look like one.
            if isinstance(func.value, ast.Name) and (func.value.id, func.attr) in PY_WRITE_CALLS:
                position = PY_METHOD_DESTINATION[func.attr]
                if position < len(node.args):
                    return func.attr, ast.unparse(node.args[position])
                return func.attr, ''
            if func.attr in PY_WRITE_METHODS:
                return func.attr, ast.unparse(func.value)
            if func.attr == 'open':
                mode = node.args[0] if node.args else None
                if isinstance(mode, ast.Constant) and isinstance(mode.value, str) \
                        and set(mode.value) & set('wax+'):
                    return 'open', ast.unparse(func.value)
                return None, None
        if isinstance(func, ast.Name) and func.id == 'open' and len(node.args) > 1:
            mode = node.args[1]
            if isinstance(mode, ast.Constant) and isinstance(mode.value, str) \
                    and set(mode.value) & set('wax+'):
                return 'open', ast.unparse(node.args[0])
        return None, None

    # -- rust -------------------------------------------------------------------------------
    def _read_rust(self, text):
        signature = re.compile(r'\bfn\s+([a-z_][a-z0-9_]*)\s*\(')
        for match in signature.finditer(text):
            name = match.group(1)
            open_index = match.end() - 1
            close = matching(text, open_index)
            if close < 0:
                continue
            params = []
            for argument in arguments(text, open_index):
                head = argument.split(':', 1)[0].strip()
                if re.fullmatch(r'(?:mut\s+)?[a-z_][a-z0-9_]*', head):
                    params.append(head.split()[-1])
            body_start = text.find('{', close)
            body_end = matching(text, body_start) if body_start >= 0 else -1
            if body_end < 0:
                continue
            self.params.setdefault(name, params)
            self.functions.setdefault(name, (body_start, body_end))
            body = text[body_start:body_end]
            for binding in RUST_BINDING.finditer(body):
                self.bindings.setdefault((name, binding.group(1)), []).append(
                    binding.group(2).strip().rstrip(';{').strip())
        for match in RUST_WRITE.finditer(text):
            open_index = text.find('(', match.end() - 1)
            args = arguments(text, open_index)
            function = enclosing(self.functions, match.start())
            kind = match.group(1) or 'create'
            destination = args[1] if kind in RUST_DESTINATION_SECOND and len(args) > 1 else (
                args[0] if args else '')
            self.writes.append((function, destination, args))
        for callee, params in self.params.items():
            for caller, (start, end) in self.functions.items():
                body = text[start:end]
                for call in re.finditer(rf'\b{callee}\s*\(', body):
                    self.calls.setdefault(callee, []).append(
                        (caller, arguments(body, call.end() - 1)))

    # -- resolution -------------------------------------------------------------------------
    def cone(self, expression, function, depth=0, seen=None):
        """Every piece of text on which a target expression's value depends.

        Locals are followed through their assignments; a parameter is followed into every call
        site in this file. Capped at `CALL_DEPTH` frames so a cycle in the call graph cannot loop.
        """
        seen = set() if seen is None else seen
        pieces = [expression]
        for name in set(re.findall(r'[A-Za-z_][A-Za-z0-9_]*', expression)):
            key = (function, name)
            if key in seen:
                continue
            seen.add(key)
            for right in self.bindings.get(key, ()):
                pieces += self.cone(right, function, depth, seen)
            if name in self.params.get(function, ()) and depth < CALL_DEPTH:
                index = self.params[function].index(name)
                for caller, args in self.calls.get(function, ()):
                    if index < len(args):
                        pieces += self.cone(args[index], caller, depth + 1, seen)
        return pieces


def shell_sources():
    """Every Python and Rust file in the shell, as `Source` objects."""
    for path in sorted(DESKTOP.rglob('*.py')):
        yield Source(str(path.relative_to(APP_DIR)), path.read_text(), 'python'), path
    for path in sorted(DESKTOP.rglob('*.rs')):
        yield Source(str(path.relative_to(APP_DIR)), path.read_text(), 'rust'), path


def tree_offenders(sources):
    """Writes whose target's cone reaches the server's tree, as report lines."""
    offenders = []
    for source in sources:
        for function, destination, _args in source.writes:
            text = ' '.join(source.cone(destination, function))
            hits = [marker for marker in SERVER_TREE if marker in text]
            if hits:
                offenders.append(
                    f'{source.label}:{function or "<module>"} {destination!r} -> {hits}')
    return offenders


def unowned_offenders(sources):
    """Writes whose target's cone names no location the shell owns, as report lines."""
    offenders = []
    for source in sources:
        for function, destination, _args in source.writes:
            text = ' '.join(source.cone(destination, function))
            if not any(resolver in text for resolver in SHELL_RESOLVERS):
                offenders.append(
                    f'{source.label}:{function or "<module>"} {destination!r} -> {text[:120]}')
    return offenders


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

    def test_no_desktop_write_targets_the_servers_tree(self):
        """The shell may write its own files; it may not write the server's.

        The target is what is judged, not the presence of a write call: HOST-003 writes
        `lifecycle.json` into the config directory and HOST-005 writes `host.json` and the
        autostart entry, and all three are the shell's own state. What is forbidden is a target
        reachable from the application directory, the database, the music root or `web/`.
        """
        offenders = tree_offenders(source for source, _path in shell_sources())
        self.assertEqual(offenders, [], 'a shell write could land in the server’s tree')

    def test_every_desktop_write_goes_through_a_shell_owned_location(self):
        """The positive half: a write target must resolve to something the shell owns.

        A target that is a bare parameter is followed into its call sites in the same file, so
        `write_state(config, …)` counts only because its callers pass a config path. A write with
        no such ancestry (a literal path, an environment variable, a helper that names nothing) is
        a failure here even when it happens to be harmless — it is the one rule that keeps the
        exceptions reviewable.
        """
        offenders = unowned_offenders(source for source, _path in shell_sources())
        self.assertEqual(offenders, [], 'every shell write must name a location it owns')

    def test_the_handoff_record_is_written_beside_the_config_file_only(self):
        """HOST-003's collision, pinned: the one new write is the window's own memory.

        `lifecycle.json` is derived from the config path's directory — never the application
        directory — in both copies of that rule (the Rust that runs and the Python that is
        tested). The last assertions make "derived from the config path" mean something: every
        `config` value in the crate comes from `host::config_path()`, and that resolver is XDG
        state or the user's home, never the tree the server lives in.
        """
        for relative in ('src-tauri/src/lifecycle.rs', 'host/lifecycle.py'):
            source = Source(relative, (DESKTOP / relative).read_text(),
                            'rust' if relative.endswith('.rs') else 'python')
            self.assertTrue(source.writes, f'{relative} should hold the handoff write')
            for function, destination, _args in source.writes:
                text = ' '.join(source.cone(destination, function))
                self.assertIn('state_path(', text,
                              f'{relative}:{function} writes {destination!r}, not the state file')
            # And `state_path` itself is the config path's directory plus the fixed file name.
            self.assertIn('state_path', source.functions)
        crate = ''.join(path.read_text()
                        for path in sorted((DESKTOP / 'src-tauri' / 'src').glob('*.rs')))
        for binding in re.finditer(r'\blet\s+config\s*=\s*([^;]+);', crate):
            self.assertEqual(binding.group(1).strip(), 'host::config_path()',
                             'a config value in the crate came from somewhere else')
        host = (DESKTOP / 'src-tauri' / 'src' / 'host.rs').read_text()
        resolver = host[host.index('pub fn config_path'):]
        resolver = resolver[:resolver.index('\n}')]
        self.assertIn('XDG_STATE_HOME', resolver)
        self.assertIn('HARDLYLISTENING_DESKTOP_CONFIG', resolver)
        for server in ('app_dir', 'resource_dir', 'current_exe', 'launcher'):
            self.assertNotIn(server, resolver, f'config_path() reaches {server}')

    def test_the_write_scan_bites(self):
        """Negative controls: the narrowed rule still fails on the writes it exists for.

        Each snippet runs through the same predicates the real files run through. If a future
        edit loosens the classifier, these stop failing and so does the point of it.
        """
        rust_into_tree = Source('synthetic.rs', '''
fn write_state(config: &Path) {
    let path = app_dir().join("bin");
    std::fs::write(&path, "#!/bin/sh\\n");
}
''', 'rust')
        rust_into_launcher = Source('synthetic.rs', '''
fn f() {
    std::fs::write("/opt/hardlylistening/server.py", "x");
}
''', 'rust')
        python_into_web = Source('synthetic.py', '''
def f(app_dir):
    page = app_dir / 'web' / 'index.html'
    page.write_text('x')
''', 'python')
        python_into_db = Source('synthetic.py', '''
import os
def f():
    open(os.environ['HARDLYLISTENING_DB'], 'w').write('x')
''', 'python')
        for source, marker in ((rust_into_tree, 'app_dir'), (rust_into_launcher, 'server.py'),
                               (python_into_web, "'web'"), (python_into_db, 'HARDLYLISTENING_DB')):
            self.assertTrue(source.writes, 'the synthetic write was not detected at all')
            reported = tree_offenders([source])
            self.assertTrue(reported, f'a write into the server tree escaped: {source.label}')
            self.assertIn(marker, reported[0], f'the scanner lost {marker}')

        # And the positive rule: a write to a path nobody owns is a failure, not a pass.
        unowned = Source('synthetic.rs', '''
fn f() {
    std::fs::write("/var/tmp/scratch.json", "{}");
}
''', 'rust')
        self.assertTrue(unowned_offenders([unowned]),
                        'a write outside every location the shell owns must not look owned')
        # A legitimate config/state write is not an offender, so the narrowing is real and this
        # is not simply a test that fails everything.
        owned = Source('synthetic.rs', '''
fn write_state(config: &Path) {
    let path = state_path(config);
    std::fs::write(&path, "{}");
}
''', 'rust')
        self.assertEqual(tree_offenders([owned]), [])
        self.assertEqual(unowned_offenders([owned]), [])


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
        # `COPY . /app` carries the whole build context, so the exclusion is `.dockerignore`'s job:
        # the image is the server, and the shell is a client that is not in it.
        copies = [line for line in instructions if line.startswith(('COPY ', 'ADD '))]
        self.assertEqual(copies, ['COPY . /app'])
        excluded = {line.split('#', 1)[0].strip().strip('/')
                    for line in (APP_DIR / '.dockerignore').read_text().splitlines()}
        excluded.discard('')
        self.assertIn('desktop', excluded,
                      '.dockerignore must exclude desktop/, or COPY . /app bakes in the shell')

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