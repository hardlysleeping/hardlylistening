#!/usr/bin/env python3
"""Stage the canonical launcher as a Tauri `externalBin` sidecar, byte for byte.

Tauri resolves `bundle.externalBin: ["bin/hardlylistening-server"]` to a file named
`bin/hardlylistening-server-<target-triple>` at build time, so a copy with that name has to
exist before `cargo tauri build` runs. This script makes that copy and nothing else:

    python3 desktop/tools/stage_sidecar.py                    # ask rustc for the triple
    python3 desktop/tools/stage_sidecar.py --triple aarch64-unknown-linux-gnu
    python3 desktop/tools/stage_sidecar.py --check            # verify a staged copy

The copy is only renamed: it is compared by SHA-256 before and after, and `--check` fails if the
staged file has drifted from `bin/hardlylistening-server`. Editing the staged copy is therefore
detectable, which is the point — a sidecar with its own logic would be a second runtime.

It runs nothing and needs no network. `desktop/` is not in the server's build or its packaging.
"""
from __future__ import annotations

import argparse
import hashlib
import shutil
import subprocess
import sys
from pathlib import Path

DESKTOP = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(DESKTOP))

from host import resolve  # noqa: E402  (path set above: the shell's own module, not the server's)


def digest(path: Path) -> str:
    hasher = hashlib.sha256()
    with path.open('rb') as handle:
        for block in iter(lambda: handle.read(65536), b''):
            hasher.update(block)
    return hasher.hexdigest()


def rustc_triple() -> str | None:
    """Ask the compiler. A guessed triple that disagrees with it yields a missing sidecar."""
    try:
        output = subprocess.run(['rustc', '-vV'], capture_output=True, text=True, check=True).stdout
    except (OSError, subprocess.CalledProcessError):
        return None
    for line in output.splitlines():
        if line.startswith('host: '):
            return line.split(':', 1)[1].strip() or None
    return None


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog='stage_sidecar.py', description=__doc__.splitlines()[0])
    parser.add_argument('--triple', help='Rust target triple; defaults to `rustc -vV`')
    parser.add_argument('--server', help='launcher to stage; defaults to HARDLYLISTENING_SERVER '
                                         'or <app>/bin/hardlylistening-server')
    parser.add_argument('--out', help='destination directory; defaults to src-tauri/bin')
    parser.add_argument('--check', action='store_true',
                        help='verify an existing staged copy instead of writing one')
    args = parser.parse_args(argv)

    source = Path(args.server) if args.server else resolve.launcher()
    if source.name != resolve.LAUNCHER_NAME:
        print(f'refusing to stage {source.name!r}: the sidecar is {resolve.LAUNCHER_NAME!r}',
              file=sys.stderr)
        return 2
    if not source.is_file():
        print(f'no launcher at {source}', file=sys.stderr)
        return 2

    triple = args.triple or rustc_triple() or resolve.target_triple()
    if not triple:
        print('no target triple: pass --triple (this platform is not one of the common ones)',
              file=sys.stderr)
        return 2

    out = Path(args.out) if args.out else DESKTOP / 'src-tauri' / 'bin'
    target = out / resolve.sidecar_filename(triple)

    if args.check:
        if not target.is_file():
            print(f'MISSING {target}', file=sys.stderr)
            return 1
        same = digest(target) == digest(source)
        print(f'{"OK     " if same else "DIFFERS"} {target}  {digest(target)}')
        return 0 if same else 1

    out.mkdir(parents=True, exist_ok=True)
    before = digest(source)
    shutil.copyfile(source, target)
    target.chmod(0o755)
    after = digest(target)
    print(f'staged {target}  {after}')
    print(f'source {source}  {before}')
    if before != after:
        print('the copy does not match its source', file=sys.stderr)
        return 1
    return 0


if __name__ == '__main__':
    raise SystemExit(main())