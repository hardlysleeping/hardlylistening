"""Publishing one finished track to a Funkwhale instance, and proving it arrived.

The plan kept this as an unknown on purpose: "import is likely asynchronous — 'accepted' must be
verified by reading the track back", and the adapter was not to be written until the API surface
was checked on their own GitLab rather than assumed. Checked 2026-11-14 against Funkwhale's
published OpenAPI (`https://docs.funkwhale.audio/swagger/schema.yml`), the surface is:

* `POST /api/v2/uploads/` — multipart, `audio_file` required, optional `library`, `channel`,
  `privacy_level`, `import_reference`; answers **201** with an upload object, not a track.
* `GET /api/v2/uploads/{uuid}/` — the upload object again, now carrying `import_status`
  (`draft`/`pending`/`finished`/`errored`/`skipped`) and, once finished, the `track` it produced.

So "accepted" is a `pending` upload with no track, and the only honest definition of success is a
`finished` status **with a track object that has an id**. That is what this module insists on: an
accepted upload that never finishes is a failure with a reason, not a success with a zero.

Two other rules, both about the credential:

* The token is read from `HARDLYLISTENING_FUNKWHALE_TOKEN` (or passed by the caller from the same
  place) and never appears in a URL, a log line or an error string — the query string is where a
  token leaks into an access log.
* The instance must be `https://`, except for loopback, which is where the tests' stub lives. That
  exception is stated rather than implied, because a plain-http instance puts the token on the wire.

Nothing here publishes automatically: this is the adapter a person's explicit action calls, and
the caller decides whether it is offered at all.
"""

from __future__ import annotations

import json
import os
import time
import urllib.error
import urllib.request
from pathlib import Path
from urllib.parse import urlsplit

TOKEN_ENV = 'HARDLYLISTENING_FUNKWHALE_TOKEN'
INSTANCE_ENV = 'HARDLYLISTENING_FUNKWHALE_INSTANCE'
LOOPBACK = {'127.0.0.1', '::1', 'localhost'}
#: How long a person waits before the upload is called stuck. Funkwhale transcodes after the
#: bytes land, so this is generous on purpose.
ATTEMPTS = 30
INTERVAL = 2.0
FINISHED, FAILED = 'finished', {'errored', 'skipped'}


class PublishError(RuntimeError):
    """A publish that did not end in a track, with the reason worth showing a person."""

    def __init__(self, message, status=None, body=b''):
        super().__init__(message)
        self.status = status
        self.body = body


def instance() -> str:
    return os.environ.get(INSTANCE_ENV, '').strip()


def token() -> str:
    return os.environ.get(TOKEN_ENV, '').strip()


def base(url: str) -> str:
    """The instance root, with the scheme checked before anything is sent."""
    text = str(url or '').strip().rstrip('/')
    parts = urlsplit(text)
    if parts.scheme not in ('http', 'https') or not parts.hostname:
        raise PublishError('a Funkwhale instance is an https URL: %r' % text)
    if parts.scheme == 'http' and parts.hostname not in LOOPBACK:
        raise PublishError('refusing %s: a Funkwhale token over plain http would travel '
                           'in the clear' % parts.hostname)
    if parts.query or parts.fragment:
        raise PublishError('the instance URL carries no query or fragment')
    return text


def _multipart(path: Path, fields: dict) -> tuple[bytes, str]:
    """One multipart body, built by hand — the server has no `requests` and never will."""
    boundary = '----hardlylistening' + os.urandom(12).hex()
    parts = []
    for name, value in fields.items():
        if value in (None, ''):
            continue
        parts.append(('--%s\r\nContent-Disposition: form-data; name="%s"\r\n\r\n%s\r\n'
                      % (boundary, name, value)).encode())
    ctype = 'application/octet-stream'
    suffix = path.suffix.lower()
    if suffix in {'.mp3', '.flac', '.ogg', '.opus', '.m4a', '.wav', '.aac'}:
        ctype = {'.mp3': 'audio/mpeg', '.flac': 'audio/flac', '.ogg': 'audio/ogg',
                 '.opus': 'audio/opus', '.m4a': 'audio/mp4', '.wav': 'audio/wav',
                 '.aac': 'audio/aac'}[suffix]
    parts.append(('--%s\r\nContent-Disposition: form-data; name="audio_file"; '
                  'filename="%s"\r\nContent-Type: %s\r\n\r\n'
                  % (boundary, path.name, ctype)).encode())
    parts.append(path.read_bytes())
    parts.append(('\r\n--%s--\r\n' % boundary).encode())
    return b''.join(parts), 'multipart/form-data; boundary=%s' % boundary


class Client:
    """The two calls, with the token in a header and the reason for every refusal."""

    def __init__(self, url: str, token_value: str, timeout: float = 60.0, opener=None):
        if not token_value:
            raise PublishError('%s is not set, so nothing can be published' % TOKEN_ENV)
        self.url = base(url)
        self._token = token_value
        self.timeout = timeout
        self._open = opener or urllib.request.urlopen
        self.requests: list[tuple[str, str]] = []     # for tests and audits: method, path

    # -- transport ----------------------------------------------------------
    def _send(self, method: str, path: str, body: bytes | None = None,
              content_type: str | None = None) -> tuple[int, dict]:
        target = self.url + path
        headers = {'Authorization': 'Bearer ' + self._token, 'Accept': 'application/json'}
        if content_type:
            headers['Content-Type'] = content_type
        request = urllib.request.Request(target, data=body, method=method, headers=headers)
        self.requests.append((method, path))
        try:
            answer = self._open(request, timeout=self.timeout)
        except urllib.error.HTTPError as error:
            payload = error.read()[:400]
            error.close()
            # The body is reported, the request URL is not: it is the one string that would
            # carry a token if a caller ever put one in a query string.
            raise PublishError('%s %s failed: %s' % (method, path, error.code),
                               status=error.code, body=payload) from None
        except (urllib.error.URLError, TimeoutError, OSError) as error:
            raise PublishError('%s %s could not reach the instance: %s'
                               % (method, path, error)) from None
        try:
            raw = answer.read()
        finally:
            answer.close()
        if not raw.strip():
            return 200, {}
        try:
            return 200, json.loads(raw.decode())
        except (UnicodeDecodeError, ValueError):
            raise PublishError('%s %s answered something that is not JSON' % (method, path)) from None

    # -- the two calls ------------------------------------------------------
    def upload(self, source, library: str = '', reference: str = '',
               privacy: str = '') -> dict:
        """Send the bytes. Answers the upload object; the track does not exist yet."""
        path = Path(source)
        if not path.is_file():
            raise PublishError('%s is not a file' % path.name)
        body, ctype = _multipart(path, {
            'library': library, 'import_reference': reference, 'privacy_level': privacy,
        })
        _, upload = self._send('POST', '/api/v2/uploads/', body, ctype)
        uuid = str(upload.get('uuid') or '')
        if not uuid:
            raise PublishError('the instance accepted the upload but returned no uuid')
        return upload

    def upload_status(self, uuid: str) -> dict:
        _, upload = self._send('GET', '/api/v2/uploads/%s/' % uuid)
        return upload


def publish(source, url: str | None = None, token_value: str | None = None,
            library: str = '', reference: str = '', privacy: str = '',
            attempts: int = ATTEMPTS, interval: float = INTERVAL,
            client: Client | None = None, sleep=time.sleep) -> dict:
    """Upload, then wait for the track — the only answer that counts as success.

    Returns `{'uuid', 'status', 'track_id', 'fid', 'url'}`. Raises `PublishError` when the
    instance refuses the bytes, when it reports `errored`/`skipped`, or when it simply never
    finishes: an accepted upload is a promise, not a delivery.
    """
    handle = client or Client(url if url is not None else instance(),
                              token_value if token_value is not None else token())
    path = Path(source)
    upload = handle.upload(path, library=library, reference=reference, privacy=privacy)
    uuid = str(upload['uuid'])
    status = str(upload.get('import_status') or 'pending')

    for attempt in range(max(1, int(attempts))):
        if status == FINISHED:
            break
        if status in FAILED:
            raise PublishError('the instance reported %r for %s and produced no track'
                               % (status, path.name))
        if attempt:
            sleep(interval)
        upload = handle.upload_status(uuid)
        status = str(upload.get('import_status') or status)

    if status != FINISHED:
        raise PublishError('the instance accepted %s (%s) but did not finish it after %d checks'
                           % (path.name, uuid, max(1, int(attempts))))
    track = upload.get('track') or {}
    track_id = track.get('id') if isinstance(track, dict) else None
    if not track_id:
        # Finished with no track is not a success: the bytes may be sitting in a library the
        # person cannot see, which is exactly the state "accepted" hides.
        raise PublishError('%s finished on the instance but no track came back for it' % path.name)
    return {'uuid': uuid, 'status': status, 'track_id': track_id,
            'fid': str(track.get('fid') or ''), 'title': str(track.get('title') or '')}