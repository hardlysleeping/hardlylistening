"""Podcast subscriptions (Phase 4 of the Subsonic surface).

Channels and episodes are member-scoped. Subscribing stores the feed URL; nothing is
fetched until `refreshPodcasts` is called, and that call does one short, byte-capped
fetch per channel at request time — there is no background loop. Feed URLs are never
logged, redirects are re-validated, and (unless HARDLYLISTENING_ALLOW_LOCAL_FEEDS=1 is
set for tests) non-public hosts are refused so a feed cannot be used to probe the LAN.

`downloadPodcastEpisode` is deliberately still unsupported (Subsonic error 30): the
storage cap and pruning policy that make downloads safe are a separate feature. The
episode's `streamUrl` is handed to clients, which stream it directly.
"""
from __future__ import annotations

import email.utils
import hashlib
import ipaddress
import os
import socket
import time
import urllib.request
import xml.etree.ElementTree as ET
from datetime import timezone
from urllib.parse import urlsplit

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
          created REAL NOT NULL,
          UNIQUE(channel_id, guid));
        CREATE INDEX IF NOT EXISTS listening_podcast_member
          ON listening_podcast_channels(member_id, id);
        CREATE INDEX IF NOT EXISTS listening_podcast_episode_channel
          ON listening_podcast_episodes(channel_id, publish_date);
        ''')
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


def create_channel(store, uid, raw_url, seed=''):
    url = clean_url(raw_url if isinstance(raw_url, str) else '')
    host = urlsplit(url).hostname or 'Podcast feed'
    title = _clip(seed, MAX_TITLE) or host
    with store.lock:
        store.conn.execute(
            'INSERT OR IGNORE INTO listening_podcast_channels'
            '(member_id,url,title,description,artwork,created,last_checked,error_message) '
            'VALUES(?,?,?,\'\',\'\',?,0,\'\')', (uid, url, title, time.time()))
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
    return {'id': str(row['id']), 'channelId': str(row['channel_id']),
            'title': row['title'], 'description': row['description'] or '',
            'publishDate': iso(row['publish_date']), 'duration': int(row['duration'] or 0),
            'streamUrl': row['stream_url'], 'size': int(row['size'] or 0), 'status': 'new'}


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
                 'errorMessage': row['error_message'] or ''}
        if include:
            entry['episode'] = _episodes_for(store, row['id'])
        channels.append(entry)
    return {'podcasts': {'channel': channels}}


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
    with store.lock:
        changed = store.conn.execute(
            'DELETE FROM listening_podcast_episodes WHERE id=? AND channel_id IN '
            '(SELECT id FROM listening_podcast_channels WHERE member_id=?)',
            (episode_id, uid)).rowcount
        store.conn.commit()
    if not changed:
        raise Denied(404, 'Not found')
