"""yt-dlp, wrapped. Nothing provider-specific lives here.

    YtDlpSettings   what the audio should look like on disk
    json(url)       metadata without downloading (--flat-playlist -J)
    download(...)   run one track, stream its output lines
    classify(text)  permanent / retryable / rate / unusable
    search(...)     ranked candidates for a text query
"""

from __future__ import annotations

import difflib
import json
import re
import shutil
import subprocess
import threading
import time
import unicodedata
from dataclasses import dataclass
from pathlib import Path

APP_DIR = Path(__file__).resolve().parent.parent

RATE_LIMIT_RE = re.compile(
    r"(429\s*Too Many Requests|HTTP Error 429|Too Many Requests|"
    r"Sign in to confirm|confirm you.re not a bot|"
    r"This request looks automated|rate.?limit|"
    r"Please wait a few minutes|try again later|"
    r"Temporarily blocked|403 Forbidden|HTTP Error 403)", re.I)

PERMANENT_RE = re.compile(
    r"(Video unavailable|This video is not available|"
    r"Unsupported URL|is not a valid URL|"
    r"Private video|has been removed|does not exist|"
    r"No space left on device|Sign in to view|"
    r"This playlist is empty|Unable to extract)", re.I)

# Exists but can never be downloaded: the answer is a different source, not a retry.
DRM_RE = re.compile(r"(DRM protected|protected by DRM|"
                    r"Only available for Premium|geo.?restricted|"
                    r"not available in your country)", re.I)

RETRY_AFTER_RE = re.compile(r"(?:retry after|retry-after:?)\s*(\d+)", re.I)

# yt-dlp prints this line for every progress tick; the prefix keeps it apart from
# the human-readable output we still keep for error classification.
PROGRESS_PREFIX = "HLPROG|"
PROGRESS_TEMPLATE = ("download:" + PROGRESS_PREFIX +
                     "%(progress._percent_str)s|%(progress.downloaded_bytes)s|"
                     "%(progress.speed)s|%(progress.eta)s")

STOP_TOKENS = {"a", "an", "the", "of", "to", "me", "my", "and", "or", "in",
               "on", "for", "with", "by", "is", "it"}
QUALIFIERS = {"remix", "remixed", "live", "cover", "sped", "spedup", "nightcore",
              "instrumental", "karaoke", "extended", "edit", "bootleg", "mashup",
              "acoustic", "demo", "slowed", "reverb", "8d", "loop", "remake",
              "tribute", "reprise", "version", "beats", "type", "flip", "vip"}


def bin_path() -> str:
    for cand in (shutil.which("yt-dlp"), str(Path.home() / ".local/bin/yt-dlp"),
                 "/usr/local/bin/yt-dlp", "/usr/bin/yt-dlp"):
        if cand and Path(cand).exists():
            return cand
    return "yt-dlp"


YTDLP = bin_path()


@dataclass
class YtDlpSettings:
    audio_format: str = "mp3"
    embed_thumb: bool = True
    archive: Path | None = None
    retries: int = 2


def norm(text: str) -> str:
    text = unicodedata.normalize("NFKD", text or "").lower()
    text = re.sub(r"\(.*?\)|\[.*?\]", " ", text)
    text = re.sub(r"\b(feat|ft|featuring|prod)\b\.?", " ", text)
    text = re.sub(r"[^a-z0-9 ]+", " ", text)
    return re.sub(r"\s+", " ", text).strip()


def similarity(a: str, b: str) -> float:
    return difflib.SequenceMatcher(None, norm(a), norm(b)).ratio()


def _tokens(text: str) -> set[str]:
    return {t for t in norm(text).split() if t and t not in STOP_TOKENS}


# Words that appear in brackets but do not name a *version*: they say where the
# audio came from, not which recording it is.
VERSION_NOISE = {"official", "video", "audio", "lyric", "lyrics", "visualiser",
                 "visualizer", "hq", "hd", "mv", "premiere", "full", "song",
                 "music", "out", "now", "feat", "ft", "featuring", "prod",
                 "produced", "by", "winner", "free", "download", "mp3", "new",
                 "mix", "mixes", "original", "radio", "clean", "explicit", "dirty",
                 "mono", "stereo", "album", "single", "track"}

SC_TRUST = 0.60
MIN_ACCEPT = 0.45

_BRACKETS = re.compile(r"[(\[{（【]([^)\]}）】]*)[)\]}）】]")


def _version_tokens(text: str) -> set[str]:
    """The words inside brackets — "Fredrick" in "Song (Fredrick Remix)".

    `norm()` deletes brackets, which is why every remix used to score the same as
    the requested one: the name that tells them apart was thrown away before the
    comparison ever happened. Full-width brackets are normalised first, because
    uploaders use them too.
    """
    flat = unicodedata.normalize("NFKC", text or "")
    words: set[str] = set()
    for group in _BRACKETS.findall(flat):
        words |= set(re.findall(r"[a-z0-9]+", group.lower()))
    return words - STOP_TOKENS - QUALIFIERS - VERSION_NOISE


def _raw_tokens(text: str) -> set[str]:
    return set(re.findall(r"[a-z0-9]+", (text or "").lower()))


def hit_score(query: str, hit: dict) -> float:
    """Token coverage, not just string similarity.

    SequenceMatcher alone punishes label uploads ("octobersveryown - Solar
    Eclipse") for a missing artist token. The qualifier test runs on the RAW
    title because norm() deletes "(Lost Kings Remix)" with the brackets, which
    would score a remix as the original.
    """
    q = _tokens(query)
    if not q:
        return 0.0
    artist, offered = hit.get("artist", ""), hit.get("title", "")
    h = _tokens(f"{artist} {offered}")
    title = _tokens(offered)
    recall = len(q & h) / len(q)
    precision = len(q & h) / max(1, len(h))
    # Coverage against title alone punished a correct uploader/title split: an
    # uploader who repeats the artist inside the title ("Artist - Song") scored a
    # full 0.2 while the properly tagged row scored 0.1. Take the better of the
    # title and artist+title readings instead.
    title_cover = max(len(q & title), len(q & h)) / len(q)
    seq = similarity(query, f"{artist} {offered}")
    score = 0.45 * recall + 0.25 * precision + 0.2 * title_cover + 0.1 * seq
    extra = (QUALIFIERS & _raw_tokens(f"{artist} {offered}")) \
        - (QUALIFIERS & _raw_tokens(query))
    if extra:
        score *= 0.45
    # Which version this is: "New Day (Fredrick Remix)" must beat "New Day
    # (Bumpÿ Remix)" for a request that names Fredrick.
    wanted, given = _version_tokens(query), _version_tokens(f"{artist} {offered}")
    if wanted:
        if not given:
            score *= 0.9                     # probably the plain/original edit
        elif not (wanted & given):
            score *= 0.7                     # a different named version
    return round(min(1.0, score), 3)


def json_info(url: str, timeout: int = 300, limit: int = 0, *, full: bool = False) -> dict:
    cmd = [YTDLP, "--no-warnings", "--ignore-errors"]
    if not full:
        cmd.append("--flat-playlist")
    if limit:
        cmd += ["--playlist-end", str(limit)]
    cmd += ["-J", url]
    r = subprocess.run(cmd, capture_output=True, text=True, timeout=timeout)
    if not r.stdout.strip():
        tail = (r.stderr or "").strip().splitlines()[-3:]
        raise RuntimeError("; ".join(tail) or "yt-dlp returned nothing")
    return json.loads(r.stdout)



# ``norm`` deliberately keeps everything, which is right for scoring but wrong for
# searching: "(feat. Poppy Baskcomb)" in the query hides the real upload. Measured
# on "Friction What I Need (feat. Poppy Baskcomb)", YouTube's own first result is
# the wanted video, but only when the feature credit is dropped from the query.
_FEAT = re.compile(r"\b(?:feat|ft|featuring|with|prod)\.?\s+[^)\]}】,]*", re.I)
_CREDIT_ONLY = re.compile(r"[(\[{（【]([^)\]}）】]*)[)\]}）】]")


def search_title(title: str) -> str:
    """The title as a search engine should hear it: no feature credits, no
    brackets that only name the guest."""
    text = unicodedata.normalize("NFKC", str(title or ""))
    out = []
    for piece in _CREDIT_ONLY.split(text):
        out.append(piece)
    text = " ".join(out)
    if _FEAT.search(text):
        head = _FEAT.split(text, maxsplit=1)[0]
        # Only drop the credit when something is left to search for.
        if len(head.split()) >= 2:
            text = head
    text = re.sub(r"\s+", " ", text).strip(" -–—,;:|")
    return text or str(title or "").strip()


def query_variants(track: dict) -> list[str]:
    """One query is not enough when a title is mistitled upstream.

    The measured case: YouTube names the wanted upload "Friction - What I Need",
    SoundCloud names the same recording "I Need To Feel", and neither query finds
    both. Searching artist-first and title-first costs one extra request and
    covers the naming difference instead of insisting on one of them.
    """
    title = search_title(track.get("title") or "")
    artist = (track.get("artist_name") or "").strip()
    seen, out = set(), []
    for query in (f"{artist} {title}".strip(), f"{title} {artist}".strip(),
                  title.strip()):
        key = query.lower()
        if query and key not in seen:
            seen.add(key)
            out.append(query)
    return out


def search_candidates(query: str, exclude: list[str] | None = None,
                      sc_trust: float = 0.6, min_accept: float = 0.45) -> list[dict]:
    """SoundCloud first, YouTube as the safety net.

    Once a candidate has been excluded (it turned out unusable) YouTube joins the
    pool even if the SoundCloud hit looked strong — what's left on SoundCloud at
    that point is usually a remix or a cover.
    """
    exclude = set(exclude or [])
    # Three results per engine was too shallow to contain the right upload: the
    # only cost of asking for ten is a slightly larger JSON, and the answer is
    # often below the third row.
    pool: list[dict] = []
    sc = _search("scsearch10", query)
    pool.extend(sc)
    best_sc = max((h["match"] for h in sc), default=0.0)
    if exclude or best_sc < sc_trust:
        pool.extend(_search("ytsearch10", query))
    ranked = sorted(pool, key=lambda h: -h["match"])
    return [h for h in ranked
            if h["url"] and h["url"] not in exclude and h["match"] >= min_accept]


def _search(spec: str, query: str) -> list[dict]:
    try:
        info = json_info(f"{spec}:{query}", timeout=180)
    except Exception:
        return []
    out = []
    for entry in (info.get("entries") or []):
        if not entry:
            continue
        hit = {
            "url": entry.get("webpage_url") or entry.get("url") or "",
            "title": entry.get("title") or "",
            "artist": entry.get("uploader") or entry.get("channel") or "",
            "duration": entry.get("duration") or 0,
            "source_id": str(entry.get("id") or ""),
            "engine": re.sub(r"\d+$", "", spec),
        }
        if hit["url"]:
            hit["match"] = hit_score(query, hit)
            out.append(hit)
    return out


def classify(text: str) -> str:
    if RATE_LIMIT_RE.search(text):
        return "rate"
    if DRM_RE.search(text):
        return "unusable"
    if PERMANENT_RE.search(text):
        return "permanent"
    return "retryable"


def retry_after(text: str) -> int | None:
    m = RETRY_AFTER_RE.search(text or "")
    return int(m.group(1)) if m else None


def error_line(text: str) -> str:
    lines = [l.strip() for l in (text or "").splitlines() if l.strip()]
    for line in reversed(lines):
        if re.search(r"(ERROR|error|WARNING)", line):
            return re.sub(r"^(\[?ERROR\]?\s*:?\s*)", "", line)[:240]
    return lines[-1][:240] if lines else ""


def build_command(url: str, outtmpl: str, settings: YtDlpSettings) -> list[str]:
    cmd = [YTDLP, "--no-warnings", "--newline", "--ignore-config",
           "--no-playlist", "--extract-audio",
           "--audio-format", settings.audio_format, "--audio-quality", "0",
           "--add-metadata", "--embed-metadata", "--no-mtime",
           "--retries", str(settings.retries), "--fragment-retries", "2",
           "--progress-template", PROGRESS_TEMPLATE,
           "-o", outtmpl]
    if settings.embed_thumb:
        cmd.append("--embed-thumbnail")
    if settings.archive:
        cmd += ["--download-archive", str(settings.archive)]
    cmd.append(url)
    return cmd


def parse_progress(line: str) -> dict | None:
    """One progress tick, or None for ordinary output."""
    if not line.startswith(PROGRESS_PREFIX):
        return None
    parts = line[len(PROGRESS_PREFIX):].split("|")
    if len(parts) < 4:
        return None

    def number(raw: str) -> float:
        raw = (raw or "").strip()
        if re.fullmatch(r"-?\d+(\.\d+)?", raw):
            return float(raw)
        # yt-dlp renders eta as HH:MM:SS; "00:12" is 12 seconds, not zero.
        clock = re.fullmatch(r"(?:(\d+):)?(\d{1,2}):(\d{2})", raw)
        if clock:
            hours, minutes, seconds = (int(g or 0) for g in clock.groups())
            return hours * 3600 + minutes * 60 + seconds
        return 0.0

    percent = number(re.sub(r"[^\d.]", "", parts[0] or ""))
    return {"percent": round(min(100.0, percent), 1),
            "bytes": int(number(parts[1])),
            "speed": number(parts[2]),
            "eta": number(parts[3])}


def run(cmd: list[str], on_line=None, on_progress=None, cancel=None,
        timeout: int = 1800) -> tuple[int, str]:
    """Run yt-dlp, streaming output, reporting progress and honouring `cancel`.

    A reader thread drains stdout (so a full pipe cannot stall the child) while
    this thread watches the process and the cancel flag — that is what makes
    "skip" interrupt a download in flight instead of waiting for it to finish.
    """
    proc = subprocess.Popen(cmd, stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
                            text=True, bufsize=1)
    tail: list[str] = []

    def pump() -> None:
        assert proc.stdout is not None
        for raw in proc.stdout:
            line = raw.rstrip()
            if not line:
                continue
            tail.append(line)
            del tail[:-40]
            event = parse_progress(line)
            if event is not None:
                if on_progress:
                    on_progress(event)
                continue
            if on_line:
                on_line(line)

    reader = threading.Thread(target=pump, name="ytdlp-out", daemon=True)
    reader.start()

    cancelled = False
    started = time.time()
    while proc.poll() is None:
        if cancel is not None and cancel.is_set():
            cancelled = True
            proc.kill()
            break
        if time.time() - started > timeout:
            proc.kill()
            tail.append("timed out")
            break
        time.sleep(0.15)
    try:
        proc.wait(timeout=30)
    except subprocess.TimeoutExpired:
        proc.kill()
    reader.join(timeout=10)
    rc = -15 if cancelled else proc.returncode
    return rc, "\n".join(tail)


def discard_output(outtmpl: str, stop: Path | None = None) -> int:
    """Remove everything a killed download left behind for one track.

    yt-dlp writes `<stem>.m4a.part`, `<stem>.m4a.ytdl` and the thumbnail
    separately, so deleting only the final file leaves junk in the library.
    """
    folder = Path(outtmpl).parent
    stem = Path(outtmpl).name.split(".%(ext)s")[0]
    removed = 0
    if folder.exists():
        for leftover in folder.glob(stem + ".*"):
            try:
                leftover.unlink()
                removed += 1
            except OSError:
                pass
        # an emptied Artist/Album folder is clutter; walk up while it is empty,
        # but never remove the library root itself (or any ancestor of it)
        parent = folder
        stop_at = Path(stop) if stop else None
        while parent.is_dir():
            if stop_at is not None and parent == stop_at:
                break
            try:
                if any(parent.iterdir()):
                    break
                parent.rmdir()
            except OSError:
                break
            parent = parent.parent
    return removed


def verify_audio(path: Path | None, min_seconds: float = 1.0,
                 min_bytes: int = 16 * 1024) -> tuple[bool, str]:
    """Is this actually a playable track? yt-dlp can exit 0 having written junk."""
    if path is None or not Path(path).is_file():
        return False, "yt-dlp wrote no file"
    size = Path(path).stat().st_size
    if size < min_bytes:
        return False, f"file is {size} bytes — not a track"
    try:
        out = subprocess.run(
            ["ffprobe", "-v", "error", "-show_entries", "format=duration",
             "-of", "default=nw=1:nk=1", str(path)],
            capture_output=True, text=True, timeout=60)
        duration = float((out.stdout or "0").strip() or 0)
    except Exception:
        return False, "could not read the downloaded file"
    if duration < min_seconds:
        return False, f"downloaded file is {duration:.1f}s long — not a track"
    return True, ""


def find_output(outtmpl: str, audio_format: str) -> Path | None:
    """The file yt-dlp just wrote, whatever extension it settled on."""
    folder = Path(outtmpl).parent
    stem = Path(outtmpl).name.split(".%(ext)s")[0]
    if not folder.exists():
        return None
    wanted = {".mp3", ".m4a", ".opus", ".flac", ".wav", ".ogg", ".webm", f".{audio_format}"}
    for candidate in sorted(folder.glob(stem + ".*"), key=lambda p: -p.stat().st_mtime):
        if candidate.suffix.lower() in wanted:
            return candidate
    return None
