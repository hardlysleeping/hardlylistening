"""Read the genre each downloaded file already carries into genre_labels.

Run once to fill the library without any AI call:

    python3 tools/genre_backfill.py            # every untagged downloaded file
    python3 tools/genre_backfill.py --limit 50 # a first look

Embedded tags outrank AI guesses (a human correction still outranks both), and files with no
genre tag are left alone for the AI pass.
"""
import argparse
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import db
import genres


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--limit', type=int, default=None)
    args = parser.parse_args()
    store = db.Store()
    result = genres.backfill(store, args.limit)
    print('scanned %(scanned)d files, tagged %(tagged)d' % result)
    store.conn.close()


if __name__ == '__main__':
    main()
