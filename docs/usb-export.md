# Preparing a drive for a CDJ (HL-228)

`/api/library/export` hands a DJ app a list of paths **on this machine**. That is a hand-off,
not preparation: carried to another computer, every path in it is dead. This is the other
thing — the audio is written onto the drive, in a folder the drive keeps, and the playlist
files are written beside it pointing at the drive's own paths, so the set imports on the DJ
computer with no network and no second copy step.

One dialog is the whole surface: `#dlg-export` ("Send to a DJ app", opened from the More
menu's *Send to a DJ app*). Its Scope field is the scope of both hand-offs.

## The four statements

**A plan before any bytes are written.** `POST /api/library/usb/plan` measures the drive's
free space, weighs every track (its own size, or what a 320 kbit/s conversion of it should
take), and answers. `fits` answers the question a person asks before writing to a stick —
does the whole set go on, and if not, what stays behind. `blocked` answers a different one:
do not write here at all.

A refused destination is still an **answer**, not an error: the reply is 200 with `blocked`
set, because the dialog shows the reason beside the field. Starting on that same destination
is the error (`400`).

The refusals, with their reasons:

| destination | refused because |
| --- | --- |
| empty | "Give the folder the drive is mounted at." |
| a relative path | "Use the drive's full path, as it is mounted on this machine." |
| nothing there | "There is no folder at `<path>`." |
| a file, not a folder | "`<path>` is a file, not a folder." |
| not writable | "This app cannot write to `<path>`. Check the drive is mounted read-write." |
| inside the library root | "That folder is inside the library, so the copy would include itself." |

`start` refuses a set that does not fit, and a scope with no files on disk, with the count of
tracks that would be left behind. Nothing is written by a refusal.

**A copy that verifies.** Each file is streamed into `<name>.part` while its SHA-256 is
computed, then that staged file is read back and hashed **again**; only when the hash and the
size both match is it renamed into place. A device that silently truncates fails that
comparison rather than producing a shorter file wearing a real name.

**A drive that can be pulled out.** A write error mid-copy (ENOSPC, EIO, the device gone)
fails the job with the reason, removes the `.part`, and leaves the track uncopied — never
reported as one that arrived. No index file is written for a partial set: a DJ app must not
open half a library.

**No new daemon.** One worker thread inside the server, started on demand, one job at a time.
The job is a row in SQLite (`usb_exports`, per-file state in `usb_export_files`), so a status
poll is a database read. A job that was `copying` when the process stopped is marked
`failed` — "interrupted — the app stopped before this finished" — at boot, rather than left
claiming to be live or silently re-run as if it had happened.

Nothing on the drive is deleted: a re-run overwrites its own names and leaves everything else
alone.

## What the index files carry

`HardlyListening.rekordbox.xml` and `HardlyListening.m3u8` are written at the **drive's root**
only after every track has arrived, each written atomically (`.part` then `os.replace`).

Per track, the XML carries the drive path, the title, artist and album, `AverageBpm` **when a
tempo was measured**, and the member's own hot cues as `POSITION_MARK`s.

It does not carry:

* **`Tonality`** — this app has no key detection, and an empty attribute would claim
  otherwise. A DJ app that needs a key must analyse the file itself.
* **`TotalTime`** — the length was never measured for this file, so it is `0` rather than
  invented. A converted copy's length is not re-measured for the same reason.

## Cost and permission

Copying is the most expensive thing this app does on a person's behalf: a set can be tens of
gigabytes onto a device that may be pulled out. So it is a queued job rather than a request
that sits there, it is limited to 10 starts a minute per member, it is CSRF-checked, and it
needs the same approved membership as any other library read. Anyone with the library can
prepare a drive — the library is shared; the drive is theirs.

## Files

| file | owns |
| --- | --- |
| `library/usb_export.py` | the plan, the job rows, the worker, the staged copy and the two index files |
| `library/dj_export.py` | `usb_document()` — the XML shape; `build()` — the on-this-PC export |
| `zip_export.py` | `to_mp3()` and `DJ_SAFE` — the one converter and the formats a CDJ takes as-is |
| `server.py` | `/api/library/usb/plan`, `/api/library/usb/start`, `GET /api/library/usb` |
| `web/index.html` | `#dlg-export`'s drive section, `#usbpath`, `#usbcheck`, `#usbgo`, `#usbplan` |

## Proof

`tests/test_usb_export.py` (14 tests): refusals, an overflowing plan that still lists the
whole set, missing files and estimate sizes, playlist scope, a verified copy with the XML's
BPM/cues and the absence of key and duration, a pulled-out drive leaving nothing reported as
copied, a real FLAC converted to a playable MP3, an interrupted job, the worker queue, and the
HTTP surface (methods, CSRF, auth, a plan that writes nothing).

`tests/test_usb_ui.cjs` (34 checks): the dialog is reachable, Check sends the folder and the
scope without submitting the form, the refusals, a plan that does not fit naming what stays
behind, the copy flow from a stubbed job (progress, ending, controls restored), a plan being
dropped when its scope or folder changes, and the whole dialog measured at 390×844.