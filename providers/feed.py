"""Any RSS or Atom feed as an artist source, and nothing else (HL-232).

Following covered SoundCloud and Apple, so a label, a self-hosted artist or a podcast that
publishes a feed had no way in. A feed already *is* a release list in a documented format, so this
provider reads exactly that and stops: it never opens the linked page, never follows a link out of
an entry, and never keeps an entry body. The entry id (or its link when the feed omits one) is the
release id, the title is the title, and everything in `<description>`/`<summary>`/`<content>` is
dropped — a feed is text somebody else controls, and the one field it could poison is the one the
web UI renders.

That makes this a feed reader, not a scraper: `identify()` only claims a URL that names itself as a
feed, so an artist page stays with the providers that actually understand it.

Parsing is `xml.etree.ElementTree` over bytes read with a hard size cap. Entity expansion beyond the
five XML includes is refused by the parser, and the cap keeps a compressed bomb from becoming a
memory one. A feed that is missing, too large, or not XML raises `FeedUnavailable`, which the follow
path reports as a sentence rather than as an artist with no releases.
"""

from __future__ import annotations

import re
import urllib.error
import urllib.request
from datetime import datetime, timezone
from email.utils import parsedate_to_datetime
from urllib.parse import urlsplit, urlunsplit
from xml.etree import ElementTree

from providers.base import Provider, Release

#: One megabyte is far more than a release list needs and far less than a problem.
MAX_BYTES = 1024 * 1024
TIMEOUT = 20
UA = 'HardlyListening/1.0 (feed follow)'

#: A title is a label, not an article. Anything longer is cut rather than stored.
TITLE_MAX = 300

ATOM = '{http://www.w3.org/2005/Atom}'
MEDIA = '{http://search.yahoo.com/mrss/}'
ITUNES = '{http://www.itunes.com/dtds/podcast-1.0.dtd}'
CONTENT = '{http://purl.org/rss/1.0/modules/content/}'


class FeedUnavailable(ValueError):
    """The URL did not produce a feed. Reported, never guessed around."""


def fetch(url: str, timeout: int = TIMEOUT) -> bytes:
    """The one call that touches the network, so a test can patch it by name."""
    request = urllib.request.Request(url, headers={'User-Agent': UA,
                                                   'Accept': 'application/atom+xml, '
                                                             'application/rss+xml, '
                                                             'application/xml;q=0.9, text/xml;q=0.8'})
    try:
        with urllib.request.urlopen(request, timeout=timeout) as response:
            body = response.read(MAX_BYTES + 1)
    except urllib.error.HTTPError as exc:
        exc.close()
        raise FeedUnavailable(f'feed unavailable: {url} answered HTTP {exc.code}') from exc
    except OSError as exc:
        raise FeedUnavailable(f'feed unavailable: {exc}') from exc
    if len(body) > MAX_BYTES:
        raise FeedUnavailable(f'feed unavailable: {url} is larger than {MAX_BYTES} bytes')
    return body


def normalise(url: str) -> str:
    """A stable identity for a feed: no fragment, no trailing slash, lower-case host."""
    parts = urlsplit(url.strip())
    path = parts.path.rstrip('/') or '/'
    return urlunsplit((parts.scheme.lower(), parts.netloc.lower(), path, parts.query, ''))


def local(tag: str) -> str:
    return tag.rsplit('}', 1)[-1]


def children(node, name: str):
    return [child for child in node if local(child.tag) == name]


def first(node, *names: str):
    for child in node:
        if local(child.tag) in names:
            return child
    return None


def plain(value: str | None) -> str:
    """Text with any markup removed: this is what may reach a stored field."""
    if not value:
        return ''
    text = re.sub(r'<[^>]*>', ' ', value)
    text = re.sub(r'\s+', ' ', text).strip()
    return text[:TITLE_MAX]


def title_of(node) -> str:
    found = first(node, 'title')
    return plain(''.join(found.itertext()) if found is not None else '')


def link_of(node) -> str:
    """The entry's own page. Atom alternates first; RSS `<link>` is bare text."""
    for child in node:
        if local(child.tag) != 'link':
            continue
        rel = (child.get('rel') or 'alternate').lower()
        href = child.get('href')
        if href and rel == 'alternate':
            return href.strip()
    found = first(node, 'link')
    return (found.text or '').strip() if found is not None else ''


def identify_of(node) -> str:
    """The feed's own id, else a guid, else the link — whatever is stable across checks."""
    guid = first(node, 'guid', 'id')
    if guid is not None:
        value = ''.join(guid.itertext()).strip()
        if value:
            return value
    return link_of(node)


def date_of(node) -> str:
    for name in ('published', 'updated', 'pubDate', 'dc:date'):
        found = first(node, name)
        if found is None:
            continue
        value = ''.join(found.itertext()).strip()
        if not value:
            continue
        if 'T' in value:                                   # Atom / RFC 3339
            try:
                return datetime.fromisoformat(value.replace('Z', '+00:00')).astimezone(
                    timezone.utc).date().isoformat()
            except ValueError:
                return value[:10]
        try:                                               # RSS 2.0
            return parsedate_to_datetime(value).astimezone(timezone.utc).date().isoformat()
        except (TypeError, ValueError):
            continue
    return ''


def artwork_of(node) -> str:
    for child in node:
        if local(child.tag) == 'thumbnail' and child.get('url'):
            return child.get('url', '').strip()
        if local(child.tag) == 'image' and child.get('href'):
            return child.get('href', '').strip()
        if local(child.tag) == 'enclosure':
            kind = (child.get('type') or '').lower()
            if child.get('url') and kind.startswith('image/'):
                return child.get('url', '').strip()
    for child in node:
        if local(child.tag) == 'content' and child.get('url') and \
                (child.get('type') or '').lower().startswith('image/'):
            return child.get('url', '').strip()
    return ''


def parse(body: bytes) -> tuple[str, list]:
    """(feed title, entries newest first). Raises `FeedUnavailable` for non-XML."""
    try:
        root = ElementTree.fromstring(body)
    except ElementTree.ParseError as exc:
        raise FeedUnavailable(f'feed unavailable: the answer is not XML ({exc})') from exc
    if local(root.tag) == 'rss':
        channel = first(root, 'channel')
        entries = children(channel, 'item') if channel is not None else []
        title = title_of(channel) if channel is not None else ''
        return title, entries
    if local(root.tag) == 'feed':
        return title_of(root), children(root, 'entry')
    # RDF/RSS 1.0: items are siblings of the channel.
    items = children(root, 'item')
    if items:
        channel = first(root, 'channel')
        return (title_of(channel) if channel is not None else input_title(root)), items
    raise FeedUnavailable('feed unavailable: no RSS channel or Atom feed in the answer')


def input_title(root) -> str:
    return title_of(root)


class FeedProvider(Provider):
    name = 'feed'
    direct_audio = True

    def identify(self, url: str) -> bool:
        parts = urlsplit(url.strip())
        if parts.scheme.lower() not in ('http', 'https'):
            return False
        path = parts.path.lower().rstrip('/')
        if path.endswith(('.xml', '.rss', '.atom')):
            return True
        segments = [segment for segment in path.split('/') if segment]
        if segments and segments[-1] in ('feed', 'feeds', 'rss', 'atom'):
            return True
        query = parts.query.lower()
        return 'feed=rss' in query or 'feed=atom' in query or 'format=rss' in query

    def supports_follow(self) -> bool:
        return True

    def describe_artist(self, url: str) -> dict | None:
        title, _entries = parse(fetch(url))
        if not title:
            title = urlsplit(url).netloc or url
        return {'artist_id': normalise(url), 'artist_name': title[:TITLE_MAX],
                'artist_url': normalise(url)}

    def new_releases(self, artist_url: str, watermark: float,
                     limit: int = 60) -> list[Release]:
        title, entries = parse(fetch(artist_url))
        releases = []
        for entry in entries:
            release_title = title_of(entry)
            link = link_of(entry)
            release_id = identify_of(entry)
            if not (release_title and release_id):
                continue                       # an entry with no title or no identity is not a release
            releases.append(Release(
                provider=self.name, release_id=release_id, title=release_title,
                artist=title[:TITLE_MAX], artist_id=normalise(artist_url),
                artist_url=normalise(artist_url), release_type='track',
                release_date=date_of(entry), track_count=1,
                artwork=artwork_of(entry), source_url=link))
        # Feeds are newest-first by convention; a feed that carries dates is sorted by them so a
        # mixed one cannot seed a follow in the wrong order.
        dated = [release for release in releases if release.release_date]
        if len(dated) == len(releases):
            releases.sort(key=lambda release: release.release_date, reverse=True)
        return releases[:limit]