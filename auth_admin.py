#!/usr/bin/env python3
"""Explicit local-only administrator recovery/enrollment. Never starts workers."""
import argparse
from db import Store
from security import Security

def main():
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument('--db', required=True, help='Exact existing application SQLite path')
    g = p.add_mutually_exclusive_group(required=True)
    g.add_argument('--name', help='Create a new administrator with this display name')
    g.add_argument('--member-id', help='Issue a recovery invite for an existing administrator')
    args = p.parse_args()
    sec = Security()
    sec.config()
    from pathlib import Path
    if not Path(args.db).is_file():
        p.error('Database must already exist')
    store = Store(Path(args.db))
    try:
        actor = {'role': 'admin'}  # authority is explicit local terminal access, never HTTP
        if args.member_id:
            member = sec.member(store, {'id': args.member_id})
            if member['role'] != 'admin':
                p.error('Recovery target must already be administrator')
            uid = args.member_id
        else:
            uid = sec.create_user(store, actor, args.name, 'admin')
        print(sec.invite(store, actor, uid))
    finally:
        store.conn.close()

if __name__ == '__main__':
    main()
