"""Two recordings with one name must not become one file.

Source-native storage means the queue cannot predict the suffix (an Opus origin
lands as .opus or .webm, an MP3 origin as .mp3), so the collision check has to
compare stems instead of full names.
"""
import tempfile
import unittest
from pathlib import Path

from library import store


class UniqueAnySuffix(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name)
        self.root.mkdir(exist_ok=True)

    def tearDown(self):
        self.tmp.cleanup()

    def test_a_free_name_is_used_as_is(self):
        target = self.root / 'Song.mp3'
        self.assertEqual(store.unique_any_suffix(target, set()), target)

    def test_an_existing_file_with_another_suffix_is_a_collision(self):
        (self.root / 'Song.webm').write_bytes(b'x')
        target = store.unique_any_suffix(self.root / 'Song.mp3', set())
        self.assertEqual(target.name, 'Song (2).mp3')

    def test_a_row_pointing_at_another_suffix_is_a_collision(self):
        target = store.unique_any_suffix(self.root / 'Song.mp3', {str(self.root / 'Song.opus')})
        self.assertEqual(target.name, 'Song (2).mp3')

    def test_numbers_keep_climbing(self):
        (self.root / 'Song.m4a').write_bytes(b'x')
        (self.root / 'Song (2).webm').write_bytes(b'x')
        target = store.unique_any_suffix(self.root / 'Song.mp3', set())
        self.assertEqual(target.name, 'Song (3).mp3')

    def test_brackets_in_a_title_are_not_a_pattern(self):
        (self.root / 'Song [FREE DL].mp3').write_bytes(b'x')
        target = store.unique_any_suffix(self.root / 'Song [FREE DL].mp3', set())
        self.assertEqual(target.name, 'Song [FREE DL] (2).mp3')


if __name__ == '__main__':
    unittest.main()