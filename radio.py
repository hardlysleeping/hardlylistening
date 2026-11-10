"""Internet radio stations (Phase 3 of the Subsonic surface).

Stations are member-scoped rows: a member only ever sees, changes or deletes their
own. Nothing is fetched or probed server-side, so a station URL is inert data; it is
still restricted to absolute http(s) URLs and length-capped before it is stored. URLs
are never written to logs, and URLs carrying userinfo are refused so no credential is
persisted in the first place.
"""
from __future__ import annotations

import time
from urllib.parse import urlsplit

from security import Denied

MAX_NAME = 200
MAX_URL = 2048


def schema(store):
    with store.lock:
        store.conn.executescript('''
        CREATE TABLE IF NOT EXISTS listening_radio_stations (
          id INTEGER PRIMARY KEY AUTOINCREMENT,
          member_id TEXT NOT NULL,
          name TEXT NOT NULL,
          stream_url TEXT NOT NULL,
          homepage_url TEXT NOT NULL DEFAULT '',
          created REAL NOT NULL);
        CREATE INDEX IF NOT EXISTS listening_radio_member
          ON listening_radio_stations(member_id, name);
        ''')
        store.conn.commit()


def clean_name(raw):
    if not isinstance(raw, str):
        raise Denied(400, 'name is required')
    name = raw.strip()
    if not 1 <= len(name) <= MAX_NAME:
        raise Denied(400, 'name must be 1-%d characters' % MAX_NAME)
    if any(ord(ch) < 32 or ord(ch) == 127 for ch in name):
        raise Denied(400, 'name contains control characters')
    return name


def clean_url(raw, field='streamUrl', optional=False):
    if raw is None:
        raw = ''
    if not isinstance(raw, str):
        raise Denied(400, '%s must be a URL' % field)
    text = raw.strip()
    if not text:
        if optional:
            return ''
        raise Denied(400, '%s is required' % field)
    if len(text) > MAX_URL:
        raise Denied(400, '%s may be at most %d characters' % (field, MAX_URL))
    if any(ord(ch) < 32 or ord(ch) == 127 or ch.isspace() for ch in text):
        raise Denied(400, '%s is not a valid URL' % field)
    parsed = urlsplit(text)
    if parsed.scheme.lower() not in ('http', 'https') or not parsed.hostname:
        raise Denied(400, '%s must be an absolute http(s) URL' % field)
    if parsed.username is not None or parsed.password is not None:
        raise Denied(400, '%s must not embed credentials' % field)
    try:
        parsed.port
    except ValueError:
        raise Denied(400, '%s has an invalid port' % field)
    return text


def list_stations(store, uid):
    with store.lock:
        rows = [dict(r) for r in store.conn.execute(
            'SELECT id,name,stream_url,homepage_url FROM listening_radio_stations '
            'WHERE member_id=? ORDER BY name COLLATE NOCASE,id', (uid,))]
    return {'internetRadioStations': {'internetRadioStation': [
        {'id': str(row['id']), 'name': row['name'], 'streamUrl': row['stream_url'],
         'homepageUrl': row['homepage_url'] or ''} for row in rows]}}


def create(store, uid, params):
    name = clean_name(params.get('name'))
    stream_url = clean_url(params.get('streamUrl'), 'streamUrl')
    homepage = clean_url(params.get('homepageUrl'), 'homepageUrl', optional=True)
    with store.lock:
        store.conn.execute(
            'INSERT INTO listening_radio_stations(member_id,name,stream_url,homepage_url,created) '
            'VALUES(?,?,?,?,?)', (uid, name, stream_url, homepage, time.time()))
        store.conn.commit()


def _station_id(identity):
    try:
        return int(identity)
    except (TypeError, ValueError):
        raise Denied(400, 'Invalid station id')


def update(store, uid, identity, params):
    station_id = _station_id(identity)
    name = clean_name(params.get('name'))
    stream_url = clean_url(params.get('streamUrl'), 'streamUrl')
    homepage = clean_url(params.get('homepageUrl'), 'homepageUrl', optional=True)
    with store.lock:
        changed = store.conn.execute(
            'UPDATE listening_radio_stations SET name=?,stream_url=?,homepage_url=? '
            'WHERE id=? AND member_id=?', (name, stream_url, homepage, station_id, uid)).rowcount
        store.conn.commit()
    if not changed:
        raise Denied(404, 'Not found')


def delete(store, uid, identity):
    station_id = _station_id(identity)
    with store.lock:
        changed = store.conn.execute(
            'DELETE FROM listening_radio_stations WHERE id=? AND member_id=?',
            (station_id, uid)).rowcount
        store.conn.commit()
    if not changed:
        raise Denied(404, 'Not found')
