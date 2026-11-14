"""The child half of the extension host: load one extension, answer its calls, say when it is done.

Started by `extension_host.run()` as

    python3 -I -B extension_shim.py <extension-directory> <entry-file>

with the extension's own directory as the working directory and an environment holding nothing but
`PATH`, `HOME` and a locale. `-I` keeps this application off `sys.path` and ignores `PYTHONPATH`, so
an extension cannot import `server`, `db`, `security` or anything else here even by accident; `-B`
leaves no `__pycache__` in a directory the server may own.

The protocol is one JSON object per line on stdin/stdout:

    child → host   {"call": 1, "method": "library.read", "params": {}}
    host  → child  {"result": 1, "value": …}   |   {"error": 1, "message": "…"}
    child → host   {"done": true, "output": …} |   {"fatal": "traceback"}

`print()` and every other write to stdout from the extension is redirected to stderr, so an
extension that logs cannot corrupt the channel. Nothing in this file is a security boundary: the
process runs as the same user as the server, with the same filesystem. The host mediates calls
because that is where the grant and the audit live, not because this process is contained.

The entry contract is one function, `run(extension)` or `main(extension)`; its return value must be
JSON-serialisable and is reported as the run's output. A module with neither is allowed — importing
it is the program — and reports no output.
"""
import importlib.util
import json
import os
import sys
import traceback

#: Must match `extension_host.MAX_OUTPUT`: a result the host will refuse anyway is better refused
#: here, where the message names the extension rather than the pipe.
MAX_OUTPUT = 64 * 1024

#: The real stdout, kept before `sys.stdout` is pointed at stderr. A duplicate descriptor, so an
#: extension that closes or replaces `sys.stdout` cannot take the channel with it.
_protocol = os.fdopen(os.dup(sys.stdout.fileno()), "w", buffering=1, encoding="utf-8")
_answers = os.fdopen(os.dup(sys.stdin.fileno()), "rb")
sys.stdout = sys.stderr


def _send(message: dict) -> None:
    _protocol.write(json.dumps(message, ensure_ascii=True) + "\n")
    _protocol.flush()


class Extension:
    """What an extension may call. Every method is a request to the host, which decides.

    The host answers with the capability check, the data and the audit; this object holds no
    authority of its own, and a method here that the host does not offer simply comes back as a
    refusal with a sentence.
    """

    def __init__(self):
        self._next = 0

    def call(self, method: str, **params):
        self._next += 1
        _send({"call": self._next, "method": str(method), "params": params})
        line = _answers.readline()
        if not line:
            raise RuntimeError("the host closed the connection")
        try:
            answer = json.loads(line.decode("utf-8"))
        except ValueError as error:
            raise RuntimeError("the host sent a line that is not JSON: %s" % error)
        if answer.get("error") is not None:
            raise RuntimeError(str(answer.get("message") or "the call was refused"))
        if "value" not in answer:
            raise RuntimeError("the host sent an answer with no value")
        return answer["value"]

    # -- the shapes an extension actually wants, so it does not build strings by hand ----------
    def library(self, offset: int = 0, limit: int = 200) -> dict:
        """The triggering member's own library, paged: `{"tracks": [...], "total": n}`."""
        return self.call("library.read", offset=int(offset), limit=int(limit))

    def social(self, limit: int = 50) -> dict:
        """The Activity feed as the server renders it: `{"items": [...]}`."""
        return self.call("social.read", limit=int(limit))

    def fetch(self, url: str) -> dict:
        """One HTTPS GET to a host the manifest declared: `{"status", "headers", "body"}`."""
        return self.call("network", url=str(url))


def _load(root: str, entry: str):
    path = os.path.join(root, entry)
    spec = importlib.util.spec_from_file_location("hl_extension_entry", path)
    if spec is None or spec.loader is None:
        raise RuntimeError("cannot load %s" % path)
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


def main(argv) -> int:
    if len(argv) != 3:
        _send({"fatal": "usage: extension_shim.py <extension-directory> <entry-file>"})
        return 2
    root, entry = argv[1], argv[2]
    # The extension may split itself over several files; that directory is the only path added.
    sys.path.insert(0, root)
    try:
        module = _load(root, entry)
        function = getattr(module, "run", None) or getattr(module, "main", None)
        output = function(Extension()) if callable(function) else None
        encoded = json.dumps(output, ensure_ascii=True)
        if len(encoded) > MAX_OUTPUT:
            raise ValueError("the output is %d bytes; the host accepts %d"
                             % (len(encoded), MAX_OUTPUT))
        _protocol.write('{"done": true, "output": %s}\n' % encoded)
        _protocol.flush()
    except BaseException:                                  # noqa: BLE001 — reported, not swallowed
        _send({"fatal": traceback.format_exc(limit=12)})
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv))