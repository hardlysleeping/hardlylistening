# Following a feed (HL-232)

Following used to mean SoundCloud or Apple Music, so a label, a self-hosted artist, a Bandcamp
page with a feed, or a podcast could not be tracked at all. Now any **RSS or Atom feed** can be
followed: paste the feed URL into the same follow box and it behaves like an artist follow — it
produces release rows in *Releases*, checks on the normal interval, and downloads nothing until
somebody presses Download.

## What counts as a feed

The provider only claims a URL that says it is one:

| URL shape | claimed |
| --- | --- |
| `…/feed.xml`, `…/show.rss`, `…/latest.atom` | yes — by extension |
| `…/feed`, `…/feeds/`, `…/rss`, `…/atom` | yes — by the last path segment |
| `…?feed=rss`, `…?feed=atom`, `…?format=rss` | yes — by query |
| `soundcloud.com/name`, `music.apple.com/…/artist/…` | no — the service providers keep them |
| any other page | no — the yt-dlp fallback keeps it |

This is deliberate: a feed is text somebody else controls, and the one thing worth never doing is
guessing that a page has one. A page is not parsed for links. If you have a site with a feed, use
the feed's own URL.

## What an entry becomes

| feed element | stored as |
| --- | --- |
| `<title>` (Atom), `<title>` (RSS item) | the release title, with any markup stripped |
| `<id>` / `<guid>`, or the entry's `<link>` when a feed has neither | the release id — the thing a later check compares |
| `<published>` / `<updated>` / `<pubDate>` | the release date, in UTC |
| `<media:thumbnail>`, `<itunes:image>`, `<enclosure type="image/…">` | artwork |
| `<link>` | the page a Download opens |
| `<summary>`, `<content>`, `<description>` | **nothing** |

The entry body is dropped before anything is stored. That is the point of the last row: a feed is
untrusted, and the field it could poison is the one the web UI renders. Titles are cut at 300
characters and have tags removed instead of being sanitised for display, so no stored field can
carry markup.

## Checks, and what "new" means

A follow keeps a seen-set, exactly like a SoundCloud follow. A brand-new follow seeds the current
entries (capped) and marks them seen; every later check reports only ids it has not looked at
before. A feed that omits guids still works — the link stands in for the id — so a second check
reporting the same entries reports nothing new.

Feeds are newest-first by convention. A feed that dates every entry is additionally sorted by
date, so a mixed or reversed feed cannot seed the list in the wrong order.

## When a feed is broken

A missing feed, a page that answers with HTML, an oversized body, or a transport failure is
`FeedUnavailable`, and the follow reports the sentence:

```
feed unavailable: https://… answered HTTP 404
```

A follow whose *check* fails keeps the failure on the follow row (`error`, last checked) rather
than raising into the sweep, so one dead feed cannot stop the others from being checked. Nothing
is ever silently interpreted as "no releases".

Responses are capped at 1 MB before parsing, and parsed with `xml.etree.ElementTree`, which
refuses entity expansion beyond the five XML predefined entities.

## What this is not

- **Not an OPML importer.** One URL at a time, through the follow box.
- **Not a podcast downloader.** A podcast channel's entries appear as rows and download when you
  press Download, like any other release. Retention for downloaded podcasts is its own policy
  (see `docs/`'s retention notes), not something a feed changes.
- **Not federation.** Following a remote node is `docs/federation.md`, still unbuilt.

## Where it lives

| piece | file |
| --- | --- |
| the provider (identify, parse, normalise, `FeedUnavailable`) | `providers/feed.py` |
| registry position (after SoundCloud/Apple, before the yt-dlp fallback) | `providers/base.py` |
| follows, seen-set, caps | `repo/subscriptions.py` |
| the tests and their two fixtures | `tests/test_feed_follow.py`, `tests/fixtures/feeds/` |