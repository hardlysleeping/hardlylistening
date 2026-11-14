# Podcast retention (HL-234)

The 5 GB per-member budget (`HARDLYLISTENING_PODCAST_CAP_MB`) was only half a storage policy. It
refused a download when the member was full and told them to delete something by hand, so a
weekly show fills a budget and then stays full until somebody goes looking. Retention is the
other half: a channel keeps what it is told to keep, and a purge takes the rest.

## Opt-in, per channel

A channel with no policy keeps everything. That is the default for every channel that existed
before this card, and it is why upgrading deletes nothing: the columns are added as `0`, whatever
`HARDLYLISTENING_PODCAST_KEEP_NEWEST` / `..._KEEP_DAYS` say. Those two variables only seed a
channel **created** after they are set.

| column | means | default |
| --- | --- | --- |
| `keep_newest` | keep the newest N **downloads** of this channel | `0` — keep all |
| `keep_days` | keep anything downloaded within N days | `0` — keep all |

A policy is set at subscribe time (`createPodcastChannel` accepts `keepNewest` and `keepDays`;
non-standard parameters a Subsonic client ignores unless it knows them) or later through
`podcasts.set_retention()`. `getPodcasts` reports each channel's policy as `retention`, and a
policy that makes no sense is refused: both numbers are whole, `0 ≤ keepNewest ≤ 1000` and
`0 ≤ keepDays ≤ 3650`.

## What the purge takes, and what it never touches

Only downloaded episodes of a channel that has a policy are candidates. Two rules, both meaning
*keep*:

* the newest `keep_newest` downloads, ranked by publish date (a tie breaks on id);
* everything downloaded less than `keep_days` ago.

A file is removed when **neither** rule keeps it, and the window is the harder of the two — an
episode older than `keep_days` goes even when the count would have kept it. The reason is
recorded as `age` or `newest`.

Removal means the file is unlinked and the episode's `file` / `bytes` / `downloaded` are cleared.
**The episode row stays**, so the client still lists it, now as `status: "new"` — a pruned episode
can be downloaded again. Nothing else is touched: `deletePodcastChannel` and
`deletePodcastEpisode` are unchanged, the feed cache is not this module's, and no file outside
`podcasts/<member>/` is ever unlinked.

## When it runs

* **Before a download needs room.** `downloadPodcastEpisode` runs the member's policy first,
  counting the episode being downloaded as kept, then reads the budget. That is what makes a full
  member get room rather than a refusal: keep-newest 1 and a full budget means the older file goes
  and the new one fits. If the policy still cannot free enough, the refusal says so ("the
  retention policy freed X, which was not enough") instead of pretending nothing was tried.
* **In the worker.** The app's scheduler (`engine/scheduler.py`) runs `podcasts.prune_all()` every
  15 minutes, which sweeps every member who has a policy. A request never waits for a sweep.

## The audit trail, and the report

Every removal is one append-only row in `listening_podcast_prunes`:
`(ts, member_id, channel_id, episode_id, title, bytes, reason)`. `podcasts.prune_log()` reads a
member's own rows newest-first, and `getPodcasts.storage.pruned` reports that same list, so a
client can say what the policy took and how many bytes came back.

Two claims on one file are impossible: a download, a policy sweep and `deletePodcastEpisode` all
take the same per-member lock, and the purge only logs a removal when its unlink actually returned
a file. If a manual delete got there first, the policy takes nothing, logs nothing, and does not
raise.

## Where it lives

| piece | file |
| --- | --- |
| schema, `set_retention`, `prune_member` / `prune_all` / `prune_log`, the download hook | `podcasts.py` |
| the periodic pass | `engine/scheduler.py` |
| the `keepNewest` / `keepDays` extension on `createPodcastChannel` | `subsonic.py` |
| the tests | `tests/test_podcast_prune.py` (with `tests/test_podcast_downloads.py` holding the cap) |

## What this is not

- **Not a background downloader.** Nothing is fetched on a schedule; the policy only removes
  local copies.
- **Not a server-wide default that changes existing channels.** A channel created before the
  policy keeps everything until somebody sets a number on it.
- **Not a trash.** A pruned file is gone. Keeping one would defeat the point of freeing the
  budget, and the episode is still listed to download again.