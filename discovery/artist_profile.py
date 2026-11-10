"""Read-only artist discovery projection. Opening a profile never checks a provider."""
from urllib.parse import urlsplit


def safe_url(value):
    try:
        parsed = urlsplit(value or "")
        return value if parsed.scheme in ("http", "https") and parsed.hostname and not parsed.username and not parsed.password else ""
    except ValueError:
        return ""


def profile(store, name):
    with store.lock:
        rows = [dict(r) for r in store.conn.execute(
            "SELECT provider, artist_id, name, url FROM artists WHERE lower(name)=lower(?) ORDER BY provider", (name,))]
        # A known provider identity must never join a different, same-named artist.
        identities = {(r['provider'], str(r['artist_id'])) for r in rows}
        if identities:
            watches = [dict(r) for r in store.conn.execute("SELECT * FROM watches")
                       if (r['provider'], str(r['artist_id'])) in identities]
        else:
            # Without a catalog identity the artist has no `artists` row at all
            # (most SoundCloud artists: the catalog row is the set's uploader,
            # not the track's artist), so a follow made from the artist link is
            # only reachable by name. Matching nothing would show "not followed"
            # for an artist that is followed.
            watches = [dict(r) for r in store.conn.execute(
                "SELECT * FROM watches WHERE lower(trim(artist_name))=lower(trim(?))",
                (name,))]
        items = []
        if watches:
            ids = [w['id'] for w in watches]
            marks = ','.join('?' for _ in ids)
            items = [dict(r) for r in store.conn.execute(
                f"SELECT id,title,artist,release_type,release_date,source_url,artwork,first_seen,decision "
                f"FROM feed_items WHERE watch_id IN ({marks}) ORDER BY first_seen DESC,id DESC LIMIT 30", ids)]
        for watch in watches:
            log = store.conn.execute(
                "SELECT ts,msg FROM logs WHERE ref=? AND level='error' ORDER BY id DESC LIMIT 1",
                (f"watch:{watch['id']}",)).fetchone()
            watch['error'] = log['msg'] if log and log['ts'] >= watch['last_checked'] else ''
            watch['artist_url'] = safe_url(watch['artist_url'])
        counts = store.conn.execute(
            "SELECT COUNT(*) n,SUM(CASE WHEN file<>'' THEN 1 ELSE 0 END) downloaded "
            "FROM tracks WHERE lower(artist_name)=lower(?)", (name,)).fetchone()
    providers = []
    for row in rows:
        artist_id = str(row['artist_id'])
        rss = (f"https://feeds.soundcloud.com/users/soundcloud:users:{artist_id}/sounds.rss"
               if row['provider'] == 'soundcloud' and artist_id.isascii() and artist_id.isdigit() else '')
        providers.append({'provider': row['provider'], 'artist_id': artist_id,
                          'url': safe_url(row['url']), 'rss': rss})
    for item in items:
        item['source_url'] = safe_url(item['source_url'])
        item['artwork'] = safe_url(item['artwork'])
    return {'name': name, 'providers': providers, 'subscribed': bool(watches),
            'watches': watches, 'feed': items, 'tracks': counts['n'] or 0,
            'downloaded': counts['downloaded'] or 0}
