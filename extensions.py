"""Opt-in extensions: what may be installed, what it is allowed to touch, and how it is taken away.

This module deliberately **runs nothing**. It is the bookkeeping half of the extension host — the
part with the security consequences — and it stays separate from the half that executes code
(`extension_host.py`) so that "what is permitted" can be read, tested and revoked without a process
being involved. The plan's ordering puts it that way for a reason: the host is disabled by default,
an extension is inert until a person trusts it by name, and no acquisition capability is bundled
with the server.

The rules this file exists to keep:

* **Nothing enabled by default.** Installing a manifest grants nothing. `enable()` is a separate
  act by an administrator, and it records who did it and when.
* **Capabilities are a closed set.** A manifest may ask for less than the host knows; it may never
  invent a capability, and an unknown one fails the whole manifest instead of being dropped — a
  typo must not silently reduce what the author thought they were asking for.
* **Acquisition is not a capability yet.** `acquisition` is refused at install time until the
  per-member permission of EXT-ACQUIRE exists (HL-219). Membership, player access and even
  administrator status must never imply it, so the server does not carry the word as a grant.
* **Revocation is immediate.** `revoke()` clears the grant and the enabled flag in one write;
  there is no cache to expire, and the loader checks the stored grant on every call, so a revoked
  extension is refused on its next call and cannot start another run.
* **Ids and paths are data, not instructions.** An extension id is a slug and a manifest's entry
  path cannot leave its own directory, so a hostile manifest cannot name `/etc` or another
  extension's files.
"""

from __future__ import annotations

import json
import re
import time

SCHEMA_VERSION = 1

#: What an extension may ask to do. Everything else is refused by name.
CAPABILITIES = {
    'library.read': 'read the shared library and the requesting member’s own library',
    'library.write': 'create or change the requesting member’s own saves, playlists and folders',
    'social.read': 'read activity, comments and votes',
    'social.write': 'post activity, comments and votes as the requesting member',
    'network': 'make outbound requests to hosts it declares in `hosts`',
    'background': 'run scheduled work while the server is up',
}
#: Named here so a refusal can explain itself, and so the word exists nowhere as a grant.
RESERVED = {'acquisition': 'acquisition is a separate, per-member permission (EXT-ACQUIRE) and is '
                           'never granted to an extension by this host'}

ID = re.compile(r'^[a-z0-9][a-z0-9._-]{1,63}$')
VERSION = re.compile(r'^\d+\.\d+\.\d+(?:[-+][0-9A-Za-z.-]+)?$')

MANIFEST_SCHEMA = """\
CREATE TABLE IF NOT EXISTS extensions (
  id TEXT PRIMARY KEY,
  version TEXT NOT NULL,
  name TEXT NOT NULL,
  manifest TEXT NOT NULL,
  trusted_by TEXT DEFAULT '',
  trusted_at REAL DEFAULT 0,
  enabled INTEGER DEFAULT 0,
  revoked_at REAL DEFAULT 0
);"""


def schema(store) -> None:
    """Create the table if a quiet database lacks it. Idempotent, like every schema here."""
    with store.lock:
        store.conn.execute(MANIFEST_SCHEMA)
        store.conn.commit()


class ExtensionError(ValueError):
    """A manifest, a grant or a request the host refuses, with the reason a person can act on."""


def _slug(value, what: str) -> str:
    text = str(value or '').strip()
    if not ID.match(text):
        raise ExtensionError('%s must be 2–64 characters of a-z, 0-9, dot, dash or underscore: %r'
                             % (what, text))
    return text


def validate(manifest: dict) -> dict:
    """The manifest, normalised, or an error naming the field that is wrong.

    Every field a manifest carries is optional except `id`, `name` and `version`, because an
    extension that does nothing is allowed to exist — it simply has no capability and therefore
    cannot reach anything.
    """
    if not isinstance(manifest, dict):
        raise ExtensionError('a manifest is a JSON object')
    out = {
        'schema': int(manifest.get('schema') or SCHEMA_VERSION),
        'id': _slug(manifest.get('id'), 'id'),
        'name': str(manifest.get('name') or '').strip(),
        'version': str(manifest.get('version') or '').strip(),
        'description': str(manifest.get('description') or '').strip()[:400],
        'entry': str(manifest.get('entry') or '').strip(),
        'capabilities': [],
        'hosts': [],
    }
    if out['schema'] != SCHEMA_VERSION:
        raise ExtensionError('unsupported manifest schema %r (this host speaks %d)'
                             % (out['schema'], SCHEMA_VERSION))
    if not out['name'] or len(out['name']) > 80:
        raise ExtensionError('name is required and up to 80 characters')
    if not VERSION.match(out['version']):
        raise ExtensionError('version must be semantic (1.2.3): %r' % out['version'])

    asked = manifest.get('capabilities') or []
    if not isinstance(asked, list):
        raise ExtensionError('capabilities must be a list')
    for capability in asked:
        name = str(capability or '').strip()
        if name in RESERVED:
            raise ExtensionError(RESERVED[name])
        if name not in CAPABILITIES:
            # Fails the manifest rather than dropping the entry: a typo must not quietly reduce
            # what the author believed they were asking for.
            raise ExtensionError('unknown capability %r (known: %s)'
                                 % (name, ', '.join(sorted(CAPABILITIES))))
        if name not in out['capabilities']:
            out['capabilities'].append(name)

    hosts = manifest.get('hosts') or []
    if not isinstance(hosts, list):
        raise ExtensionError('hosts must be a list')
    for host in hosts:
        text = str(host or '').strip().lower()
        if not re.fullmatch(r'[a-z0-9.*-]{1,253}', text):
            raise ExtensionError('host must be a bare hostname or wildcard: %r' % text)
        if text not in out['hosts']:
            out['hosts'].append(text)
    if out['hosts'] and 'network' not in out['capabilities']:
        raise ExtensionError('hosts are declared but the network capability was not requested')

    entry = out['entry']
    if entry:
        parts = entry.split('/')
        if entry.startswith('/') or '..' in parts or not entry.strip():
            raise ExtensionError('entry must be a path inside the extension, not %r' % entry)
    return out


def install(store, manifest: dict, by: str = '') -> dict:
    """Record an extension as **installed but disabled**. Trust is a second, separate act."""
    clean = validate(manifest)
    schema(store)
    with store.lock:
        row = store.conn.execute('SELECT version FROM extensions WHERE id=?',
                                 (clean['id'],)).fetchone()
        if row and row['version'] == clean['version']:
            raise ExtensionError('%s %s is already installed' % (clean['id'], clean['version']))
        store.conn.execute(
            'INSERT OR REPLACE INTO extensions(id,version,name,manifest,trusted_by,trusted_at,'
            'enabled,revoked_at) VALUES(?,?,?,?,?,?,0,0)',
            (clean['id'], clean['version'], clean['name'], json.dumps(clean), str(by or ''),
             time.time() if by else 0))
        store.conn.commit()
    return clean


def enable(store, ext_id: str, by: str) -> dict:
    """Trust it by name. Refused without an administrator, because this is the act that matters."""
    if not by:
        raise ExtensionError('enabling an extension needs an administrator')
    schema(store)
    with store.lock:
        row = store.conn.execute('SELECT * FROM extensions WHERE id=?',
                                 (_slug(ext_id, 'id'),)).fetchone()
        if not row:
            raise ExtensionError('no such extension: %s' % ext_id)
        if row['revoked_at']:
            # A revoked extension is not silently revived: it is installed again, which is a
            # new decision with a new manifest.
            raise ExtensionError('%s was revoked; reinstall it to trust it again' % ext_id)
        store.conn.execute('UPDATE extensions SET enabled=1,trusted_by=?,trusted_at=? WHERE id=?',
                           (str(by), time.time(), ext_id))
        store.conn.commit()
    return manifest_of(store, ext_id)


def revoke(store, ext_id: str) -> None:
    """Take everything back in one write: no grant survives a revocation."""
    schema(store)
    with store.lock:
        store.conn.execute('UPDATE extensions SET enabled=0,revoked_at=? WHERE id=?',
                           (time.time(), _slug(ext_id, 'id')))
        store.conn.commit()


def remove(store, ext_id: str) -> None:
    """Forget it entirely. Audio, saves and history are not the extension's to delete."""
    schema(store)
    with store.lock:
        store.conn.execute('DELETE FROM extensions WHERE id=?', (_slug(ext_id, 'id'),))
        store.conn.commit()


def manifest_of(store, ext_id: str) -> dict:
    with store.lock:
        row = store.conn.execute('SELECT manifest FROM extensions WHERE id=?',
                                 (_slug(ext_id, 'id'),)).fetchone()
    if not row:
        raise ExtensionError('no such extension: %s' % ext_id)
    return json.loads(row['manifest'])


def installed(store) -> list[dict]:
    """Every recorded extension, with the capabilities it would hold *if* it were enabled."""
    schema(store)
    with store.lock:
        rows = store.conn.execute('SELECT * FROM extensions ORDER BY id').fetchall()
    out = []
    for row in rows:
        manifest = json.loads(row['manifest'])
        enabled = bool(row['enabled']) and not row['revoked_at']
        out.append({
            'id': row['id'], 'name': row['name'], 'version': row['version'],
            'enabled': enabled, 'revoked': bool(row['revoked_at']),
            'trusted_by': row['trusted_by'], 'trusted_at': row['trusted_at'],
            # A disabled or revoked extension holds nothing. This is the guard a future loader
            # reads, so it is the guard the tests pin.
            'capabilities': list(manifest['capabilities']) if enabled else [],
            'requested': list(manifest['capabilities']),
        })
    return out


def granted(store, ext_id: str) -> set[str]:
    """The capabilities actually in force right now — empty for anything not enabled."""
    for record in installed(store):
        if record['id'] == ext_id:
            return set(record['capabilities'])
    raise ExtensionError('no such extension: %s' % ext_id)


def may(store, ext_id: str, capability: str) -> bool:
    """The one question a caller asks: may this extension do that, now?"""
    if capability in RESERVED:
        return False
    if capability not in CAPABILITIES:
        raise ExtensionError('unknown capability %r' % capability)
    return capability in granted(store, ext_id)