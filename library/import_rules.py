"""What a member is allowed to import: one audio file, nothing else.

Folders, archives and batches were the biggest way to dump 30 GB into the
shared library in one click, so imports are deliberately narrow:

* exactly **one** file per request — no folder walks, no zip, no multi-select;
* the file must look like audio by extension (the importer still probes it);
* at most `MAX_IMPORT_BYTES` (20 MB) of bytes.

The rules live here rather than inline in the handler so the upload path and
the path-import path cannot drift apart. Raise `ValueError` with the message the
user should see; `Handler` already turns that into a 400.
"""

from __future__ import annotations

from pathlib import Path

from library.import_files import AUDIO_EXTS

MAX_IMPORT_BYTES = 20 * 1024 * 1024     # 20 MB, for now

_NAMES = ", ".join(sorted(ext.lstrip(".") for ext in AUDIO_EXTS))


def human(limit: int = MAX_IMPORT_BYTES) -> str:
    return f"{limit / (1024 * 1024):.0f} MB"


def one_audio_file() -> str:
    """The single sentence every refusal shares."""
    return (f"imports take one audio file at a time ({_NAMES}) up to {human()} — "
            "not folders, zips or multiple files")


def check_name(name: str) -> str:
    suffix = Path(str(name or "")).suffix.lower()
    if suffix not in AUDIO_EXTS:
        raise ValueError(one_audio_file())
    return suffix


def check_size(size: int, what: str = "file") -> int:
    if size <= 0:
        raise ValueError("that file is empty")
    if size > MAX_IMPORT_BYTES:
        raise ValueError(f"that {what} is {size / (1024 * 1024):.1f} MB; "
                         f"imports are limited to {human()}")
    return size


def check_path(raw: str) -> Path:
    """One real audio file on this machine. Directories and archives are not it."""
    path = Path(str(raw or "").strip()).expanduser()
    if not path.exists():
        raise ValueError(f"no such file: {path}")
    if path.is_dir():
        raise ValueError(one_audio_file())
    if not path.is_file():
        raise ValueError(one_audio_file())
    check_name(path.name)
    check_size(path.stat().st_size)
    return path
