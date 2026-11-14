#!/usr/bin/env python3
"""Classify every downloaded track by what codec its origin actually serves.

Read-only: opens the SQLite file in mode=ro, writes nothing except the JSON report.
It answers one question before anything is re-downloaded — is this track's source
already Opus (so keeping it costs no quality), only MP3 (so "converting to Opus"
is a second lossy generation), or gone?

    python3 tools/opus_audit.py --limit 5        # smoke test
    python3 tools/opus_audit.py                  # full catalogue
"""
from __future__ import annotations

import argparse
import json
import sqlite3
import subprocess
import sys
import time
from pathlib import Path
from urllib.parse import urlparse

APP = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(APP))

from downloaders import ytdlp          # noqa: E402  (bin_path only)

PROBE_TIMEOUT = 60
# Formats yt-dlp reports for "audio only" streams can repeat the same codec at
# several bitrates; the loudest one is what a keep-the-source download would get.
AUDIO_HINTS = ('opus', 'mp3', 'aac', 'm4a', 'vorbis', 'flac', 'wav')


def candidates(db_path: Path) -> list[dict]:
    """Tracks that have a file on disk and a recorded origin. Never mutates."""
    conn = sqlite3.connect(f'file:{db_path}?mode=ro', uri=True)
    conn.row_factory = sqlite3.Row
    try:
        rows = conn.execute('''
            SELECT t.id, t.title, t.artist_name, t.duration, t.file,
                   t.source_url AS track_url,
                   j.id AS job_id, j.provider,
                   j.resolved_url, j.source_url AS job_url, j.pinned
            FROM tracks t
            JOIN download_jobs j ON j.track_id = t.id
            WHERE t.file <> '' AND j.status = 'completed'
            ORDER BY t.id
        ''').fetchall()
    finally:
        conn.close()
    out = []
    for row in rows:
        origin = (row['resolved_url'] or row['job_url'] or row['track_url'] or '').strip()
        out.append({
            'track_id': row['id'], 'title': row['title'], 'artist': row['artist_name'],
            'duration': row['duration'] or 0, 'file': row['file'],
            'provider': row['provider'], 'pinned': bool(row['pinned']),
            'origin': origin,
            'host': urlparse(origin).hostname or '',
        })
    return out


def classify(formats: list[dict]) -> dict:
    """Which class is the best audio this origin offers?"""
    audio = [f for f in formats if (f.get('acodec') or 'none') != 'none' and f.get('vcodec') in (None, 'none')]
    if not audio:
        audio = [f for f in formats if (f.get('acodec') or 'none') != 'none']
    if not audio:
        return {'class': 'dead', 'acodec': '', 'abr': 0}

    def abr(f):
        return float(f.get('abr') or f.get('tbr') or 0)

    opus = [f for f in audio if 'opus' in (f.get('acodec') or '').lower()]
    best_opus = max(opus, key=abr) if opus else None
    best_any = max(audio, key=abr)
    if best_opus:
        return {'class': 'native-opus', 'acodec': best_opus.get('acodec') or 'opus',
                'abr': round(abr(best_opus), 1)}
    return {'class': 'mp3-only', 'acodec': best_any.get('acodec') or '',
            'abr': round(abr(best_any), 1)}


def probe(url: str) -> dict:
    cmd = [ytdlp.YTDLP, '--no-warnings', '--no-playlist', '-J', url]
    try:
        done = subprocess.run(cmd, capture_output=True, text=True, timeout=PROBE_TIMEOUT)
    except subprocess.TimeoutExpired:
        return {'class': 'dead', 'error': 'probe timed out'}
    if done.returncode != 0:
        line = (done.stderr or '').strip().splitlines()
        return {'class': 'dead', 'error': (line[-1] if line else 'probe failed')[:200]}
    try:
        info = json.loads(done.stdout)
    except ValueError:
        return {'class': 'dead', 'error': 'unreadable yt-dlp output'}
    got = classify(info.get('formats') or [])
    got['title'] = (info.get('title') or '')[:120]
    got['duration'] = info.get('duration') or 0
    return got


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument('--db', default=str(APP / 'hardlylistening.db'))
    ap.add_argument('--out', default='')
    ap.add_argument('--limit', type=int, default=0, help='probe only the first N tracks')
    ap.add_argument('--pause', type=float, default=1.0, help='seconds between probes')
    args = ap.parse_args()

    tracks = candidates(Path(args.db))
    if args.limit:
        tracks = tracks[:args.limit]

    origins: dict[str, dict] = {}
    for index, track in enumerate(tracks, 1):
        origin = track['origin']
        if not origin:
            origins.setdefault('', {'class': 'no-origin'})
        elif origin not in origins:
            origins[origin] = probe(origin)
            time.sleep(args.pause)
        verdict = origins[origin]
        track['class'] = verdict.get('class', 'dead')
        track['acodec'] = verdict.get('acodec', '')
        track['abr'] = verdict.get('abr', 0)
        track['error'] = verdict.get('error', '')
        print(f"[{index}/{len(tracks)}] {track['class']:<12} {track['abr']:>6}k "
              f"{track['title'][:44]:<44} {origin[:60]}", flush=True)

    summary: dict[str, dict] = {}
    for track in tracks:
        bucket = summary.setdefault(track['class'], {'tracks': 0, 'seconds': 0, 'est_bytes': 0})
        bucket['tracks'] += 1
        bucket['seconds'] += float(track['duration'] or 0)
        bucket['est_bytes'] += float(track['duration'] or 0) * float(track['abr'] or 0) * 125  # abr kbit/s -> bytes/s

    stamp = time.strftime('%Y%m%d-%H%M%S')
    out = Path(args.out) if args.out else APP / 'backups' / f'opus-audit-{stamp}.json'
    out.write_text(json.dumps({'generated': stamp, 'db': args.db, 'tracks': tracks,
                               'origins': {k: v for k, v in origins.items() if k},
                               'summary': summary}, indent=1))

    print('\nclass          tracks   hours   est. size')
    for name in ('native-opus', 'mp3-only', 'dead', 'no-origin'):
        if name in summary:
            b = summary[name]
            print(f'{name:<14} {b["tracks"]:>6} {b["seconds"]/3600:>8.1f} '
                  f'{b["est_bytes"]/1e9:>8.2f} GB')
    print(f'\nreport: {out}')
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
