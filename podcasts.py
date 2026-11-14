"""Podcast subscriptions (Phase 4 of the Subsonic surface).

Channels and episodes are member-scoped. Subscribing stores the feed URL; nothing is
fetched until `refreshPodcasts` is called, and that call does one short, byte-capped
fetch per channel at request time — there is no background loop. Feed URLs are never
logged, redirects are re-validated, and (unless HARDLYLISTENING_ALLOW_LOCAL_FEEDS=1 is
set for tests) non-public hosts are refused so a feed cannot be used to probe the LAN.

`downloadPodcastEpisode` stores the episode on this server, under a **5 GB per-member
cap** (HL-209). The cap counts only files held here, per member, and `deletePodcastEpisode`
unlinks the file so deleting is what actually frees the space.

Retention (HL-234) is the other half, and it is **opt-in per channel**: a channel keeps the
newest N downloads and/or anything younger than N days, and a purge drops what neither rule
keeps. A channel with no policy keeps everything, so nothing is deleted on an upgrade and the
cap-only refusal above is still what happens by default. A silent delete of someone's file to
make space for another file is not a policy this server gets to invent — it is a policy the
owner of the channel asks for. Every removal is one row in `listening_podcast_prunes`, with
the bytes and the rule that took it, and `getPodcasts.storage.pruned` reports that same list
back. The sweep runs in the app's periodic worker (and before a download needs room), always
under the member lock a manual delete also takes, so two claims on one file are impossible.
"""
from __future__ import annotations

import email.utils
import hashlib
import ipaddress
import os
import socket
import threading
import time
import urllib.request
import xml.etree.ElementTree as ET
from datetime import datetime, timezone
from pathlib import Path
from urllib.parse import urlsplit

import paths
from security import Denied

MAX_URL = 2048
MAX_TITLE = 300
MAX_DESCRIPTION = 4000
MAX_GUID = 500
MAX_FEED_BYTES = 2 * 1024 * 1024
FEED_TIMEOUT = 5
MAX_CHANNELS_PER_REFRESH = 10
REFRESH_DEADLINE = 12
MAX_DURATION = 10 ** 7

#: How much podcast audio one member may keep on this server. Read once so the message a
#: member is refused with is the same number the accounting uses.
CAP_BYTES = int(os.getenv('HARDLYLISTENING_PODCAST_CAP_MB', '5120')) * 1024 * 1024
DOWNLOAD_CHUNK = 256 * 1024
DOWNLOAD_TIMEOUT = 30


def _env_policy(name):
    """A retention default from the environment, or 0 when it is absent or nonsense.

    Read at import so one typo cannot make a sweep behave differently halfway through a run.
    """
    try:
        return max(0, int(os.getenv(name, '0') or 0))
    except (TypeError, ValueError):
        return 0


#: What a **new** channel starts with (HL-234). 0 means "keep everything", which is also what
#: every channel created before the policy existed keeps — retention never applies backwards.
DEFAULT_KEEP_NEWEST = _env_policy('HARDLYLISTENING_PODCAST_KEEP_NEWEST')
DEFAULT_KEEP_DAYS = _env_policy('HARDLYLISTENING_PODCAST_KEEP_DAYS')

#: Bounds a member cannot type past: a thousand downloads and ten years are far beyond any
#: honest channel, and both keep a bad number from reading as "delete everything".
MAX_KEEP_NEWEST = 1000
MAX_KEEP_DAYS = 3650

AUDIO_TYPES = {'mp3': 'audio/mpeg', 'm4a': 'audio/mp4', 'mp4': 'audio/mp4',
               'aac': 'audio/aac', 'ogg': 'audio/ogg', 'oga': 'audio/ogg',
               'opus': 'audio/ogg', 'wav': 'audio/wav', 'flac': 'audio/flac'}


def iso(seconds):
    value = float(seconds or 0)
    if value <= 0:
        return '1970-01-01T00:00:00.000Z'
    return time.strftime('%Y-%m-%dT%H:%M:%S.000Z', time.gmtime(value))


def schema(store):
    with store.lock:
        store.conn.executescript('''
        CREATE TABLE IF NOT EXISTS listening_podcast_channels (
          id INTEGER PRIMARY KEY AUTOINCREMENT,
          member_id TEXT NOT NULL,
          url TEXT NOT NULL,
          title TEXT NOT NULL,
          description TEXT NOT NULL DEFAULT '',
          artwork TEXT NOT NULL DEFAULT '',
          created REAL NOT NULL,
          last_checked REAL NOT NULL DEFAULT 0,
          error_message TEXT NOT NULL DEFAULT '',
          UNIQUE(member_id, url));
        CREATE TABLE IF NOT EXISTS listening_podcast_episodes (
          id INTEGER PRIMARY KEY AUTOINCREMENT,
          channel_id INTEGER NOT NULL,
          guid TEXT NOT NULL,
          title TEXT NOT NULL,
          description TEXT NOT NULL DEFAULT '',
          publish_date REAL NOT NULL DEFAULT 0,
          duration INTEGER NOT NULL DEFAULT 0,
          stream_url TEXT NOT NULL,
          size INTEGER NOT NULL DEFAULT 0,
          file TEXT NOT NULL DEFAULT '',
          bytes INTEGER NOT NULL DEFAULT 0,
          downloaded REAL NOT NULL DEFAULT 0,
          created REAL NOT NULL,
          UNIQUE(channel_id, guid));
        CREATE TABLE IF NOT EXISTS listening_podcast_prunes (
          id INTEGER PRIMARY KEY AUTOINCREMENT,
          ts REAL NOT NULL,
          member_id TEXT NOT NULL,
          channel_id INTEGER NOT NULL,
          episode_id INTEGER NOT NULL,
          title TEXT NOT NULL,
          bytes INTEGER NOT NULL DEFAULT 0,
          reason TEXT NOT NULL DEFAULT ''
        );
        CREATE INDEX IF NOT EXISTS listening_podcast_prunes_member
          ON listening_podcast_prunes(member_id, id);
        CREATE INDEX IF NOT EXISTS listening_podcast_member
          ON listening_podcast_channels(member_id, id);
        CREATE INDEX IF NOT EXISTS listening_podcast_episode_channel
          ON listening_podcast_episodes(channel_id, publish_date);
        ''')
        # A store created before downloads existed has the table without the held-file
        # columns. This mirror is the only schema path podcasts.py owns, so it adds them.
        present = {row[1] for row in store.conn.execute(
            'PRAGMA table_info(listening_podcast_episodes)')}
        for name, ddl in (('file', "TEXT NOT NULL DEFAULT ''"),
                          ('bytes', 'INTEGER NOT NULL DEFAULT 0'),
                          ('downloaded', 'REAL NOT NULL DEFAULT 0')):
            if name not in present:
                store.conn.execute(
                    f'ALTER TABLE listening_podcast_episodes ADD COLUMN {name} {ddl}')
        # Retention is added as 0 = keep everything for existing channels, whatever the
        # environment says: a policy must not start deleting files someone already has.
        channels = {row[1] for row in store.conn.execute(
            'PRAGMA table_info(listening_podcast_channels)')}
        for name in ('keep_newest', 'keep_days'):
            if name not in channels:
                store.conn.execute(
                    f'ALTER TABLE listening_podcast_channels ADD COLUMN {name} '
                    'INTEGER NOT NULL DEFAULT 0')
        store.conn.commit()


def _allow_local():
    return os.getenv('HARDLYLISTENING_ALLOW_LOCAL_FEEDS', '').lower() in ('1', 'true', 'yes')


def _clip(value, limit):
    text = value if isinstance(value, str) else ''
    return text.strip()[:limit]


def clean_url(raw):
    """Syntactic validation only; safe to call without touching the network."""
    if not isinstance(raw, str):
        raise Denied(400, 'url is required')
    text = raw.strip()
    if not text:
        raise Denied(400, 'url is required')
    if len(text) > MAX_URL:
        raise Denied(400, 'url may be at most %d characters' % MAX_URL)
    if any(ord(ch) < 32 or ord(ch) == 127 or ch.isspace() for ch in text):
        raise Denied(400, 'url is not valid')
    parsed = urlsplit(text)
    if parsed.scheme.lower() not in ('http', 'https') or not parsed.hostname:
        raise Denied(400, 'url must be an absolute http(s) URL')
    if parsed.username is not None or parsed.password is not None:
        raise Denied(400, 'url must not embed credentials')
    try:
        parsed.port
    except ValueError:
        raise Denied(400, 'url has an invalid port')
    return text


def _public_host(url):
    """Refuse localhost/private/link-local targets unless explicitly allowed.

    A feed URL is fetched by the server, so this is the SSRF guard. DNS is resolved
    here and every answer must be globally routable; redirects run the same check.
    """
    if _allow_local():
        return
    parsed = urlsplit(url)
    host = (parsed.hostname or '').rstrip('.').lower()
    if not host or host == 'localhost' or host.endswith('.localhost') or host.endswith('.local'):
        raise Denied(400, 'url must point at a public host')
    try:
        literal = ipaddress.ip_address(host)
    except ValueError:
        literal = None
    if literal is not None and not literal.is_global:
        raise Denied(400, 'url must point at a public host')
    try:
        answers = socket.getaddrinfo(host, parsed.port or (443 if parsed.scheme == 'https' else 80),
                                     type=socket.SOCK_STREAM)
    except OSError:
        raise Denied(400, 'url host cannot be resolved')
    if not answers:
        raise Denied(400, 'url host cannot be resolved')
    for answer in answers:
        try:
            address = ipaddress.ip_address(answer[4][0])
        except ValueError:
            raise Denied(400, 'url host cannot be resolved')
        if not address.is_global:
            raise Denied(400, 'url must point at a public host')


class _Redirects(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        clean_url(newurl)
        _public_host(newurl)
        return super().redirect_request(req, fp, code, msg, headers, newurl)


def fetch(url):
    """One bounded fetch. Raises Denied with a message that never includes the URL."""
    clean_url(url)
    _public_host(url)
    request = urllib.request.Request(url, headers={
        'User-Agent': 'HardlyListening/1.0 (+podcast refresh)',
        'Accept': 'application/rss+xml, application/atom+xml, application/xml, text/xml;q=0.9, */*;q=0.1'})
    opener = urllib.request.build_opener(_Redirects())
    try:
        with opener.open(request, timeout=FEED_TIMEOUT) as response:
            raw = response.read(MAX_FEED_BYTES + 1)
    except Denied:
        raise
    except Exception:
        raise Denied(400, 'Feed could not be fetched')
    if len(raw) > MAX_FEED_BYTES:
        raise Denied(400, 'Feed is too large')
    lowered = raw.lower()
    if b'<!doctype' in lowered or b'<!entity' in lowered:
        raise Denied(400, 'Feed is not valid XML')
    try:
        return ET.fromstring(raw)
    except ET.ParseError:
        raise Denied(400, 'Feed is not valid XML')


def _local(tag):
    return str(tag).rsplit('}', 1)[-1].lower()


def _child(node, name):
    name = name.lower()
    for item in node:
        if _local(item.tag) == name:
            return item
    return None


def _children(node, name):
    return [item for item in node if _local(item.tag) == name]


def _text(node, name):
    found = _child(node, name)
    return (found.text or '').strip() if found is not None and found.text else ''

def _link(node):
    for item in _children(node, 'link'):
        rel = (item.get('rel') or '').strip().lower()
        if rel in ('alternate', ''):
            return (item.get('href') or '').strip()
    found = _child(node, 'link')
    return (found.get('href') or '').strip() if found is not None else ''


def parse_duration(raw):
    text = (raw or '').strip()
    if not text:
        return 0
    if text.isdigit():
        return min(int(text), MAX_DURATION)
    parts = text.split(':')
    if 1 < len(parts) <= 3 and all(part.isdigit() for part in parts):
        total = 0
        for part in parts:
            total = total * 60 + int(part)
        return min(total, MAX_DURATION)
    return 0


def parse_date(raw):
    text = (raw or '').strip()
    if not text:
        return 0.0
    try:
        parsed = email.utils.parsedate_to_datetime(text)
        if parsed.tzinfo is None:
            parsed = parsed.replace(tzinfo=timezone.utc)
        return parsed.timestamp()
    except (TypeError, ValueError, IndexError):
        pass
    try:
        parsed = __import__('datetime').datetime.fromisoformat(text.replace('Z', '+00:00'))
        if parsed.tzinfo is None:
            parsed = parsed.replace(tzinfo=timezone.utc)
        return parsed.timestamp()
    except ValueError:
        return 0.0


def _enclosure(node):
    for item in _children(node, 'enclosure'):
        url = (item.get('url') or '').strip()
        if url:
            return url, item.get('length') or ''
    for item in _children(node, 'link'):
        if (item.get('rel') or '').lower() == 'enclosure' and item.get('href'):
            return item.get('href').strip(), item.get('length') or ''
    return '', ''


def parse_feed(root):
    """Return ({title, description, artwork}, [{guid,title,description,publish,duration,url,size}])."""
    name = _local(root.tag)
    if name == 'rss':
        channel = _child(root, 'channel')
        if channel is None:
            raise Denied(400, 'Feed has no channel')
        items = _children(channel, 'item')
    elif name == 'feed':
        channel = root
        items = _children(root, 'entry')
    elif name == 'channel':
        channel = root
        items = _children(root, 'item')
    else:
        raise Denied(400, 'Feed is not RSS or Atom')

    image = _child(channel, 'image')
    artwork = _text(image, 'url') if image is not None else ''
    itunes_image = _child(channel, 'image')
    if itunes_image is not None and itunes_image.get('href'):
        artwork = artwork or itunes_image.get('href').strip()
    meta = {'title': _clip(_text(channel, 'title'), MAX_TITLE),
            'description': _clip(_text(channel, 'description') or _text(channel, 'subtitle'),
                                 MAX_DESCRIPTION),
            'artwork': artwork}

    episodes = []
    for item in items:
        title = _clip(_text(item, 'title'), MAX_TITLE)
        enclosure_url, enclosure_length = _enclosure(item)
        stream = (enclosure_url or _link(item)).strip()
        if not stream:
            continue
        try:
            clean_url(stream)
        except Denied:
            continue
        guid = _clip(_text(item, 'guid') or _text(item, 'id'), MAX_GUID)
        if not guid:
            guid = hashlib.sha256(stream.encode()).hexdigest()
        size = int(enclosure_length) if str(enclosure_length).isdigit() else 0
        description = _clip(_text(item, 'description') or _text(item, 'summary')
                            or _text(item, 'content'), MAX_DESCRIPTION)
        episodes.append({
            'guid': guid, 'title': title or 'Untitled episode', 'description': description,
            'publish': parse_date(_text(item, 'pubDate') or _text(item, 'published')
                                  or _text(item, 'updated')),
            'duration': parse_duration(_text(item, 'duration')),
            'url': stream, 'size': size})
    return meta, episodes


def _channel_rows(store, uid, identity=''):
    with store.lock:
        if identity:
            rows = [dict(r) for r in store.conn.execute(
                'SELECT * FROM listening_podcast_channels WHERE member_id=? AND id=?',
                (uid, identity))]
        else:
            rows = [dict(r) for r in store.conn.execute(
                'SELECT * FROM listening_podcast_channels WHERE member_id=? ORDER BY title COLLATE NOCASE,id',
                (uid,))]
    return rows


def _channel_id(identity):
    try:
        return int(identity)
    except (TypeError, ValueError):
        raise Denied(400, 'Invalid podcast channel id')


def retention_of(row):
    """A channel's policy as the API reports it, and as `_policy` reads it."""
    return {'keepNewest': int(row.get('keep_newest') or 0),
            'keepDays': int(row.get('keep_days') or 0)}


def _policy_value(raw, limit, name, default=0):
    """A retention number from a request or the environment: a count, never a guess."""
    if raw is None or raw == '':
        return int(default)
    if isinstance(raw, bool):
        raise Denied(400, '%s must be a whole number' % name)
    try:
        value = int(str(raw).strip())
    except (TypeError, ValueError):
        raise Denied(400, '%s must be a whole number' % name)
    if value < 0 or value > limit:
        raise Denied(400, '%s must be between 0 and %d' % (name, limit))
    return value


def set_retention(store, uid, identity, keep_newest=None, keep_days=None):
    """Change one channel's retention. Absent arguments are left alone, never reset."""
    channel_id = _channel_id(identity)
    with store.lock:
        row = store.conn.execute(
            'SELECT * FROM listening_podcast_channels WHERE id=? AND member_id=?',
            (channel_id, uid)).fetchone()
    if row is None:
        raise Denied(404, 'Not found')
    current = retention_of(dict(row))
    newest = (current['keepNewest'] if keep_newest is None
              else _policy_value(keep_newest, MAX_KEEP_NEWEST, 'keepNewest'))
    days = (current['keepDays'] if keep_days is None
            else _policy_value(keep_days, MAX_KEEP_DAYS, 'keepDays'))
    with store.lock:
        store.conn.execute(
            'UPDATE listening_podcast_channels SET keep_newest=?, keep_days=? '
            'WHERE id=? AND member_id=?', (newest, days, channel_id, uid))
        store.conn.commit()
    return {'keepNewest': newest, 'keepDays': days}


def create_channel(store, uid, raw_url, seed='', keep_newest=None, keep_days=None):
    url = clean_url(raw_url if isinstance(raw_url, str) else '')
    host = urlsplit(url).hostname or 'Podcast feed'
    title = _clip(seed, MAX_TITLE) or host
    # A channel created now starts with the server's policy; a channel created before the
    # policy existed keeps 0, so nothing is deleted on an upgrade.
    newest = _policy_value(keep_newest, MAX_KEEP_NEWEST, 'keepNewest', DEFAULT_KEEP_NEWEST)
    days = _policy_value(keep_days, MAX_KEEP_DAYS, 'keepDays', DEFAULT_KEEP_DAYS)
    with store.lock:
        store.conn.execute(
            'INSERT OR IGNORE INTO listening_podcast_channels'
            '(member_id,url,title,description,artwork,created,last_checked,error_message,'
            'keep_newest,keep_days) VALUES(?,?,?,\'\',\'\',?,0,\'\',?,?)',
            (uid, url, title, time.time(), newest, days))
        store.conn.commit()


def _store_refresh(store, channel_id, meta, episodes):
    now = time.time()
    with store.lock:
        store.conn.execute(
            "UPDATE listening_podcast_channels SET title=?,description=?,artwork=?,"
            "last_checked=?,error_message='' WHERE id=?",
            (meta['title'] or 'Podcast feed', meta['description'], meta['artwork'],
             now, channel_id))
        for entry in episodes:
            store.conn.execute(
                'INSERT INTO listening_podcast_episodes'
                '(channel_id,guid,title,description,publish_date,duration,stream_url,size,created) '
                'VALUES(?,?,?,?,?,?,?,?,?) ON CONFLICT(channel_id,guid) DO UPDATE SET '
                'title=excluded.title,description=excluded.description,'
                'publish_date=excluded.publish_date,duration=excluded.duration,'
                'stream_url=excluded.stream_url,size=excluded.size',
                (channel_id, entry['guid'], entry['title'], entry['description'],
                 entry['publish'], entry['duration'], entry['url'], entry['size'], now))
        store.conn.commit()


def refresh(store, uid, identity=''):
    rows = None
    if identity:
        rows = _channel_rows(store, uid, _channel_id(identity))
        if not rows:
            raise Denied(404, 'Not found')
    else:
        rows = _channel_rows(store, uid)[:MAX_CHANNELS_PER_REFRESH]
    deadline = time.monotonic() + REFRESH_DEADLINE
    for row in rows:
        if time.monotonic() >= deadline:
            break
        try:
            meta, episodes = parse_feed(fetch(row['url']))
        except Denied as exc:
            with store.lock:
                store.conn.execute(
                    'UPDATE listening_podcast_channels SET last_checked=?,error_message=? WHERE id=?',
                    (time.time(), exc.message[:300], row['id']))
                store.conn.commit()
            continue
        except Exception:
            with store.lock:
                store.conn.execute(
                    'UPDATE listening_podcast_channels SET last_checked=?,error_message=? WHERE id=?',
                    (time.time(), 'Feed could not be refreshed', row['id']))
                store.conn.commit()
            continue
        _store_refresh(store, row['id'], meta, episodes)


def episode_payload(row):
    held = row.get('file') or ''
    return {'id': str(row['id']), 'channelId': str(row['channel_id']),
            'title': row['title'], 'description': row['description'] or '',
            'publishDate': iso(row['publish_date']), 'duration': int(row['duration'] or 0),
            'streamUrl': row['stream_url'], 'size': int(row['size'] or 0),
            # What this server holds for this member, if anything. `status` says completed
            # exactly when a local copy exists, so a client can show it without another call.
            'bytes': int(row.get('bytes') or 0), 'downloaded': bool(held),
            'status': 'completed' if held else 'new'}


#: One download at a time per member. Two parallel requests would each read the same
#: headroom and both write, which turns a hard cap into a suggestion.
_MEMBER_GATE = threading.Lock()
_MEMBER_LOCKS = {}


def _member_lock(uid):
    with _MEMBER_GATE:
        return _MEMBER_LOCKS.setdefault(str(uid), threading.Lock())


def human_bytes(count):
    value = float(count or 0)
    for unit in ('B', 'KB', 'MB', 'GB', 'TB'):
        if value < 1024 or unit == 'TB':
            return ('%d %s' % (value, unit)) if unit == 'B' else ('%.1f %s' % (value, unit))
        value /= 1024.0
    return '%.1f TB' % value


def _member_dir(uid):
    """One directory per member, named from their id and nothing else reachable."""
    safe = ''.join(ch for ch in str(uid) if ch.isalnum() or ch in '-_') or 'member'
    return paths.PODCAST_DIR / safe


def _suffix(url):
    tail = urlsplit(url).path.rsplit('/', 1)[-1]
    ext = tail.rsplit('.', 1)[-1].lower() if '.' in tail else ''
    return ext if ext in AUDIO_TYPES else 'mp3'


def _remove_file(row):
    """Unlink the file this row names. Returns whether there was one to take.

    A `False` is not an error: it means somebody else (a manual delete, an earlier sweep)
    already had it, and an audit row claiming otherwise would be a lie.
    """
    held = row.get('file') or ''
    if not held:
        return False
    path = Path(held)
    try:
        if not path.is_file():
            return False
        path.unlink()
        return True
    except OSError:
        return False


def _downloaded(store, channel_id):
    """The episodes this channel actually holds, newest published first."""
    with store.lock:
        return [dict(row) for row in store.conn.execute(
            "SELECT * FROM listening_podcast_episodes WHERE channel_id=? AND file<>'' "
            "AND bytes>0 ORDER BY publish_date DESC, id DESC", (channel_id,))]


def _plan(records, keep_newest, keep_days, now, incoming=None):
    """What a policy drops, and why.

    Two independent rules, both meaning *keep*: the newest N downloads, and anything
    downloaded inside the window. The window is the harder of the two — an episode older
    than it goes even when the count would keep it — because a count alone never stops a
    folder from growing with a weekly show.

    `incoming` is the episode being downloaded right now. It counts as kept when the newest
    N is worked out, so a full budget frees exactly the room the new file needs.
    """
    ranked = sorted((records + ([incoming] if incoming else [])),
                    key=lambda row: (row.get('publish_date') or 0, row.get('id') or 0),
                    reverse=True)
    newest = {row['id'] for row in ranked[:keep_newest]} if keep_newest > 0 else None
    cutoff = now - keep_days * 86400 if keep_days > 0 else None
    dropped = []
    for row in records:
        if cutoff is not None and (row.get('downloaded') or 0) < cutoff:
            dropped.append({'row': row, 'reason': 'age'})
        elif newest is not None and row['id'] not in newest:
            dropped.append({'row': row, 'reason': 'newest'})
    return dropped


def _prune_member_locked(store, uid, incoming=None):
    """Apply every channel's policy for one member. The member lock must already be held."""
    now = time.time()
    removed, freed, policy = [], 0, False
    for channel in _channel_rows(store, uid):
        keep_newest = int(channel.get('keep_newest') or 0)
        keep_days = int(channel.get('keep_days') or 0)
        if keep_newest <= 0 and keep_days <= 0:
            continue
        policy = True
        records = _downloaded(store, channel['id'])
        if not records:
            continue
        fresh = incoming if incoming and incoming['channel_id'] == channel['id'] else None
        for drop in _plan(records, keep_newest, keep_days, now, fresh):
            row = drop['row']
            if not _remove_file(row):
                continue                    # somebody else already claimed it
            with store.lock:
                store.conn.execute(
                    "UPDATE listening_podcast_episodes SET file='', bytes=0, downloaded=0 "
                    "WHERE id=? AND file=?", (row['id'], row['file']))
                store.conn.execute(
                    'INSERT INTO listening_podcast_prunes'
                    '(ts,member_id,channel_id,episode_id,title,bytes,reason) '
                    'VALUES(?,?,?,?,?,?,?)',
                    (now, uid, channel['id'], row['id'], row['title'],
                     int(row['bytes'] or 0), drop['reason']))
                store.conn.commit()
            removed.append({'id': str(row['id']), 'title': row['title'],
                            'channel': channel['title'], 'bytes': int(row['bytes'] or 0),
                            'reason': drop['reason']})
            freed += int(row['bytes'] or 0)
    return {'removed': removed, 'bytes': freed, 'policy': policy}


def prune_member(store, uid):
    """Run the policy for one member now. A no-op for a member with no policy set."""
    with _member_lock(uid):
        return _prune_member_locked(store, uid)


def prune_all(store):
    """The periodic pass: every member who actually has a policy."""
    with store.lock:
        members = [row[0] for row in store.conn.execute(
            'SELECT DISTINCT member_id FROM listening_podcast_channels '
            'WHERE keep_newest>0 OR keep_days>0')]
    report = {'members': 0, 'removed': [], 'bytes': 0}
    for uid in members:
        try:
            result = prune_member(store, uid)
        except Exception as exc:                            # noqa: BLE001
            store.log('podcast retention failed for one member: %s' % exc, 'error')
            continue
        report['members'] += 1
        report['removed'].extend(result['removed'])
        report['bytes'] += result['bytes']
    return report


def prune_log(store, uid, limit=50):
    """What the policy has taken from this member, newest first. Never another member's."""
    try:
        count = max(1, min(int(limit), 500))
    except (TypeError, ValueError):
        count = 50
    with store.lock:
        return [{'id': str(row['episode_id']), 'title': row['title'],
                 'bytes': int(row['bytes'] or 0), 'reason': row['reason'] or '',
                 'removedAt': iso(row['ts'])}
                for row in store.conn.execute(
                    'SELECT * FROM listening_podcast_prunes WHERE member_id=? '
                    'ORDER BY id DESC LIMIT ?', (uid, count))]


def used_bytes(store, uid):
    """How much podcast audio this member holds, in bytes. The cap counts this."""
    with store.lock:
        row = store.conn.execute(
            'SELECT COALESCE(SUM(e.bytes),0) AS total FROM listening_podcast_episodes e '
            'JOIN listening_podcast_channels c ON c.id=e.channel_id WHERE c.member_id=?',
            (uid,)).fetchone()
    return int(row['total'] or 0)


def usage(store, uid):
    """The member's budget and the files inside it, so deleting is a choice, not a guess."""
    used = used_bytes(store, uid)
    with store.lock:
        rows = [dict(r) for r in store.conn.execute(
            'SELECT e.*, c.title AS channel_title FROM listening_podcast_episodes e '
            'JOIN listening_podcast_channels c ON c.id=e.channel_id '
            'WHERE c.member_id=? AND e.bytes>0 ORDER BY e.downloaded DESC,e.id DESC', (uid,))]
    return {'usedBytes': used, 'capBytes': CAP_BYTES,
            'used': human_bytes(used), 'cap': human_bytes(CAP_BYTES),
            'episodes': [{'id': str(r['id']), 'title': r['title'],
                          'channel': r['channel_title'], 'bytes': int(r['bytes']),
                          'downloaded': iso(r['downloaded'])} for r in rows]}


def _full_message(used, policy=False, freed=0):
    message = ('Podcast storage is full: %s of %s used. '
               'Delete a downloaded episode to make room.'
               % (human_bytes(used), human_bytes(CAP_BYTES)))
    if policy:
        # Say what the policy did, or the refusal reads as "nothing was even tried".
        message += (' The retention policy freed %s, which was not enough for this episode.'
                    % human_bytes(freed))
    return message


def _episode(store, uid, identity):
    try:
        episode_id = int(identity)
    except (TypeError, ValueError):
        raise Denied(400, 'Invalid episode id')
    with store.lock:
        row = store.conn.execute(
            'SELECT e.* FROM listening_podcast_episodes e JOIN listening_podcast_channels c '
            'ON c.id=e.channel_id WHERE e.id=? AND c.member_id=?',
            (episode_id, uid)).fetchone()
    if row is None:
        raise Denied(404, 'Not found')
    return dict(row)


def _save(url, target, limit):
    """Copy one episode's audio to `target`, refusing to cross `limit`. Returns bytes."""
    clean_url(url)
    _public_host(url)
    request = urllib.request.Request(url, headers={
        'User-Agent': 'HardlyListening/1.0 (+podcast download)',
        'Accept': 'audio/*,application/octet-stream;q=0.9,*/*;q=0.1'})
    opener = urllib.request.build_opener(_Redirects())
    target.parent.mkdir(parents=True, exist_ok=True)
    partial = target.with_name(target.name + '.part')
    written = 0
    try:
        with opener.open(request, timeout=DOWNLOAD_TIMEOUT) as response, open(partial, 'wb') as out:
            kind = (response.headers.get('Content-Type') or '').split(';')[0].strip().lower()
            if kind.startswith('text/') or kind in ('application/xml', 'application/rss+xml'):
                raise Denied(400, 'That link is not an audio file')
            while True:
                chunk = response.read(DOWNLOAD_CHUNK)
                if not chunk:
                    break
                written += len(chunk)
                if written > limit:
                    raise Denied(507, 'That episode is larger than the space left. '
                                      'Delete a downloaded episode to make room.')
                out.write(chunk)
    except Denied:
        partial.unlink(missing_ok=True)
        raise
    except Exception:
        partial.unlink(missing_ok=True)
        raise Denied(400, 'Episode could not be downloaded')
    if written == 0:
        partial.unlink(missing_ok=True)
        raise Denied(400, 'Episode was empty')
    partial.replace(target)
    return written


def download_episode(store, uid, identity):
    """Keep one episode for this member. Refuses at the cap; never prunes someone's file."""
    episode = _episode(store, uid, identity)
    with _member_lock(uid):
        held = episode.get('file') or ''
        if held and Path(held).is_file():
            return episode_payload(episode)      # already kept: the same answer, no refetch
        # The policy runs before the budget is read, counting this episode as kept, so a
        # member with a policy gets room made rather than a refusal. Without a policy this
        # is one SELECT per channel and changes nothing.
        pruned = _prune_member_locked(store, uid, incoming=episode)
        used = used_bytes(store, uid)
        headroom = CAP_BYTES - used
        if headroom <= 0:
            raise Denied(507, _full_message(used, pruned['policy'], pruned['bytes']))
        # The feed's `length` is not trusted here: a wrong one would refuse a file that
        # fits. `_save` stops at the headroom, which is the only number that must be right.
        target = _member_dir(uid) / ('%s.%s' % (episode['id'], _suffix(episode['stream_url'])))
        written = _save(episode['stream_url'], target, headroom)
        now = time.time()
        with store.lock:
            store.conn.execute(
                'UPDATE listening_podcast_episodes SET file=?, bytes=?, downloaded=? WHERE id=?',
                (str(target), written, now, episode['id']))
            store.conn.commit()
        episode.update({'file': str(target), 'bytes': written, 'downloaded': now})
        return episode_payload(episode)


def held_file(store, uid, identity):
    """The local copy for an episode, or a refusal: there is nothing to stream until kept."""
    episode = _episode(store, uid, identity)
    held = episode.get('file') or ''
    path = Path(held) if held else None
    if path is None or not path.is_file():
        raise Denied(404, 'Episode is not downloaded')
    return path, AUDIO_TYPES.get(path.suffix.lstrip('.').lower(), 'audio/mpeg')


def _episodes_for(store, channel_id):
    with store.lock:
        return [episode_payload(dict(r)) for r in store.conn.execute(
            'SELECT * FROM listening_podcast_episodes WHERE channel_id=? '
            'ORDER BY publish_date DESC,id DESC', (channel_id,))]


def get_podcasts(store, uid, params):
    identity = params.get('id', '')
    rows = _channel_rows(store, uid, _channel_id(identity) if identity else '')
    if identity and not rows:
        raise Denied(404, 'Not found')
    include = str(params.get('includeEpisodes', 'true')).lower() not in ('false', '0', 'no')
    channels = []
    for row in rows:
        entry = {'id': str(row['id']), 'url': row['url'], 'title': row['title'],
                 'description': row['description'] or '',
                 'status': 'error' if row['error_message'] else 'completed',
                 'errorMessage': row['error_message'] or '',
                 # Additive: what this channel keeps, so a client can show the policy.
                 'retention': retention_of(row)}
        if include:
            entry['episode'] = _episodes_for(store, row['id'])
        channels.append(entry)
    # The cap is per member, so the budget belongs on the member's own listing. Additive:
    # a client that does not know the key ignores it; one that does can show the bar.
    budget = usage(store, uid)
    # What the policy removed, newest first — the report the card asks for. Additive, and it
    # is the same list the audit table holds, never a recomputation.
    budget['pruned'] = prune_log(store, uid)
    return {'podcasts': {'channel': channels, 'storage': budget}}


def newest(store, uid, params):
    try:
        count = int(params.get('count', 20))
    except (TypeError, ValueError):
        raise Denied(400, 'count must be an integer')
    count = max(1, min(count, 100))
    with store.lock:
        rows = [dict(r) for r in store.conn.execute(
            'SELECT e.* FROM listening_podcast_episodes e JOIN listening_podcast_channels c '
            'ON c.id=e.channel_id WHERE c.member_id=? '
            'ORDER BY e.publish_date DESC,e.id DESC LIMIT ?', (uid, count))]
    return {'newestPodcasts': {'episode': [episode_payload(row) for row in rows]}}


def get_episode(store, uid, identity):
    try:
        episode_id = int(identity)
    except (TypeError, ValueError):
        raise Denied(400, 'Invalid episode id')
    with store.lock:
        row = store.conn.execute(
            'SELECT e.* FROM listening_podcast_episodes e JOIN listening_podcast_channels c '
            'ON c.id=e.channel_id WHERE e.id=? AND c.member_id=?', (episode_id, uid)).fetchone()
    if row is None:
        raise Denied(404, 'Not found')
    return {'podcastEpisode': episode_payload(dict(row))}


def delete_channel(store, uid, identity):
    channel_id = _channel_id(identity)
    with store.lock:
        channel = store.conn.execute(
            'SELECT id FROM listening_podcast_channels WHERE id=? AND member_id=?',
            (channel_id, uid)).fetchone()
        if channel is None:
            raise Denied(404, 'Not found')
        store.conn.execute('DELETE FROM listening_podcast_episodes WHERE channel_id=?', (channel_id,))
        store.conn.execute('DELETE FROM listening_podcast_channels WHERE id=? AND member_id=?',
                           (channel_id, uid))
        store.conn.commit()


def delete_episode(store, uid, identity):
    try:
        episode_id = int(identity)
    except (TypeError, ValueError):
        raise Denied(400, 'Invalid episode id')
    # Deleting the episode is the member's act; its file must go with it, or the bytes
    # would count against the cap forever with nothing left to point at them. The member
    # lock is the same one a download and a policy sweep take, so a manual delete and the
    # policy can never both claim the same file.
    with _member_lock(uid), store.lock:
        row = store.conn.execute(
            'SELECT e.* FROM listening_podcast_episodes e JOIN listening_podcast_channels c '
            'ON c.id=e.channel_id WHERE e.id=? AND c.member_id=?',
            (episode_id, uid)).fetchone()
        changed = store.conn.execute(
            'DELETE FROM listening_podcast_episodes WHERE id=? AND channel_id IN '
            '(SELECT id FROM listening_podcast_channels WHERE member_id=?)',
            (episode_id, uid)).rowcount
        store.conn.commit()
    if row is not None:
        _remove_file(dict(row))
    if not changed:
        raise Denied(404, 'Not found')
