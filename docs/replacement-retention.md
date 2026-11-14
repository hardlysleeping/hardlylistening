# What a source change keeps, and for how long (HL-229)

Changing a track's source is a staged swap: the new file is downloaded privately, verified,
and only then does the library start naming it. The file it replaced is not deleted — it is
parked under `<root>/.replacements/retained/<replacement id>/` so the change can be reversed.

That was the whole policy before this card: keep it, forever. Two things wrong with that. The
folder grows with every source change and nothing ever decides a keep has done its job, and
the scratch directory each download wrote into (`<root>/.replacements/<uuid>/`) is left behind
after the swap moves the file out.

## Two kinds of thing, decided differently

| what | where | when it goes |
| --- | --- | --- |
| **Retained media** — the file a completed swap replaced | `.replacements/retained/<id>/` | when the row is past the window. Inside the window it is never touched, whatever else is true of it |
| **An empty scratch directory** | `.replacements/<uuid>/` | at once: a finished swap moved its file out, so an empty one is litter with nothing to lose |
| **A scratch directory still holding something** | `.replacements/<uuid>/` | only when its replacement is over *and* the window has passed |
| **A scratch directory a queued or downloading row points at** | `.replacements/<uuid>/` | never, at any age — a live download owns its own folder |

Nothing outside `.replacements/` is read or written. The library tree is not this collector's
business, and canonical media is never globbed: a file is collected because a row names it,
never because it looks like an orphan.

## The window

`replacements_keep_days`, default **30**, in the settings table (`POST /api/settings` takes it,
because it has to be a key in `DEFAULTS`). `0` turns collection off entirely — that is what the
app did before there was a collector, and a person who wants every previous copy kept keeps it.

The window is per replacement and measured from the swap itself (`source_replacements.created`),
not from when the collector last looked, so a collector that was not running for a week does
not shorten anybody's window.

## What happens when a copy is collected

* **The removal is logged before the unlink** — `collected <name> (<bytes> bytes) from <id>`,
  one line per file, so a line that never had its removal cannot look like one that did.
* **The row is marked `pruned`** with the time. A reverse after that point does not fail on a
  missing file: it answers with the fact — *the previous file was collected on 2026-10-02 and
  cannot be restored; change the source again to get another copy*.
* **The library is not touched.** The track keeps the media the swap installed.

## When it runs

* **At boot**, and then every six hours, in one daemon thread (`replacements.start`). It is a
  standing job, not a scheduler job: collection has to happen whether or not anybody downloads
  anything, and it must not hold up the queue.
* **On demand**, `POST /api/replacements/collect` (administrator, CSRF-checked), which answers
  with the same report the thread logs — `removed`, `bytes`, `staging`, `dirs`, `rows`,
  `window_days`. Useful when the window has just been changed and waiting six hours is silly.
  There is no button for it: the standing job is the interface, and the route is for scripting.
* **Never as disk pressure.** The disk ceiling's job is to throw away the trash, oldest first;
  collecting a reverse's copy early because the disk is full would take away an undo the person
  was told they had.

## Report

`collect(store, now=0, limit=500)` returns:

| key | meaning |
| --- | --- |
| `removed` | files unlinked (retained copies and staging content) |
| `bytes` | their total size |
| `staging` | how many of `removed` came from scratch directories |
| `dirs` | scratch directories removed |
| `rows` | replacement rows examined for retained media |
| `window_days` | the window that was applied |
| `reason` | why nothing happened, when nothing happened (`retention is off`) |

`limit` bounds how many rows one pass will consider, so a first run on a large history does not
hold the database for a long time; the next pass continues.

## Moving the library root (HL-230)

`POST /api/library/move` (administrator) rewrites what it carries, so a move is not a way to
lose an undo:

* `.replacements/` travels — both `retained/<id>/` (the media a completed swap parked) and any
  `<uuid>/` scratch directory a swap still owns. Folders are named by a unique id, so the two
  trees merge instead of overwriting, and a name already present on the far side is left alone;
  a move that died halfway can be run again.
* The rows are rewritten: `source_replacements.old_file` and `new_file` point at the new root, so
  `reverse` still knows where its file goes, and the dated refusal for a collected copy is
  unchanged.
* `.trash/` travels the same way, with `library_trash.path` and `trash_path` rewritten, so
  `restore` keeps working inside the new root.
* A destination **inside** the current library is refused before anything moves: the retained
  media would be copied into the tree it is being moved out of.
* A swap whose previous file already lives outside the library cannot be adopted by a move. It is
  reported in `unserviceable` and logged, not rewritten into a path that does not exist; `reverse`
  already refused those rows before the move and still does.

Verified by `tests/test_library_move.py` (6 tests): a moved library plays from its new root, the
parked original and its scratch directory travel, the row is rewritten and `reverse` restores the
original; the trash travels and `restore` puts a song back inside the new root; a nested
destination is refused with nothing moved; a previous file outside the library is reported; a
library with no swaps moves exactly as before; and a history already present on the far side is
merged without overwriting a file.

## Files

| file | owns |
| --- | --- |
| `replacements.py` | the schema (`pruned` column), `keep_days`, `collect`, `_collect_folder`, `start`/`stop`/`_loop`, and the `reverse` refusal |
| `core/db.py` | `replacements_keep_days` in `DEFAULTS` (so `set_settings` will accept it) |
| `server.py` | `replacements.start(STORE)` in `boot()`, and `POST /api/replacements/collect` |
| `library/import_files.py` | `move_library`'s carry and rewrite (`_move_dotfolder`, `_rewrite_replacements`, `_rewrite_trash`, `_inside`, `_unserviceable`) |

## Proof

`tests/test_replacements.py`, class `Retention` (8 tests): a window that passed collects the
retained copy, logs the line, marks the row and makes `reverse` name the date; a reverse inside
the window still works; `0` collects nothing and still reverses; an empty scratch directory goes
at any age; a queued or downloading directory is never touched even at 400 days; a failed swap's
leftovers go only after the window; a database created before this card gains the `pruned`
column with a default of 0; and the collector is one daemon thread. `ReplacementRetentionHTTP`
covers the route: a member gets 403 and nothing is written, an administrator gets the report.