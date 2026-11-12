"""The Subsonic / OpenSubsonic surface, scoped to one device's member.

`listening.rest()` authenticates the device, builds that member's catalogue and hands the
endpoint here. Everything that maps onto real app data is implemented; the subsystems this
app has no equivalent for (chat, jukebox, sonic analysis, podcast downloads, user
administration) answer Subsonic error 30 with a plain message, never a silent lie.

Transcoding is answered as the OpenSubsonic extension #1 requires, and it is real: the
library keeps one canonical file per track, and a client that cannot play it as it is gets
the file encoded through ffmpeg to a format its own `transcodingProfiles` named (MP3 when the
client names none, which is the Subsonic baseline). `getTranscodeStream` serves those bytes
with Range support, from an encode cached by the source file's size and mtime. `hls` stays
unimplemented, because there is nothing to segment.
"""
from __future__ import annotations

import base64
import os
import random
import secrets
import subprocess
import time

from paths import CACHE_DIR
from security import Denied

#: Encodes produced for clients that cannot play the stored file. Bounded and pruned by
#: `_trim`; tests point this at a temporary directory.
CACHE = CACHE_DIR / 'transcode'

BLANK_PNG = base64.b64decode(
    b'iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAYAAAAfFcSJAAAADUlEQVR42mP8z8DwHwAFAAH/q842iQAAAABJRU5ErkJggg==')

MUSIC_TYPES = {'mp3': 'audio/mpeg', 'm4a': 'audio/mp4', 'aac': 'audio/aac', 'flac': 'audio/flac',
               'ogg': 'audio/ogg', 'opus': 'audio/ogg', 'wav': 'audio/wav', 'aiff': 'audio/aiff',
               'aif': 'audio/aiff', 'wma': 'audio/x-ms-wma', 'alac': 'audio/mp4', 'webm': 'audio/webm'}

NOT_IMPLEMENTED = {
    'jukeboxControl', 'hls',
    'downloadPodcastEpisode',
    'addChatMessage', 'getChatMessages',
    'getSonicSimilarTracks', 'findSonicPath', 'getVideos', 'getVideoInfo', 'getCaptions',
    'changePassword', 'createUser', 'updateUser', 'deleteUser', 'tokenInfo',
}


def schema(store):
    """Kept as the name the Subsonic entry point calls; the tables belong to the library."""
    from library import service
    service.schema(store)


def iso(seconds):
    value = float(seconds or 0)
    if value <= 0:
        return '1970-01-01T00:00:00.000Z'
    return time.strftime('%Y-%m-%dT%H:%M:%S.000Z', time.gmtime(value))


def genre_of(track):
    tags = track.get('genres')
    if isinstance(tags, list) and tags:
        return tags[0]
    return track.get('genre') or ''


def song(t, starred=(), ratings=None, bookmarks=None):
    path = t.get('file') or ''
    name = path.rsplit('/', 1)[-1]
    suffix = name.rsplit('.', 1)[-1].lower() if '.' in name else 'mp3'
    album_id = str(t.get('albumId') or '')
    track_id = int(t['id'])
    data = {
        'id': str(track_id), 'parent': album_id, 'isDir': False, 'title': t.get('title') or '',
        'album': t.get('album') or '', 'artist': t.get('artist_name') or '',
        'albumArtist': t.get('artist_name') or '',
        'artistId': t.get('artistId') or '', 'albumId': album_id,
        'coverArt': album_id, 'duration': int(t.get('duration') or 0),
        'track': int(t.get('track_number') or 0), 'discNumber': int(t.get('disc') or 0),
        'suffix': suffix, 'contentType': MUSIC_TYPES.get(suffix, 'audio/mpeg'),
        'size': int(t.get('size') or 0), 'created': iso(t.get('added')),
        'type': 'music', 'bpm': int(t.get('bpm') or 0), 'genre': genre_of(t),
    }
    if track_id in starred:
        data['starred'] = iso(t.get('added'))
    if ratings and track_id in ratings:
        data['userRating'] = ratings[track_id]
    if bookmarks and track_id in bookmarks:
        data['bookmarkPosition'] = int(bookmarks[track_id])
    return data


# ---- transcoding extension (OpenSubsonic #1) -------------------------------
# One entry per stored suffix: the container a client would name, and every codec the file
# may contain. `.m4a` is the only ambiguous one — aac is what provider downloads produce,
# alac is possible — and either is accepted, because refusing every m4a for an unprovable
# codec would be worse than the rare wrong guess.
STREAM_FORMATS = {
    'mp3': ('mp3', ('mp3',)),
    'm4a': ('mp4', ('aac', 'alac')),
    'mp4': ('mp4', ('aac', 'alac')),
    'aac': ('aac', ('aac',)),
    'flac': ('flac', ('flac',)),
    'ogg': ('ogg', ('vorbis', 'opus')),
    'opus': ('ogg', ('opus',)),
    'webm': ('webm', ('opus', 'vorbis')),
    'wav': ('wav', ('pcm', 'wav')),
    'aiff': ('aiff', ('pcm', 'aiff')),
    'aif': ('aiff', ('pcm', 'aiff')),
    'wma': ('asf', ('wmav2', 'wma')),
}

# Clients name the same container differently, especially for Opus: the file is an Ogg
# container but a player's direct-play list often says "opus", and an mp4 audio file is
# "m4a" to some. A refusal on the name alone would be wrong, so aliases match together.
CONTAINER_ALIASES = {
    'mp4': {'mp4', 'm4a', 'm4b'},
    'ogg': {'ogg', 'opus'},
    'asf': {'asf', 'wma'},
    'aiff': {'aiff', 'aif'},
}

#: What this server can actually encode, keyed by the codec a client names. MP3 is the
#: Subsonic baseline every client plays; Opus and AAC are here because modern clients ask for
#: them. `container` is what a ClientInfo profile calls it, `suffix` what the cache file gets.
TRANSCODE_TARGETS = {
    'mp3': {'suffix': 'mp3', 'container': 'mp3', 'codec': 'mp3', 'encoder': 'libmp3lame', 'bitrate': 192},
    'opus': {'suffix': 'ogg', 'container': 'ogg', 'codec': 'opus', 'encoder': 'libopus', 'bitrate': 160},
    'aac': {'suffix': 'm4a', 'container': 'mp4', 'codec': 'aac', 'encoder': 'aac', 'bitrate': 192},
}
DEFAULT_TRANSCODE = 'mp3'
#: A client's `format` on a plain stream is the file suffix; map the ones that differ.
_FORMAT_CODECS = {'mp3': 'mp3', 'ogg': 'opus', 'opus': 'opus', 'm4a': 'aac', 'mp4': 'aac', 'aac': 'aac'}
TRANSCODE_TIMEOUT = 90
TRANSCODE_CACHE_FILES = 400
TRANSCODE_CACHE_AGE = 7 * 24 * 3600

TRANSCODE_TTL = 30 * 60
_TRANSCODE_PARAMS = {}


def _int(value):
    try:
        return int(value or 0)
    except (TypeError, ValueError):
        return 0


def _capped(target, limit):
    """A target with its bitrate lowered to what the client allows, never raised."""
    result = dict(target)
    if limit and 0 < int(limit) < result['bitrate']:
        result['bitrate'] = int(limit)
    return result


def transcode_target(client, requested='', max_bitrate=0):
    """What this server would encode for a client, or None when it cannot honour it.

    `requested` is a `format` on a plain stream call; `max_bitrate` is its kbps. Otherwise the
    client's own `transcodingProfiles` are consulted in order. No list at all means an older
    client that expects the Subsonic default, MP3. A list that names only formats this server
    cannot encode is an honest refusal: handing back audio the client said it cannot play is
    worse than a stated reason.
    """
    client = client if isinstance(client, dict) else {}
    limit = _int(max_bitrate) or (_int(client.get('maxAudioBitrate')) // 1000)
    wanted = str(requested or '').strip().lower()
    if wanted and wanted not in ('raw', 'original'):
        codec = _FORMAT_CODECS.get(wanted, wanted)
        target = TRANSCODE_TARGETS.get(codec)
        return _capped(target, limit) if target else None
    profiles = [p for p in (client.get('transcodingProfiles') or []) if isinstance(p, dict)]
    if not profiles:
        return _capped(TRANSCODE_TARGETS[DEFAULT_TRANSCODE], limit)
    for profile in profiles:
        if str(profile.get('protocol') or 'http').lower() not in ('', 'http'):
            continue
        codec = str(profile.get('audioCodec') or '').lower()
        container = str(profile.get('container') or '').lower()
        target = TRANSCODE_TARGETS.get(codec)
        if target is None and container in ('mp4', 'm4a'):
            target = TRANSCODE_TARGETS['aac']           # a profile names the container only
        if target is not None:
            own = _int(profile.get('maxAudioBitrate')) // 1000
            return _capped(target, own or limit)
    return None


def should_encode(track, target, max_bitrate=0):
    """Whether the stored file has to be re-encoded to satisfy this target.

    Same suffix and no bitrate ceiling is the stored file verbatim; a client asking for a
    lower bitrate than the file carries still wants a re-encode.
    """
    path = track.get('file') or ''
    suffix = path.rsplit('.', 1)[-1].lower() if '.' in path else ''
    if suffix != target['suffix']:
        return True
    stored = stored_format(track)[3] // 1000
    return bool(_int(max_bitrate)) and stored > _int(max_bitrate)


def issue_transcode_params(uid, track_id, target=None):
    """An opaque token tying a decision to one member, one track and one target format.

    Kept in memory for half an hour, which the extension explicitly allows. A restart
    invalidates outstanding decisions; the client simply asks again. `target` is None for a
    direct-play decision, meaning the stream is the stored bytes.
    """
    now = time.time()
    if len(_TRANSCODE_PARAMS) > 1000:
        for token in [t for t, entry in _TRANSCODE_PARAMS.items() if entry[3] < now]:
            _TRANSCODE_PARAMS.pop(token, None)
    token = secrets.token_urlsafe(24)
    _TRANSCODE_PARAMS[token] = (uid, int(track_id), target, now + TRANSCODE_TTL)
    return token


def resolve_transcode_params(token, uid):
    """(track id, target) a token was issued for, or None when it is unknown, expired or
    another member's."""
    entry = _TRANSCODE_PARAMS.get(str(token or ''))
    if not entry:
        return None
    owner, track_id, target, expiry = entry
    if expiry < time.time() or owner != uid:
        return None
    return track_id, target


def _cache_folder():
    folder = CACHE
    folder.mkdir(parents=True, exist_ok=True)
    return folder


def _trim(folder):
    """Keep the cache bounded: stale encodes go first, then the oldest past the ceiling."""
    try:
        files = sorted(folder.glob('*'), key=lambda path: path.stat().st_mtime)
    except OSError:
        return
    cutoff = time.time() - TRANSCODE_CACHE_AGE
    for index, path in enumerate(files):
        try:
            stale = path.stat().st_mtime < cutoff
        except OSError:
            continue
        if stale or len(files) - index > TRANSCODE_CACHE_FILES:
            try:
                path.unlink()
            except OSError:
                pass


def encode(store, track_id, target):
    """The transcoded file for one track and one target, or None when ffmpeg could not.

    Cached by the source file's size and mtime, so seeking through an album does not
    re-encode the same song on every Range request, and a replaced file is not served from
    the old encode.
    """
    from library import playback
    source = playback.resolve(store, int(track_id))
    if source is None:
        return None
    try:
        stat = source.stat()
    except OSError:
        return None
    folder = _cache_folder()
    key = (f"{int(track_id)}-{int(stat.st_mtime)}-{stat.st_size}"
           f"-{target['codec']}-{target['bitrate']}k")
    output = folder / f"{key}.{target['suffix']}"
    if output.is_file() and output.stat().st_size > 0:
        return output
    temp = output.with_name(f"{output.stem}.part.{target['suffix']}")
    command = ['ffmpeg', '-v', 'error', '-y', '-i', str(source), '-map', '0:a:0', '-vn',
               '-c:a', target['encoder'], '-b:a', f"{target['bitrate']}k"]
    if target['codec'] == 'opus':
        command += ['-vbr', 'on']
    if target['suffix'] == 'm4a':
        command += ['-movflags', '+faststart']
    command.append(str(temp))
    try:
        done = subprocess.run(command, capture_output=True, timeout=TRANSCODE_TIMEOUT)
    except (OSError, subprocess.SubprocessError):
        temp.unlink(missing_ok=True)
        return None
    if done.returncode != 0:
        temp.unlink(missing_ok=True)
        return None
    try:
        if temp.stat().st_size == 0:
            temp.unlink(missing_ok=True)
            return None
        os.replace(temp, output)
    except OSError:
        temp.unlink(missing_ok=True)
        return None
    _trim(folder)
    return output


def transcode_decision(track, client, uid):
    playable, reasons = can_direct_play(track, client)
    decision = {'canDirectPlay': playable, 'canTranscode': False,
                'sourceStream': stream_details(track)}
    if reasons:
        decision['transcodeReason'] = reasons
    if playable:
        decision['transcodeParams'] = issue_transcode_params(uid, track['id'])
        return {'transcodeDecision': decision}
    target = transcode_target(client)
    if target is None:
        decision['errorReason'] = ('This server cannot encode a profile the client offers, '
                                   'and none of its direct-play profiles accept the stored file.')
        return {'transcodeDecision': decision}
    decision['canTranscode'] = True
    decision['transcodeParams'] = issue_transcode_params(uid, track['id'], target)
    return {'transcodeDecision': decision}


def stored_format(track):
    """(suffix, container, accepted codecs, average bitrate) for the file on disk.

    Channel count and sample rate are not probed: the catalogue stores no such columns and
    a probe per decision is not worth a subprocess, so they are left out rather than
    guessed.
    """
    path = track.get('file') or ''
    suffix = path.rsplit('.', 1)[-1].lower() if '.' in path else 'mp3'
    container, codecs = STREAM_FORMATS.get(suffix, (suffix, (suffix,)))
    size, seconds = _int(track.get('size')), float(track.get('duration') or 0)
    bitrate = int(round(size * 8 / seconds)) if size > 0 and seconds > 0 else 0
    return suffix, container, codecs, bitrate


def stream_details(track):
    _, container, codecs, bitrate = stored_format(track)
    details = {'protocol': 'http', 'container': container, 'codec': codecs[0]}
    if bitrate:
        details['audioBitrate'] = bitrate
    return details


def _limitations_met(limitations, bitrate):
    """A required limitation must be provable from the stored file; an optional one is a
    preference and is ignored. Only the average bitrate is known, so a required channel,
    sample-rate or bit-depth limitation cannot be confirmed and fails the profile."""
    for limit in limitations:
        if not isinstance(limit, dict) or not limit.get('required'):
            continue
        if limit.get('name') != 'audioBitrate' or not bitrate:
            return False
        values = limit.get('values') or []
        target = _int(values[0]) if isinstance(values, list) and values else 0
        if not target:
            return False
        comparison = str(limit.get('comparison') or '')
        if comparison == 'LessThanEqual' and bitrate > target:
            return False
        if comparison == 'GreaterThanEqual' and bitrate < target:
            return False
        if comparison == 'Equals' and bitrate != target:
            return False
        if comparison == 'NotEquals' and bitrate == target:
            return False
        if comparison not in ('LessThanEqual', 'GreaterThanEqual', 'Equals', 'NotEquals'):
            return False
    return True


def can_direct_play(track, client):
    """(playable, reasons) for one ClientInfo payload against the stored file.

    Reasons carry one entry per direct-play profile that was refused, as the spec asks.
    """
    if not isinstance(client, dict):
        return False, ['NoClientInfo']
    _, container, codecs, bitrate = stored_format(track)
    profiles = [p for p in (client.get('directPlayProfiles') or []) if isinstance(p, dict)]
    if not profiles:
        return False, ['NoDirectPlayProfile']
    max_bitrate = _int(client.get('maxAudioBitrate'))
    limits = []
    for profile in client.get('codecProfiles') or []:
        if isinstance(profile, dict) and str(profile.get('name') or '').lower() in codecs:
            limits.extend(profile.get('limitations') or [])
    reasons = []
    for index, profile in enumerate(profiles, 1):
        containers = {str(c).lower() for c in profile.get('containers') or []}
        accepted = {str(c).lower() for c in profile.get('audioCodecs') or []}
        protocols = {str(p).lower() for p in profile.get('protocols') or []}
        channels = _int(profile.get('maxAudioChannels'))
        names = {container.lower()} | CONTAINER_ALIASES.get(container.lower(), set())
        if containers and not (containers & names):
            reasons.append('Profile%dContainerNotSupported' % index)
        elif accepted and not (accepted & set(codecs)):
            reasons.append('Profile%dAudioCodecNotSupported' % index)
        elif protocols and 'http' not in protocols:
            reasons.append('Profile%dProtocolNotSupported' % index)
        elif channels and channels < 2:
            # A mono-only sink cannot be confirmed without probing the file.
            reasons.append('Profile%dAudioChannelsUnknown' % index)
        elif max_bitrate and bitrate > max_bitrate:
            reasons.append('Profile%dAudioBitrateTooHigh' % index)
        elif not _limitations_met(limits, bitrate):
            reasons.append('Profile%dCodecLimitationNotMet' % index)
        else:
            return True, []
    return False, reasons


def handle(endpoint, params, multi, ctx):
    """Return a payload dict; write and return None when the response is bytes or a redirect."""
    store, uid, handler = ctx['store'], ctx['uid'], ctx['handler']
    songs, albums, artists, tracks = ctx['songs'], ctx['albums'], ctx['artists'], ctx['tracks']
    page, find, playlist_of = ctx['page'], ctx['find'], ctx['playlist']
    identity = params.get('id', '')
    schema(store)
    import podcasts
    import radio
    radio.schema(store)
    podcasts.schema(store)

    def ids_of(key):
        return multi.get(key) or ([params[key]] if params.get(key) else [])

    # The catalogue questions belong to the library service, so this surface and the web
    # API cannot drift apart about what a member has starred or how often they played it.
    from library import service
    starred = service.starred(store, uid)
    ratings = service.ratings(store, uid)
    plays = service.plays(store, uid)
    album_added = service.album_added(store)

    # ---- core -------------------------------------------------------------
    if endpoint == 'ping':
        return {}
    if endpoint == 'getLicense':
        return {'license': {'valid': True}}
    if endpoint == 'getMusicFolders':
        return {'musicFolders': {'musicFolder': [{'id': '1', 'name': 'Saved music'}]}}
    if endpoint == 'getArtists':
        return {'artists': {'ignoredArticles': '', 'index': [{'name': 'Saved', 'artist': list(artists.values())}]}}
    if endpoint == 'getIndexes':
        return {'indexes': {'ignoredArticles': '', 'lastModified': int(time.time() * 1000),
                            'index': [{'name': 'Saved', 'artist': list(artists.values())}]}}
    if endpoint == 'getArtist':
        row = find(list(artists.values()))
        row['album'] = [a for a in albums.values() if a['artistId'] == identity]
        return {'artist': row}
    if endpoint == 'getAlbum':
        row = find(list(albums.values()))
        row['song'] = [s for s in songs if s['albumId'] == identity]
        return {'album': row}
    if endpoint == 'getSong':
        return {'song': find(songs)}
    if endpoint == 'getPlaylists':
        return {'playlists': {'playlist': [playlist_of(p, songs)[0] for p in ctx['playlists']]}}
    if endpoint == 'getPlaylist':
        row, entries = playlist_of(find(ctx['playlists']), songs)
        row['entry'] = entries
        return {'playlist': row}
    if endpoint in ('search', 'search2', 'search3'):
        term = params.get('query', '').casefold()
        key = {'search': 'searchResult', 'search2': 'searchResult2', 'search3': 'searchResult3'}[endpoint]
        return {key: {
            'song': page([s for s in songs if term in s['title'].casefold()], 'songCount', 'songOffset'),
            'album': page([a for a in albums.values() if term in a['name'].casefold()], 'albumCount', 'albumOffset'),
            'artist': page([a for a in artists.values() if term in a['name'].casefold()], 'artistCount', 'artistOffset')}}

    # ---- transcoding extension (OpenSubsonic #1) --------------------------
    if endpoint == 'getTranscodeDecision':
        if params.get('mediaType') != 'song':
            raise Denied(430, 'This server only decides on song media')
        track = next((dict(t) for t in tracks if str(t.get('id')) == params.get('mediaId', '')), None)
        if track is None:
            raise Denied(404, 'Not found')
        return transcode_decision(track, ctx.get('client'), uid)

    # ---- artwork ----------------------------------------------------------
    if endpoint == 'getCoverArt':
        art = ctx['art'].get(identity)
        if not art:
            track = next((t for t in tracks if str(t.get('id')) == identity), None)
            if track is not None:
                art = track.get('artwork')
            else:
                found = next((s for s in songs if s['id'] == identity), None)
                if found is not None:
                    art = ctx['art'].get(found['albumId'])
        if art:
            handler.send_response(302)
            handler.send_header('Location', art)
            handler.send_header('Cache-Control', 'public, max-age=86400')
            handler.send_header('Content-Length', '0')
            handler.end_headers()
            return None
        # An id that is not in this member's catalogue is a miss, not an empty image.
        known = (identity in albums or identity in artists
                 or any(s['id'] == identity for s in songs)
                 or any(str(t.get('id')) == identity for t in tracks))
        if not known:
            raise Denied(404, 'Not found')
        handler._send(200, BLANK_PNG, 'image/png')
        return None
    if endpoint == 'getAvatar':
        handler.send_response(302)
        handler.send_header('Location', '/icon-192.png')
        handler.send_header('Content-Length', '0')
        handler.end_headers()
        return None

    # ---- directories ------------------------------------------------------
    if endpoint == 'getMusicDirectory':
        if identity in ('', '1'):
            rows = [dict(a, isDir=True, title=a['name'], parent='1') for a in albums.values()]
            name = 'Saved music'
        elif identity in artists:
            rows = [dict(a, isDir=True, title=a['name'], parent=identity)
                    for a in albums.values() if a['artistId'] == identity]
            name = artists[identity]['name']
        elif identity in albums:
            rows = [dict(s, parent=identity) for s in songs if s['albumId'] == identity]
            name = albums[identity]['name']
        else:
            raise Denied(404, 'Not found')
        return {'directory': {'id': identity or '1', 'name': name, 'child': rows}}

    # ---- album lists ------------------------------------------------------
    if endpoint in ('getAlbumList', 'getAlbumList2'):
        sort = params.get('type', 'alphabeticalByName')
        rows = list(albums.values())
        wanted = params.get('genre', '').casefold()
        if wanted:
            keep = {s['albumId'] for s, t in zip(songs, tracks) if genre_of(t).casefold() == wanted}
            rows = [a for a in rows if a['id'] in keep]
        if sort == 'alphabeticalByName':
            rows.sort(key=lambda a: a['name'].casefold())
        elif sort == 'alphabeticalByArtist':
            rows.sort(key=lambda a: (a['artist'].casefold(), a['name'].casefold()))
        elif sort in ('newest', 'recent'):
            rows.sort(key=lambda a: album_added.get((a['artist'], a['name']), 0), reverse=True)
        elif sort == 'random':
            random.shuffle(rows)
        elif sort == 'frequent':
            def weight(album):
                return sum(plays.get(int(s['id']), (0, 0))[0] for s in songs if s['albumId'] == album['id'])
            rows.sort(key=weight, reverse=True)
        elif sort in ('highest', 'byYear'):
            rows.sort(key=lambda a: a['name'].casefold())
        elif sort == 'byGenre':
            if not wanted:
                raise Denied(400, 'genre is required for byGenre')
            rows.sort(key=lambda a: a['name'].casefold())
        else:
            raise Denied(400, 'Unsupported album list type')
        key = 'albumList2' if endpoint == 'getAlbumList2' else 'albumList'
        return {key: {'album': page(rows)}}

    # ---- genres -----------------------------------------------------------
    if endpoint in ('getGenres', 'getSongsByGenre'):
        counts = {}
        for track in tracks:
            tag = genre_of(track)
            if tag:
                counts[tag] = counts.get(tag, 0) + 1
        if endpoint == 'getGenres':
            return {'genres': {'genre': [{'songCount': n, 'albumCount': 0, 'value': tag}
                                         for tag, n in sorted(counts.items())]}}
        wanted = params.get('genre', '').casefold()
        rows = [s for s, t in zip(songs, tracks) if genre_of(t).casefold() == wanted]
        return {'songsByGenre': {'song': page(rows)}}
    if endpoint == 'getRandomSongs':
        pool = list(songs)
        random.shuffle(pool)
        try:
            size = max(1, min(int(params.get('size', 10)), 500))
        except ValueError:
            size = 10
        return {'randomSongs': {'song': pool[:size]}}

    # ---- stars, ratings, plays -------------------------------------------
    if endpoint in ('star', 'unstar'):
        handler.close_connection = True
        on = endpoint == 'star'
        for value in ids_of('id'):
            try:
                track_id = int(value)
            except ValueError:
                continue
            service.star(store, uid, track_id, on)
        for value in ids_of('albumId'):
            album = albums.get(value)
            if not album:
                continue
            service.like_album(store, uid, album.get('key') or value, on)
        return {}
    if endpoint in ('getStarred', 'getStarred2'):
        starred_songs = [s for s in songs if int(s['id']) in starred]
        album_ids = {s['albumId'] for s in starred_songs}
        artist_ids = {s['artistId'] for s in starred_songs}
        return {('getStarred2' if endpoint == 'getStarred2' else 'getStarred'): {
            'song': starred_songs,
            'album': [a for a in albums.values() if a['id'] in album_ids],
            'artist': [a for a in artists.values() if a['id'] in artist_ids]}}
    if endpoint == 'setRating':
        try:
            track_id = int(identity)
            rating = max(0, min(int(params.get('rating', 0)), 5))
        except ValueError:
            raise Denied(400, 'Invalid rating')
        service.rate(store, uid, track_id, rating)
        return {}
    if endpoint in ('scrobble', 'reportPlayback'):
        now = time.time()
        for value in ids_of('id'):
            try:
                track_id = int(value)
            except ValueError:
                continue
            service.record_play(store, uid, track_id, when=now)
        return {}
    if endpoint == 'getTopSongs':
        artist = params.get('artist', '').casefold()
        pool = [s for s in songs if s['artist'].casefold() == artist]
        pool.sort(key=lambda s: plays.get(int(s['id']), (0, 0))[0], reverse=True)
        return {'topSongs': {'song': page(pool)}}

    # ---- play queue -------------------------------------------------------
    if endpoint in ('savePlayQueue', 'savePlayQueueByIndex'):
        handler.close_connection = True
        queue = [int(v) for v in ids_of('id') if str(v).isdigit()]
        try:
            current = int(params.get('current') or (queue[0] if queue else 0) or 0)
            position = float(params.get('position') or 0) / 1000.0
        except ValueError:
            current, position = 0, 0.0
        import nowplaying
        nowplaying.put(store, uid, {'id': current or None, 'queue': queue, 'time': max(0.0, position)})
        return {}
    if endpoint in ('getPlayQueue', 'getPlayQueueByIndex'):
        import nowplaying
        state = nowplaying.get(store, uid) or {}
        entry = [s for track_id in state.get('queue') or [] for s in songs if s['id'] == str(track_id)]
        current = next((s for s in songs if s['id'] == str(state.get('id') or '')), None)
        return {'playQueue': {'current': current['id'] if current else '',
                              'position': int(float(state.get('time') or 0) * 1000),
                              'username': uid, 'changed': iso(state.get('updated')),
                              'entry': entry}}

    if endpoint == 'getNowPlaying':
        # The position comes from the member's own saved state (`listening_now`), which the
        # web player writes as it plays: one row per member, so the most recent writer wins.
        # This surface only reports that state — nothing plays on the server — so `minutesAgo`
        # is the age of the last write and is reported rather than hidden. The player fields
        # name the device this request authenticated as, since that is this app's Subsonic
        # user. A track the member no longer has in scope is not described, rather than
        # named with fields the scope forbids.
        state = ctx.get('now')() or {}
        current = next((s for s in songs if s['id'] == str(state.get('id') or '')), None)
        entry = []
        if current is not None:
            updated = float(state.get('updated') or 0)
            item = dict(current)
            item.update({'username': ctx.get('username') or uid,
                         'minutesAgo': max(0, int((time.time() - updated) // 60)) if updated else 0,
                         'playerId': ctx.get('username') or uid,
                         'playerName': ctx.get('device_label') or 'HardlyListening'})
            entry.append(item)
        return {'nowPlaying': {'entry': entry}}

    # ---- bookmarks (Phase 1) ----------------------------------------------
    if endpoint == 'getBookmarks':
        out = []
        rows = service.bookmark_rows(store, uid)
        by_id = {s['id']: s for s in songs}
        for row in rows:
            entry = by_id.get(str(row['track_id']))
            if entry is None:
                continue                      # track left this member's catalogue
            out.append({'entry': entry, 'position': int(row['position_ms']),
                        'username': ctx.get('username') or uid,
                        'comment': row['comment'] or '', 'created': iso(row['created']),
                        'changed': iso(row['updated'])})
        return {'bookmarks': {'bookmark': out}}
    if endpoint == 'createBookmark':
        entry = find(songs)
        try:
            position = int(params.get('position') or 0)
        except ValueError:
            raise Denied(400, 'position must be milliseconds')
        if position < 0:
            raise Denied(400, 'position must not be negative')
        comment = params.get('comment', '')
        if not isinstance(comment, str) or len(comment) > 500:
            raise Denied(400, 'comment may be at most 500 characters')
        service.save_bookmark(store, uid, int(entry['id']), position, comment.strip())
        return {}
    if endpoint == 'deleteBookmark':
        try:
            track_id = int(identity)
        except ValueError:
            raise Denied(400, 'Invalid bookmark id')
        service.remove_bookmark(store, uid, track_id)
        return {}

    # ---- public shares (Phase 2) ------------------------------------------
    if endpoint == 'createShare':
        import shares
        kind = (params.get('kind') or '').strip().lower()
        target = identity
        if kind not in ('track', 'album', 'playlist'):
            if target in albums:
                kind = 'album'
            elif any(s['id'] == target for s in songs):
                kind = 'track'
            elif any(str(p['id']) == target for p in ctx['playlists']):
                kind = 'playlist'
            else:
                raise Denied(404, 'Not found')
        if kind == 'track' and not any(s['id'] == target for s in songs):
            raise Denied(404, 'Not found')
        if kind == 'album' and target not in albums:
            raise Denied(404, 'Not found')
        if kind == 'playlist' and not any(str(p['id']) == target for p in ctx['playlists']):
            raise Denied(404, 'Not found')
        shares.create(store, {'id': uid, 'role': ctx.get('role', 'member')}, kind, target,
                      note=params.get('description') or params.get('note') or '',
                      expires=params.get('expires'), security=ctx.get('security'))
        return {}
    if endpoint == 'getShares':
        import shares
        with store.lock:
            rows = [dict(r) for r in store.conn.execute(
                'SELECT * FROM listening_shares WHERE member_id=? ORDER BY created DESC,id DESC',
                (uid,))]
        by_id = {s['id']: s for s in songs}
        out = []
        for row in rows:
            entries = [by_id[str(t['id'])] for t in shares.tracks_for(store, row)
                       if str(t['id']) in by_id]
            out.append({'id': str(row['id']), 'description': row['note'] or '',
                        'username': ctx.get('username') or uid,
                        'created': iso(row['created']), 'expires': iso(row['expires']),
                        'visitCount': int(row['hits']), 'entry': entries})
        return {'shares': {'share': out}}
    if endpoint in ('updateShare', 'deleteShare'):
        import shares
        try:
            share_id = int(identity)
        except ValueError:
            raise Denied(400, 'Invalid share id')
        member = {'id': uid, 'role': ctx.get('role', 'member')}
        if endpoint == 'deleteShare':
            shares.delete(store, member, share_id)
        else:
            shares.update(store, member, share_id,
                          note=params.get('description') if 'description' in params else None,
                          expires=params.get('expires'))
        return {}

    # ---- internet radio (Phase 3) -----------------------------------------
    if endpoint == 'getInternetRadioStations':
        return radio.list_stations(store, uid)
    if endpoint == 'createInternetRadioStation':
        radio.create(store, uid, params)
        return {}
    if endpoint == 'updateInternetRadioStation':
        radio.update(store, uid, identity, params)
        return {}
    if endpoint == 'deleteInternetRadioStation':
        radio.delete(store, uid, identity)
        return {}

    # ---- podcasts (Phase 4) -----------------------------------------------
    if endpoint == 'createPodcastChannel':
        podcasts.create_channel(store, uid, params.get('url') or identity,
                                seed=params.get('title') or '')
        return {}
    if endpoint == 'getPodcasts':
        return podcasts.get_podcasts(store, uid, params)
    if endpoint == 'getNewestPodcasts':
        return podcasts.newest(store, uid, params)
    if endpoint == 'getPodcastEpisode':
        return podcasts.get_episode(store, uid, identity)
    if endpoint == 'refreshPodcasts':
        podcasts.refresh(store, uid, params.get('id') or '')
        return {}
    if endpoint == 'deletePodcastChannel':
        podcasts.delete_channel(store, uid, identity)
        return {}
    if endpoint == 'deletePodcastEpisode':
        podcasts.delete_episode(store, uid, identity)
        return {}

    # ---- info, lyrics, plumbing ------------------------------------------
    if endpoint in ('getArtistInfo', 'getArtistInfo2', 'getAlbumInfo', 'getAlbumInfo2'):
        key = {'getArtistInfo': 'artistInfo', 'getArtistInfo2': 'artistInfo2',
               'getAlbumInfo': 'albumInfo', 'getAlbumInfo2': 'albumInfo2'}[endpoint]
        return {key: {'biography': '', 'musicBrainzId': '', 'smallImageUrl': '', 'mediumImageUrl': '',
                      'largeImageUrl': '', 'similarArtist': [], 'notes': ''}}
    if endpoint == 'getLyrics':
        title = params.get('title', '').casefold()
        artist = params.get('artist', '').casefold()
        track = next((s for s in songs if s['title'].casefold() == title
                      and (not artist or s['artist'].casefold() == artist)), None)
        if not track:
            return {'lyrics': {}}
        data = ctx['lyrics'](int(track['id'])) or {}
        return {'lyrics': {'artist': track['artist'], 'title': track['title'],
                           'value': data.get('text') or ''}}
    if endpoint == 'getLyricsBySongId':
        track = next((s for s in songs if s['id'] == identity), None)
        data = ctx['lyrics'](int(identity)) or {}
        lines = [{'value': entry.get('text') or '', 'start': int(entry.get('time') or 0)}
                 for entry in data.get('lines') or [] if isinstance(entry, dict)]
        if not lines and data.get('text'):
            lines = [{'value': line} for line in data['text'].splitlines() if line.strip()]
        return {'lyricsList': {'structuredLyrics': [{
            'displayArtist': track['artist'] if track else '',
            'displayTitle': track['title'] if track else '',
            'synced': bool(data.get('lines')), 'line': lines}]}}
    if endpoint in ('getSimilarSongs', 'getSimilarSongs2'):
        track = next((s for s in songs if s['id'] == identity), None)
        pool = [s for s in songs if track and s['artist'] == track['artist'] and s['id'] != identity]
        if not pool:
            pool = list(songs)
        random.shuffle(pool)
        key = 'getSimilarSongs2' if endpoint == 'getSimilarSongs2' else 'getSimilarSongs'
        return {key: {'song': page(pool)}}
    if endpoint == 'getOpenSubsonicExtensions':
        return {'openSubsonicExtensions': [{'name': 'formPost', 'versions': [1]},
                                           {'name': 'songLyrics', 'versions': [1]},
                                           {'name': 'transcoding', 'versions': [1]}]}
    if endpoint in ('getScanStatus', 'startScan'):
        return {'scanStatus': {'scanning': False, 'count': len(tracks), 'folderCount': 1,
                               'lastScan': iso(time.time())}}
    if endpoint in ('getUsers', 'getUser'):
        member = {'username': ctx.get('username') or uid, 'email': '', 'scrobblingEnabled': True, 'adminRole': False,
                  'settingsRole': False, 'downloadRole': True, 'uploadRole': False,
                  'playlistRole': True, 'coverArtRole': True, 'commentRole': False,
                  'podcastRole': False, 'streamRole': True, 'jukeboxRole': False,
                  'shareRole': False, 'videoConversionRole': False}
        return {'users': {'user': [member]}} if endpoint == 'getUsers' else {'user': member}

    if endpoint in NOT_IMPLEMENTED:
        raise Denied(430, 'This server does not implement %s' % endpoint)
    raise Denied(404, 'Unsupported endpoint')
