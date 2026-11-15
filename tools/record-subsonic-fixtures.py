"""Record one set of real third-party Subsonic responses (HL-231).

Not a test: a recorder. It is kept in the tree so a fixture set can be refreshed and so the
provenance of every file is reproducible — the card's rule is that no compatibility claim exists
without a recorded response, which means the recording itself has to be inspectable.

    python3 tools/record-subsonic-fixtures.py navidrome-0.64.2 https://demo.navidrome.org demo demo

Only `GET` requests are made: nothing is starred, scanned or written on the server being recorded.
"""
import hashlib
import json
import sys
import time
import urllib.parse
import urllib.request
from pathlib import Path

FIXTURES = Path(__file__).resolve().parents[1] / 'tests' / 'fixtures' / 'subsonic'
VERSION = '1.16.1'
CLIENT = 'hardlylistening-fidelity'

#: endpoint → parameters, in the order they are recorded. Ids come from an earlier call, so the
#: set is one album, one song and one playlist of whatever the server has, not a guess.
PLAN = [
    ('ping', 'ping', {}),
    ('getOpenSubsonicExtensions', 'getOpenSubsonicExtensions', {}),
    ('getAlbumList2', 'getAlbumList2', {'type': 'alphabeticalByName', 'size': 5}),
    ('search3', 'search3', {'query': 'love', 'songCount': 3, 'albumCount': 3, 'artistCount': 3}),
    # These two are the envelopes a strict client refuses to guess about.
    ('getArtist_missing_id', 'getArtist', {}),                     # → error 10
    ('getCoverArt_unknown', 'getCoverArt', {'id': 'nonexistent-cover-art'}),   # → error 70
]


def fetch(base, user, password, endpoint, params):
    query = {'u': user, 'p': password, 'v': VERSION, 'c': CLIENT, 'f': 'json', **params}
    url = f'{base.rstrip("/")}/rest/{endpoint}.view?' + urllib.parse.urlencode(query)
    # Some public instances refuse a request with no User-Agent at all (the demo answers 403
    # to `Python-urllib/3.15`), so the recorder names itself rather than borrowing a default.
    request = urllib.request.Request(url, headers={'User-Agent': CLIENT})
    with urllib.request.urlopen(request, timeout=30) as response:
        return url, response.read()


def main():
    if len(sys.argv) < 5:
        raise SystemExit(__doc__)
    name, base, user, password = sys.argv[1:5]
    folder = FIXTURES / name
    folder.mkdir(parents=True, exist_ok=True)
    recorded = {}

    def save(label, endpoint, params):
        url, body = fetch(base, user, password, endpoint, params)
        document = json.loads(body.decode('utf-8'))
        (folder / f'{label}.json').write_text(
            json.dumps(document, indent=2, ensure_ascii=False, sort_keys=True) + '\n',
            encoding='utf-8')
        recorded[label] = {'endpoint': endpoint, 'params': params,
                           'sha256': hashlib.sha256(body).hexdigest()}
        return document.get('subsonic-response', {})

    print('recording', name, 'from', base)
    for label, endpoint, params in PLAN:
        envelope = save(label, endpoint, params)
        print(f'  {label}: {envelope.get("status")}')

    # The objects named by the recorded listing, so the fixtures agree with each other.
    artists = save('getArtists', 'getArtists', {}).get('artists', {})
    artist_id = ''
    for index in artists.get('index', []):
        for artist in index.get('artist', []):
            artist_id = artist['id']
            break
        if artist_id:
            break
    assert artist_id, 'the server listed no artists'
    artist = save('getArtist', 'getArtist', {'id': artist_id}).get('artist', {})
    album_id = (artist.get('album') or [{}])[0].get('id')
    assert album_id, 'the artist has no albums'
    album = save('getAlbum', 'getAlbum', {'id': album_id}).get('album', {})
    song_id = (album.get('song') or [{}])[0].get('id')
    assert song_id, 'the album has no songs'
    save('getSong', 'getSong', {'id': song_id})
    save('getLyricsBySongId', 'getLyricsBySongId', {'id': song_id})
    playlists = save('getPlaylists', 'getPlaylists', {}).get('playlists', {}).get('playlist', [])
    if playlists:
        save('getPlaylist', 'getPlaylist', {'id': playlists[0]['id']})

    manifest = {
        'server': name,
        'base_url': base,
        'recorded': time.strftime('%Y-%m-%dT%H:%M:%SZ', time.gmtime()),
        'protocol_version': VERSION,
        'client': CLIENT,
        'server_version': json.loads((folder / 'ping.json').read_text())['subsonic-response']
                          .get('serverVersion'),
        'note': 'GET only. Recorded by tools/record-subsonic-fixtures.py.',
        'responses': recorded,
    }
    (folder / 'manifest.json').write_text(
        json.dumps(manifest, indent=2, sort_keys=True) + '\n', encoding='utf-8')
    print('wrote', len(recorded), 'responses to', folder)


if __name__ == '__main__':
    main()