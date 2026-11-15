"""The executing half of the opt-in extension host: one child process, mediated calls, an audit.

`extensions.py` is the grant: what may be installed, what it may ask for, and how it is taken away.
This module is the loader. It runs **one enabled extension, once**, in a child process, and answers
the calls that process makes. Nothing is loaded at boot, nothing is scheduled, and a server with no
extensions runs exactly the code it ran before this file existed.

**This is not a sandbox.** The child runs as the same user with the same filesystem, so an
extension is trusted code, and trusting it is what `enable()` means. What the process boundary
buys is not containment but three things that are worth having anyway:

* the extension cannot import this application (`python3 -I` keeps it off `sys.path`), and it gets
  an environment holding nothing but `PATH`, `HOME` and a locale — no database path, no keys;
* every call it makes passes through `mediate()`, which reads the grant on every request, so a
  revoke takes effect on the next call rather than at the next restart;
* each call and each run is written to `extension_calls` / `extension_runs`, so "what did it ask
  for" is a query and not a guess.

What an extension may call today is the read surface only:

    library.read   the triggering member's own library, paged, without file paths or source URLs
    social.read    the Activity feed, capped
    network        one GET to a host the manifest declared, https, no redirects, bounded

`library.write`, `social.write` and `background` are named in the capability set and **refused
here** with a sentence; they are grants the host does not yet offer, not grants it honours. Keeping
the refusal explicit is the point — a manifest may ask for them, and the answer is a refusal an
administrator can read, not a silent no-op.

Installation is from disk, not from a request body: an extension is a directory under
`paths.EXTENSIONS_DIR` holding `manifest.json` and its entry file. There is no upload route,
because accepting code over HTTP is a bigger decision than this host makes.
"""
from __future__ import annotations

import json
import os
import select
import ssl
import subprocess
import sys
import tempfile
import time
import urllib.error
import urllib.parse
import urllib.request

import extensions
from paths import EXTENSIONS_DIR

#: One protocol line. A call or a result larger than this is a broken extension, not a big answer.
MAX_LINE = 1024 * 1024
#: A run's reported output. Matches `extension_shim.MAX_OUTPUT`.
MAX_OUTPUT = 64 * 1024
#: One network response body.
MAX_BODY = 2 * 1024 * 1024
#: How much of the child's stderr is kept beside a run. Enough for a traceback, not a log file.
MAX_STDERR = 4 * 1024
#: Wall clock for one run, including interpreter start and import.
RUN_TIMEOUT = 20.0
#: Audit retention, per extension. Old rows go, so a chatty extension cannot grow the database.
KEEP_RUNS = 200
KEEP_CALLS = 500

#: The capability each mediated method needs. A method absent from this map is not offered.
METHODS = {
    'library.read': 'library.read',
    'social.read': 'social.read',
    'network': 'network',
}

SCHEMA = """\
CREATE TABLE IF NOT EXISTS extension_runs (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  ext_id TEXT NOT NULL,
  member_id TEXT NOT NULL DEFAULT '',
  started REAL NOT NULL,
  finished REAL NOT NULL DEFAULT 0,
  status TEXT NOT NULL DEFAULT 'running',
  message TEXT NOT NULL DEFAULT '',
  calls INTEGER NOT NULL DEFAULT 0,
  output TEXT NOT NULL DEFAULT '',
  stderr TEXT NOT NULL DEFAULT ''
);
CREATE TABLE IF NOT EXISTS extension_calls (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  ext_id TEXT NOT NULL,
  run_id INTEGER NOT NULL DEFAULT 0,
  method TEXT NOT NULL,
  detail TEXT NOT NULL DEFAULT '',
  ok INTEGER NOT NULL DEFAULT 0,
  at REAL NOT NULL
);
CREATE INDEX IF NOT EXISTS extension_calls_owner ON extension_calls(ext_id, id);"""


def schema(store) -> None:
    """Create the two audit tables if a quiet database lacks them. Idempotent."""
    with store.lock:
        store.conn.executescript(SCHEMA)
        store.conn.commit()


def directory(ext_id: str):
    """The one directory an extension id may name, resolved and proven to be directly beneath it."""
    text = str(ext_id or '').strip()
    if not extensions.ID.match(text):
        raise extensions.ExtensionError('not an extension id: %r' % text)
    base = EXTENSIONS_DIR.resolve()
    root = (base / text).resolve()
    if root.parent != base:
        raise extensions.ExtensionError('%r does not name a directory under %s' % (text, base))
    return root


def on_disk() -> list[dict]:
    """Directories under the extensions root that carry a manifest, whether installed or not.

    Reading this is how an administrator knows what can be installed. A directory is reported with
    its own error rather than hidden, because "I put it there and nothing happened" is the failure
    mode this list exists to prevent.
    """
    base = EXTENSIONS_DIR
    if not base.is_dir():
        return []
    out = []
    for entry in sorted(base.iterdir(), key=lambda item: item.name)[:200]:
        if not entry.is_dir() or not (entry / 'manifest.json').is_file():
            continue
        item = {'id': entry.name, 'path': str(entry), 'manifest': None, 'error': ''}
        try:
            raw = json.loads((entry / 'manifest.json').read_text(encoding='utf-8'))
            clean = extensions.validate(raw)
            item['manifest'] = {key: clean[key] for key in
                                ('id', 'name', 'version', 'description', 'entry',
                                 'capabilities', 'hosts')}
            if clean['id'] != entry.name:
                item['error'] = 'manifest id %r does not match the directory name' % clean['id']
            elif clean['entry'] and not (entry / clean['entry']).is_file():
                item['error'] = 'entry file %r is missing' % clean['entry']
        except (OSError, ValueError) as error:
            item['error'] = str(error)
        out.append(item)
    return out


def install_from_disk(store, ext_id: str, by: str = '') -> dict:
    """Read `manifest.json` from the extension's own directory and record it, disabled."""
    root = directory(ext_id)
    manifest_path = root / 'manifest.json'
    if not manifest_path.is_file():
        raise extensions.ExtensionError('no manifest.json in %s' % root)
    try:
        raw = json.loads(manifest_path.read_text(encoding='utf-8'))
    except ValueError as error:
        raise extensions.ExtensionError('manifest.json is not valid JSON: %s' % error)
    clean = extensions.validate(raw)
    if clean['id'] != str(ext_id):
        raise extensions.ExtensionError('manifest id %r does not match the directory name'
                                        % clean['id'])
    if clean['entry']:
        entry = (root / clean['entry']).resolve()
        if entry.parent != root.resolve() or not entry.is_file():
            raise extensions.ExtensionError('entry file %r is missing or outside the extension'
                                            % clean['entry'])
    return extensions.install(store, clean, by=by)


def _member_id(member) -> str:
    if not member:
        return ''
    return str(member.get('id') or '')


def _audit(store, ext_id: str, run_id: int, method: str, detail: str, ok: bool) -> None:
    with store.lock:
        store.conn.execute(
            'INSERT INTO extension_calls(ext_id,run_id,method,detail,ok,at) VALUES(?,?,?,?,?,?)',
            (ext_id, run_id, method, detail[:400], 1 if ok else 0, time.time()))
        store.conn.execute(
            'DELETE FROM extension_calls WHERE ext_id=? AND id NOT IN'
            ' (SELECT id FROM extension_calls WHERE ext_id=? ORDER BY id DESC LIMIT ?)',
            (ext_id, ext_id, KEEP_CALLS))
        store.conn.commit()


def _log_safe(url: str) -> str:
    """A URL as an audit line may keep it: scheme, host, path — never the query or credentials."""
    try:
        parts = urllib.parse.urlsplit(url)
    except ValueError:
        return '(unparseable url)'
    if not parts.hostname:
        return '(no host)'
    return '%s://%s%s' % (parts.scheme, parts.hostname, parts.path)


def _host_allowed(host: str, declared: list[str]) -> bool:
    host = (host or '').lower()
    for pattern in declared:
        pattern = str(pattern or '').lower()
        if pattern == '*':
            return True
        if pattern.startswith('*.') and host.endswith(pattern[1:]):
            return True
        if host == pattern:
            return True
    return False


class _NoRedirect(urllib.request.HTTPRedirectHandler):
    """A redirect is a second request to a host nobody declared. Refused, and said so."""

    def redirect_request(self, req, fp, code, msg, headers, newurl):
        raise extensions.ExtensionError(
            'redirects are not followed (%d to %s)' % (code, _log_safe(newurl)))


def network(url: str, declared: list[str]) -> dict:
    """One bounded GET to a declared host. https, unless the loopback test hatch is open."""
    text = str(url or '').strip()
    if not text:
        raise extensions.ExtensionError('network needs a url')
    try:
        parts = urllib.parse.urlsplit(text)
        host = (parts.hostname or '').lower()
    except ValueError as error:
        raise extensions.ExtensionError('unparseable url: %s' % error)
    if parts.username or parts.password:
        raise extensions.ExtensionError('credentials in a url are refused')
    if not host:
        raise extensions.ExtensionError('the url has no host')
    if not _host_allowed(host, declared):
        raise extensions.ExtensionError('%s is not a host this extension declared' % host)
    loopback = host in ('127.0.0.1', '::1', 'localhost')
    if parts.scheme != 'https':
        if not (parts.scheme == 'http' and loopback
                and os.environ.get('HARDLYLISTENING_EXTENSION_ALLOW_LOCAL') == '1'):
            raise extensions.ExtensionError('only https is fetched (test hatch: '
                                            'HARDLYLISTENING_EXTENSION_ALLOW_LOCAL=1)')
    request = urllib.request.Request(text, method='GET',
                                     headers={'User-Agent': 'HardlyListening-extension/1'})
    opener = urllib.request.build_opener(
        _NoRedirect, urllib.request.HTTPSHandler(context=ssl.create_default_context()))
    try:
        response = opener.open(request, timeout=10)
    except urllib.error.HTTPError as error:
        response = error                                  # an answer, not a failure
    except urllib.error.URLError as error:
        raise extensions.ExtensionError('request failed: %s' % (error.reason,))
    with response:
        length = response.headers.get('Content-Length')
        if length and length.isdigit() and int(length) > MAX_BODY:
            raise extensions.ExtensionError('the response is larger than %d bytes' % MAX_BODY)
        body = response.read(MAX_BODY + 1)
        if len(body) > MAX_BODY:
            raise extensions.ExtensionError('the response is larger than %d bytes' % MAX_BODY)
        headers = {key.lower(): value for key, value in response.headers.items()
                   if key.lower() in ('content-type', 'content-length', 'etag', 'last-modified')}
        return {'status': int(getattr(response, 'status', None)
                              or getattr(response, 'code', 0) or 0),
                'headers': headers,
                'body': body.decode('utf-8', 'replace')}


#: The columns a `library.read` may keep. `file` is a path on this server and `source_url` is the
#: provider's own URL — neither is the extension's to see under a read grant, and both are exactly
#: how a credential would leave the server.
TRACK_FIELDS = ('id', 'artist_name', 'album', 'title', 'track_number', 'duration',
                'isrc', 'bpm', 'added', 'art_hash')


def mediate(store, ext_id: str, grants: set, method: str, params: dict, member) -> dict:
    """Answer one call from a running extension, or refuse it with a sentence a person can act on."""
    method = str(method or '')
    if method in ('library.write', 'social.write', 'background'):
        raise extensions.ExtensionError('%s is not offered by this host yet' % method)
    if method not in METHODS:
        raise extensions.ExtensionError('unknown method %r' % method)
    if METHODS[method] not in grants:
        raise extensions.ExtensionError('this extension does not hold the %s capability'
                                        % METHODS[method])
    params = params if isinstance(params, dict) else {}

    if method == 'library.read':
        uid = _member_id(member)
        if not uid:
            raise extensions.ExtensionError('library.read needs the member the run belongs to')
        # The union `catalog` reads spans four tables owned by three modules, and an
        # extension may be the first thing to ask on a fresh database. Each schema is
        # idempotent; calling them here is what makes the read work without the web player
        # having visited first.
        from library import folders, saves
        from library import service as library_service
        saves.schema(store)
        folders.schema(store)
        rows = library_service.catalog(store, uid)
        offset = max(0, int(params.get('offset') or 0))
        limit = min(1000, max(1, int(params.get('limit') or 200)))
        page = rows[offset:offset + limit]
        return {'tracks': [{key: row.get(key) for key in TRACK_FIELDS} for row in page],
                'total': len(rows), 'offset': offset, 'limit': limit}

    if method == 'social.read':
        from social import service as social_service
        limit = min(200, max(1, int(params.get('limit') or 50)))
        return {'items': social_service.feed(store, limit)}

    declared = list(extensions.manifest_of(store, ext_id).get('hosts') or [])
    return network(str(params.get('url') or ''), declared)


class _Channel:
    """Line-delimited JSON over the child's pipes, with one wall clock for the whole run."""

    def __init__(self, proc):
        self.proc = proc
        self.fd = proc.stdout.fileno()
        self.buffer = b''

    def send(self, message: dict) -> None:
        data = json.dumps(message, ensure_ascii=True, default=str).encode('utf-8') + b'\n'
        if len(data) > MAX_LINE:
            raise extensions.ExtensionError('the answer is larger than %d bytes; page the read'
                                            % MAX_LINE)
        self.proc.stdin.write(data)
        self.proc.stdin.flush()

    def receive(self, deadline: float) -> dict:
        while b'\n' not in self.buffer:
            remaining = deadline - time.time()
            if remaining <= 0:
                raise TimeoutError
            ready, _, _ = select.select([self.fd], [], [], remaining)
            if not ready:
                raise TimeoutError
            chunk = os.read(self.fd, 65536)
            if not chunk:
                raise EOFError
            self.buffer += chunk
            if len(self.buffer) > MAX_LINE:
                raise extensions.ExtensionError('the extension sent a message larger than %d bytes'
                                                % MAX_LINE)
        line, _, self.buffer = self.buffer.partition(b'\n')
        try:
            message = json.loads(line.decode('utf-8', 'replace'))
        except ValueError:
            raise extensions.ExtensionError('the extension sent a line that is not JSON')
        if not isinstance(message, dict):
            raise extensions.ExtensionError('the extension sent a message that is not an object')
        return message


def _tail(handle, limit: int) -> str:
    try:
        handle.flush()
        size = handle.seek(0, os.SEEK_END)
        handle.seek(max(0, size - limit))
        return handle.read().decode('utf-8', 'replace')
    except (OSError, ValueError):
        return ''


def run(store, ext_id: str, member=None, timeout: float = RUN_TIMEOUT) -> dict:
    """Run one enabled extension once. Returns its status, output, call count and audit id.

    A refusal here is raised before anything is spawned: an extension that is not installed, not
    enabled, has no entry file, or whose entry would leave its own directory never becomes a
    process. Once it does, a crash, a timeout and a refusal inside the extension are statuses and
    audit rows rather than exceptions — the administrator asked for a report, and that is the report.
    """
    schema(store)
    record = next((item for item in extensions.installed(store) if item['id'] == ext_id), None)
    if record is None:
        raise extensions.ExtensionError('no such extension: %s' % ext_id)
    if not record['enabled']:
        raise extensions.ExtensionError('%s is not enabled' % ext_id)
    manifest = extensions.manifest_of(store, ext_id)
    grants = set(record['capabilities'])
    entry = str(manifest.get('entry') or '').strip()
    if not entry:
        raise extensions.ExtensionError('%s has no entry file in its manifest' % ext_id)

    root = directory(ext_id)
    target = (root / entry).resolve()
    if target.parent != root.resolve() or not target.is_file():
        raise extensions.ExtensionError('entry file %r is missing or outside the extension'
                                        % entry)

    started = time.time()
    deadline = started + max(1.0, float(timeout))
    member_id = _member_id(member)
    with store.lock:
        cursor = store.conn.execute(
            'INSERT INTO extension_runs(ext_id,member_id,started,status,message,calls,output,stderr)'
            ' VALUES(?,?,?,?,?,?,?,?)',
            (ext_id, member_id, started, 'running', '', 0, '', ''))
        run_id = int(cursor.lastrowid)
        store.conn.commit()

    shim = os.path.join(os.path.dirname(os.path.abspath(__file__)), 'extension_shim.py')
    environment = {'PATH': '/usr/bin:/bin', 'HOME': str(root),
                   'LANG': 'C.UTF-8', 'LC_ALL': 'C.UTF-8'}
    stderr = tempfile.TemporaryFile()
    status, message, output, calls = 'failed', '', None, 0
    proc = None
    try:
        proc = subprocess.Popen(
            [sys.executable, '-I', '-B', shim, str(root), entry],
            cwd=str(root), env=environment, close_fds=True,
            stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=stderr)
        channel = _Channel(proc)
        while True:
            try:
                incoming = channel.receive(deadline)
            except TimeoutError:
                status = 'timeout'
                message = 'the extension did not finish within %gs' % (deadline - started)
                break
            except EOFError:
                status = 'crashed'
                message = 'the extension exited without finishing'
                break
            if 'call' in incoming:
                calls += 1
                method = str(incoming.get('method') or '')
                params = incoming.get('params')
                detail = _log_safe(params.get('url')) if method == 'network' and isinstance(
                    params, dict) else ''
                try:
                    value = mediate(store, ext_id, grants, method, params, member)
                except extensions.ExtensionError as error:
                    _audit(store, ext_id, run_id, method, detail or str(error), False)
                    channel.send({'error': incoming['call'], 'message': str(error)})
                except Exception as error:              # noqa: BLE001 — a bug in mediation, said
                    _audit(store, ext_id, run_id, method, detail or str(error), False)
                    channel.send({'error': incoming['call'],
                                  'message': '%s: %s' % (type(error).__name__, error)})
                else:
                    # The audit says "this was answered", so it is written after the answer
                    # leaves: a call whose reply never reached the child is not a success.
                    channel.send({'result': incoming['call'], 'value': value})
                    _audit(store, ext_id, run_id, method, detail, True)
            elif incoming.get('done'):
                status = 'finished'
                output = incoming.get('output')
                break
            elif incoming.get('fatal'):
                status = 'failed'
                message = str(incoming.get('fatal') or '')[:2000]
                break
            else:
                status = 'failed'
                message = 'the extension sent a message the host does not understand'
                break
    except (OSError, extensions.ExtensionError) as error:
        status, message = 'failed', str(error)
    finally:
        if proc is not None:
            try:
                proc.wait(timeout=1.0)
            except subprocess.TimeoutExpired:
                proc.kill()
                try:
                    proc.wait(timeout=2.0)
                except subprocess.TimeoutExpired:
                    pass
            for handle in (proc.stdin, proc.stdout):
                try:
                    handle.close()
                except (OSError, ValueError):
                    pass
        tail = _tail(stderr, MAX_STDERR)
        stderr.close()

    finished = time.time()
    text = '' if output is None else json.dumps(output, ensure_ascii=True)
    if len(text) > MAX_OUTPUT:
        status, message, text = 'failed', 'the output is larger than the host accepts', ''
    with store.lock:
        store.conn.execute(
            'UPDATE extension_runs SET finished=?,status=?,message=?,calls=?,output=?,stderr=?'
            ' WHERE id=?',
            (finished, status, message[:2000], calls, text, tail, run_id))
        store.conn.execute(
            'DELETE FROM extension_runs WHERE ext_id=? AND id NOT IN'
            ' (SELECT id FROM extension_runs WHERE ext_id=? ORDER BY id DESC LIMIT ?)',
            (ext_id, ext_id, KEEP_RUNS))
        store.conn.commit()
    return {'id': run_id, 'ext_id': ext_id, 'status': status, 'message': message,
            'output': output, 'calls': calls, 'stderr': tail,
            'duration_ms': int((finished - started) * 1000)}


def runs(store, ext_id: str = '', limit: int = 20) -> list[dict]:
    """Recent runs, newest first. `output` and `stderr` are the stored text, not parsed."""
    schema(store)
    limit = min(200, max(1, int(limit)))
    with store.lock:
        if ext_id:
            rows = store.conn.execute(
                'SELECT * FROM extension_runs WHERE ext_id=? ORDER BY id DESC LIMIT ?',
                (ext_id, limit)).fetchall()
        else:
            rows = store.conn.execute(
                'SELECT * FROM extension_runs ORDER BY id DESC LIMIT ?', (limit,)).fetchall()
    return [dict(row) for row in rows]


def calls(store, ext_id: str = '', limit: int = 100) -> list[dict]:
    """Recent mediated calls, newest first — what the extension asked for, allowed or not."""
    schema(store)
    limit = min(500, max(1, int(limit)))
    with store.lock:
        if ext_id:
            rows = store.conn.execute(
                'SELECT * FROM extension_calls WHERE ext_id=? ORDER BY id DESC LIMIT ?',
                (ext_id, limit)).fetchall()
        else:
            rows = store.conn.execute(
                'SELECT * FROM extension_calls ORDER BY id DESC LIMIT ?', (limit,)).fetchall()
    return [dict(row) for row in rows]