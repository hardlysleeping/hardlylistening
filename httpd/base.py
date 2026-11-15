"""The request/response plumbing, and the one place a POST finds its action.

`_send` and `json` are the whole reply vocabulary: a status, a content type, a body, and the
security headers `end_headers` adds to every one of them. `json` also applies the member's
field rule — a non-admin never receives `logs`, `settings`, an error message or an absolute
path — so redaction cannot be forgotten by an adapter that forgot to think about it.

`do_POST` is dispatch, not policy: it drains the body once (rfile reads once), then hands the
path to the first matching route in `routes.py`. A refusal raised anywhere below becomes a
status here, which is why the action methods can raise `Denied` and `ValueError` freely.
"""

from __future__ import annotations

import json
import mimetypes
import re
from urllib.parse import urlparse

from .constants import APP_DIR, UI_OFF, WEB_DIR
from .runtime import boot, features, security
from .routes import ROUTES


class Plumbing:
    def log_message(self, fmt, *args):
        # A keep-alive timeout can log before parse_request set self.path.
        if not getattr(self, "path", ""):
            print(f"{self.address_string()} {fmt % args}", flush=True)
            return
        if self.path.startswith(('/party', '/api/party', '/auth/', '/api/auth/', '/api/members', '/rest/', '/api/listening/', '/s/')):
            return  # Never log OAuth callback codes.
        if self.path.startswith("/api/state"):
            return                       # 1.5 s polling would drown the log
        print(f"{self.address_string()} {fmt % args}", flush=True)

    def setup(self):
        super().setup()
        self.connection.settimeout(15)

    def end_headers(self):
        self.send_header('X-Robots-Tag', 'noindex, nofollow, noarchive')
        self.send_header('X-Content-Type-Options', 'nosniff')
        self.send_header('X-Frame-Options', 'DENY')
        self.send_header('Referrer-Policy', 'no-referrer')
        self.send_header('Permissions-Policy', 'camera=(), microphone=(), geolocation=()')
        self.send_header('Content-Security-Policy', "frame-ancestors 'none'; object-src 'none'; base-uri 'self'")
        if security().required:
            self.send_header('Strict-Transport-Security', 'max-age=31536000')
        super().end_headers()

    def _send(self, code: int, body: bytes, ctype: str, extra_headers=None) -> None:
        self.send_response(code)
        self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store")
        for name, value in (extra_headers or {}).items():
            self.send_header(name, str(value))
        self.end_headers()
        try:
            self.wfile.write(body)
        except BrokenPipeError:
            pass

    def json(self, data, code: int = 200, extra_headers=None) -> None:
        if getattr(self, 'member', None) and self.member['role'] != 'admin' and code < 400:
            def public(value):
                if isinstance(value, list):
                    return [public(v) for v in value]
                if isinstance(value, dict):
                    result = {k: ('available' if v else '') if k == 'file' else public(v)
                            for k, v in value.items() if k not in ('logs', 'settings', 'error_message', 'error', 'cooldown_reason', 'playlist_projection_error', 'yt_dlp', 'root')}
                    if 'settings' in value:
                        result['settings'] = {}
                    if 'logs' in value:
                        result['logs'] = []
                    return result
                return value
            data = public(data)
        self._send(code, json.dumps(data, default=str).encode(), "application/json", extra_headers=extra_headers)

    def body(self) -> dict:
        """Memoised: rfile.read() only answers once, so a second call would hang."""
        if getattr(self, "_body", None) is not None:
            return self._body
        length = int(self.headers.get("Content-Length") or 0)
        raw = self.rfile.read(length) if length else b""
        try:
            self._body = json.loads(raw.decode("utf-8", "replace") or "{}")
            if not isinstance(self._body, dict):
                self._body = {}
        except json.JSONDecodeError:
            self._body = {}
        return self._body

    def do_DELETE(self) -> None:
        path = urlparse(self.path).path
        if not self.boundary(path):
            return
        self.json({"error": "no such endpoint"}, 404)

    def do_POST(self) -> None:
        self._body = None  # Handler instances survive HTTP keep-alive requests.
        path = urlparse(self.path).path
        if not self.boundary(path):
            return
        boot()
        self.body()  # Drain once, including actions that do not inspect a payload.
        for _, pattern, action in ROUTES:
            match = re.match(pattern, path)
            if not match:
                continue
            args = [int(g) for g in match.groups() if g.isdigit()]
            try:
                getattr(self, "do_" + action)(*args)
            except ValueError as exc:
                self.json({"error": str(exc)}, 400)
            except RuntimeError as exc:
                self.json({"error": str(exc)}, 400)
            except Exception as exc:                          # noqa: BLE001
                self.json({"error": 'Request failed' if security().required else f"{type(exc).__name__}: {exc}"}, 500)
            return
        self.json({"error": "no such endpoint"}, 404)

    def web_ui_off(self) -> bool:
        """Answer 404 for the browser app when `--no-web-ui` was used, and say why.

        The switch covers `web/` — the pages, scripts, styles and PWA assets served from that
        directory. The API, `/rest/`, the health probe and rendered share links are not the web
        UI and keep answering.
        """
        if features().get("web_ui", True):
            return False
        self._send(404, UI_OFF, "text/plain; charset=utf-8")
        return True

    def theme_css(self) -> None:
        """The signed-in member's three colours, or the stock ones.

        Only the `--user-*` inputs are written, never the derived tokens: tokens.css keeps
        owning the mixing, and the same `:root:root` selector it uses means a sheet linked
        after it wins the cascade without an !important anywhere.
        """
        import preferences
        if self.web_ui_off():
            return
        colours = dict(preferences.DEFAULT_THEME)
        if security().required:
            store, _ = boot()
            try:
                member = security().peek(self, store)
            except Exception:
                member = None          # a broken session must not 500 the page's stylesheet
            if member:
                colours = preferences.theme(store, member['id'])
        css = (':root:root{--user-base:%s;--user-accent:%s;--user-trim:%s}\n'
               % (colours['base'], colours['accent'], colours['trim']))
        self._send(200, css.encode(), 'text/css; charset=utf-8')

    def static(self, name: str) -> None:
        if self.web_ui_off():
            return
        bases = (WEB_DIR, APP_DIR) if name in ('app.webmanifest', 'icon-192.png', 'icon-512.png') else (WEB_DIR,)
        for base in bases:
            target = (base / name).resolve()
            if base.resolve() not in target.parents:
                continue                       # no traversal
            if target.is_file():
                ctype = mimetypes.guess_type(target.name)[0] or "application/octet-stream"
                if target.suffix == ".webmanifest":
                    ctype = "application/manifest+json"
                self._send(200, target.read_bytes(), ctype)
                return
        self._send(404, b"not found", "text/plain")
