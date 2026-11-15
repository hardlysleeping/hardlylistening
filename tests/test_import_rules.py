"""Import limits: one audio file, no folders/zips/batches, 20 MB at most."""
import os
import tempfile
import unittest
from pathlib import Path

from library import import_rules


class Rules(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory(dir=os.environ["TMPDIR"])
        self.dir = Path(self.tmp.name)

    def tearDown(self):
        self.tmp.cleanup()

    def write(self, name, size):
        path = self.dir / name
        path.write_bytes(b"\0" * size)
        return path

    def test_audio_extension_is_accepted(self):
        for name in ("Song.mp3", "song.M4A", "a.flac", "b.ogg", "c.wav"):
            import_rules.check_name(name)      # must not raise

    def test_zip_and_other_files_are_refused(self):
        for name in ("mixtape.zip", "notes.txt", "setup.exe", "cover.jpg", "no-extension"):
            with self.assertRaises(ValueError) as caught:
                import_rules.check_name(name)
            self.assertIn("one audio file", str(caught.exception))

    def test_twenty_megabytes_is_the_ceiling(self):
        self.assertEqual(import_rules.check_size(20 * 1024 * 1024), 20 * 1024 * 1024)
        with self.assertRaises(ValueError) as caught:
            import_rules.check_size(20 * 1024 * 1024 + 1)
        self.assertIn("limited to 20 MB", str(caught.exception))

    def test_empty_file_is_refused(self):
        with self.assertRaises(ValueError):
            import_rules.check_size(0)

    def test_a_single_real_audio_file_passes(self):
        path = self.write("track.mp3", 1024)
        self.assertEqual(import_rules.check_path(str(path)), path)

    def test_a_folder_is_not_an_import(self):
        folder = self.dir / "album"
        folder.mkdir()
        self.write("album/one.mp3", 1024)
        with self.assertRaises(ValueError) as caught:
            import_rules.check_path(str(folder))
        self.assertIn("not folders, zips or multiple files", str(caught.exception))

    def test_a_zip_on_disk_is_not_an_import(self):
        archive = self.write("songs.zip", 2048)
        with self.assertRaises(ValueError):
            import_rules.check_path(str(archive))

    def test_an_oversized_single_file_is_refused(self):
        path = self.write("long.mp3", import_rules.MAX_IMPORT_BYTES + 1)
        with self.assertRaises(ValueError) as caught:
            import_rules.check_path(str(path))
        self.assertIn("20 MB", str(caught.exception))

    def test_a_missing_path_says_so(self):
        with self.assertRaises(ValueError) as caught:
            import_rules.check_path(str(self.dir / "nope.mp3"))
        self.assertIn("no such file", str(caught.exception))

    def test_the_message_names_the_limit_and_the_shapes_refused(self):
        message = import_rules.one_audio_file()
        for word in ("one audio file", "20 MB", "folders", "zips", "multiple files"):
            self.assertIn(word, message)


if __name__ == "__main__":
    unittest.main()
