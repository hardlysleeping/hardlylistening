# HardlyListening — the headless server, nothing else.
#
# The image is a wrapper, not a second implementation: the same `server.py`, the same `web/`, the
# same SQLite file. What changes is where the files live and who runs the process.
FROM python:3.13-slim

# ffmpeg for audio, and yt-dlp from pip rather than Debian: the archive's yt-dlp is a year behind
# the sites it has to parse, and a downloader that cannot read SoundCloud is the whole app.
RUN apt-get update \
 && apt-get install -y --no-install-recommends ffmpeg ca-certificates \
 && rm -rf /var/lib/apt/lists/* \
 && pip install --no-cache-dir --upgrade yt-dlp

WORKDIR /app
COPY . /app

RUN useradd --system --uid 10001 --home-dir /data --shell /usr/sbin/nologin hardlylistening \
 && mkdir -p /data /music \
 && chown 10001:10001 /data /music

USER 10001

# HOME is /data on purpose: yt-dlp and any provider that caches cookies writes under it, and a
# cache written to a container path vanishes with the container.
ENV HARDLYLISTENING_DATA_DIR=/data \
    HARDLYLISTENING_DB=/data/hardlylistening.db \
    HARDLYLISTENING_MUSIC_DIR=/music \
    HARDLYLISTENING_HOST=0.0.0.0 \
    HARDLYLISTENING_PORT=8470 \
    HOME=/data \
    PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1

EXPOSE 8470

# Health means the process answers, never that a provider or a peer does. `check` is deliberately
# not used here: it wants a writable music mount and reports tool state, which is an installer's
# question, not a liveness one.
HEALTHCHECK --interval=30s --timeout=5s --start-period=15s --retries=3 \
  CMD python3 -c "import urllib.request; urllib.request.urlopen('http://127.0.0.1:8470/api/health', timeout=3)"

ENTRYPOINT ["/app/bin/hardlylistening-server"]
CMD ["run"]