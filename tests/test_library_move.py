"""Moving the library root carries the swap history, or refuses a destination it cannot (HL-230).

A completed replacement parks the media it replaced under `.replacements/retained/<id>/` and
`reverse` puts it back at `old_file` — an absolute path. So a root move used to leave every
parked undo behind: the media stayed under the old root and `reverse` refused because the
previous file was no longer inside the library. The move now carries the folder, rewrites the
rows, and refuses a destination inside the current library, where the copy would be moved into
the tree it is leaving.
"""
import os
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

import replacements
import test_replacements
from db import Store
from engine.queue import Queue
from library import browser_downloads
from library.import_files import move_library

wav = test_replacements.wav


class LibraryMove(unittest.TestCase):
    setUp = test_replacements.Replacements.setUp
    stage = test_replacements.Replacements.stage
    fake = test_replacements.Replacements.fake
    run_fake = test_replacements.Replacements.run_fake

    def completed(self):
        """A swap that finished: the new file is canonical, the old one is parked."""
        row = self.stage()
        self.run_fake()
        return row

    def retained(self, row, root=None):
        return Path(root or self.root) / '.replacements' / 'retained' / str(row['id'])

    def track_file(self, tid=None):
        return Path(self.store.track_by_id(tid or self.tid)['file'])

    def scratch(self):
        """A staging directory a swap still owns: `.replacements/<uuid>/audio.wav`."""
        folder = self.root / '.replacements' / ('a' * 32)
        folder.mkdir(parents=True, exist_ok=True)
        (folder / 'audio.wav').write_bytes(wav(9))
        return folder

    def swap_on_a_file_outside_the_root(self):
        """A second track whose media lives elsewhere, with a completed swap on it."""
        outside = Path(self.tmp.name) / 'elsewhere'
        outside.mkdir()
        source = outside / 'kept.wav'
        source.write_bytes(wav(3))
        tid = self.store.track('apple', 'outside-fixture', 'Outside')
        job = self.store.enqueue(tid, 'apple')
        self.store.conn.execute('UPDATE tracks SET file=?,size=? WHERE id=?',
                                (str(source), source.stat().st_size, tid))
        self.store.update_job(job, status='completed',
                              resolved_url='https://example.test/outside', pinned=0)
        replacements.stage(self.store, tid, 'https://example.test/outside-new')

        def download(cmd, **kwargs):
            Path(cmd[cmd.index('-o') + 1].replace('%(ext)s', 'wav')).write_bytes(wav(4))
            return 0, ''

        with patch('replacements.ytdlp.run', side_effect=download):
            self.queue._run(self.store.job(job))
        with self.store.lock:
            row = self.store.conn.execute(
                "SELECT * FROM source_replacements WHERE track_id=? AND status='completed'",
                (tid,)).fetchone()
        self.assertIsNotNone(row, 'the fixture needs a completed swap on a file outside the root')
        self.assertEqual(row['old_file'], str(source))
        return dict(row), source

    def test_the_parked_media_and_its_rows_move_with_the_tree(self):
        row = self.completed()
        self.assertTrue((self.retained(row) / 'original.wav').is_file())
        scratch = self.scratch()
        target = Path(self.tmp.name) / 'second-root'
        result = move_library(self.store, str(target))
        # The library itself moved, and it plays from where it now is.
        self.assertEqual(result['moved'], 1)
        self.assertEqual(result['root'], str(target))
        self.assertEqual(result['replacements'], 1)
        self.assertEqual(result['unserviceable'], [])
        moved_file = self.track_file()
        self.assertTrue(moved_file.is_file())
        self.assertTrue(str(moved_file).startswith(str(target)))
        self.assertEqual(browser_downloads.local_path(self.store, self.store.track_by_id(self.tid)),
                         moved_file.resolve())
        # The undo came along: the parked original, its scratch directory, and the row.
        self.assertFalse((self.root / '.replacements').exists())
        self.assertEqual((self.retained(row, target) / 'original.wav').read_bytes(), self.original)
        self.assertTrue((target / '.replacements' / scratch.name / 'audio.wav').is_file())
        stored = self.store.conn.execute(
            "SELECT old_file,new_file FROM source_replacements WHERE id=?", (row['id'],)).fetchone()
        self.assertTrue(stored['old_file'].startswith(str(target)))
        self.assertTrue(stored['new_file'].startswith(str(target)))
        # And the thing the card is about: an old replacement still reverses.
        replacements.reverse(self.store, row['id'])
        self.assertEqual(self.track_file().read_bytes(), self.original)
        self.assertEqual(self.store.job(self.jid)['resolved_url'], 'https://example.test/old')
        self.assertFalse(self.retained(row, target).exists())

    def test_a_destination_inside_the_library_is_refused_before_anything_moves(self):
        row = self.completed()
        before = sorted(str(p) for p in self.root.rglob('*'))
        with self.assertRaisesRegex(ValueError, 'inside the current one'):
            move_library(self.store, str(self.root / 'nested'))
        # Nothing moved, nothing was rewritten, and the parked undo is where it was.
        self.assertEqual(self.store.setting('root'), str(self.root))
        self.assertEqual(sorted(str(p) for p in self.root.rglob('*')), before)
        self.assertTrue((self.retained(row) / 'original.wav').is_file())
        self.assertFalse((self.root / 'nested' / '.replacements').exists())

    def test_a_previous_file_outside_the_library_is_reported_not_pretended(self):
        travelled = self.completed()
        row, source = self.swap_on_a_file_outside_the_root()
        target = Path(self.tmp.name) / 'second-root'
        result = move_library(self.store, str(target))
        # A move cannot adopt a folder that was never part of the library, so that one undo is
        # named instead of being rewritten into something that does not exist.
        self.assertEqual(result['unserviceable'], [row['id']])
        self.assertEqual(self.track_file(row['track_id']).read_bytes(), wav(4))
        line = [entry['msg'] for entry in self.store.logs() if 'outside it' in entry['msg']][0]
        self.assertIn(str(row['id']), line)
        # Everything else still reverses: the revocable undo travels with the library.
        replacements.reverse(self.store, travelled['id'])
        self.assertEqual(self.track_file().read_bytes(), self.original)

    def test_the_trash_moves_and_still_restores(self):
        import deletion
        self.store.conn.execute('UPDATE tracks SET file=?,size=? WHERE id=?',
                                (str(self.root / 'original.wav'), len(self.original), self.tid))
        self.root.joinpath('original.wav').write_bytes(self.original)
        deletion.delete_track(self.store, self.tid)
        with self.store.lock:
            row = dict(self.store.conn.execute("SELECT * FROM library_trash").fetchone())
        self.assertTrue(Path(row['trash_path']).is_file())
        target = Path(self.tmp.name) / 'second-root'
        result = move_library(self.store, str(target))
        self.assertEqual(result['trash'], 1)
        # The trashed file travelled with the library and its row says where it now is, so a
        # restore puts the song back inside the new root instead of refusing it.
        stored = self.store.conn.execute(
            "SELECT path,trash_path FROM library_trash WHERE id=?", (row['id'],)).fetchone()
        self.assertTrue(stored['trash_path'].startswith(str(target)))
        self.assertFalse((self.root / '.trash').exists())
        deletion.restore_one(self.store, row['id'])
        self.assertEqual(Path(stored['path']).read_bytes(), self.original)
        self.assertTrue(str(stored['path']).startswith(str(target)))

    def test_a_library_with_no_swaps_moves_as_before(self):
        self.store.conn.execute("DELETE FROM source_replacements")
        self.store.conn.commit()
        target = Path(self.tmp.name) / 'second-root'
        result = move_library(self.store, str(target))
        self.assertEqual(result['moved'], 1)
        self.assertEqual(result['replacements'], 0)
        self.assertEqual(result['unserviceable'], [])
        self.assertFalse((target / '.replacements').exists())
        self.assertEqual(self.track_file().read_bytes(), self.original)

    def test_a_history_already_present_on_the_far_side_is_merged_not_overwritten(self):
        row = self.completed()
        target = Path(self.tmp.name) / 'second-root'
        landed = self.retained(row, target)
        landed.mkdir(parents=True)
        keeper = landed / 'original.wav'
        keeper.write_bytes(wav(7))
        result = move_library(self.store, str(target))
        # The file that was already there is untouched, and the copy left behind is not deleted.
        self.assertEqual(keeper.read_bytes(), wav(7))
        self.assertTrue((self.retained(row) / 'original.wav').is_file())
        self.assertEqual(result['replacements'], 1)


if __name__ == '__main__':
    unittest.main()