"""Acquisition: how work enters the download queue (REPO-014).

The queue is `engine/queue.py`. This is the Repo-side name for the one operation the API performs
on it — turning a pasted link or a provider result into a job. Everything else on the worker
(`start`, `stop`, `wake`, `cancel`, `pause`) acts on a live instance and stays on the instance.

A facade rather than a move: signatures and answers are the worker's, so nothing downstream can
tell the difference, and a route can say "acquire this" without holding the worker's vocabulary.
"""
from __future__ import annotations


def ingest(queue, item):
    """Queue one acquisition: a URL string, or the dict a provider read produced.

    Returns the worker's own answer (`queued`, `playlist_id`, an existing-job id …) unchanged —
    including the `ValueError`s it raises, which the API already turns into a 400.
    """
    return queue.ingest(item)