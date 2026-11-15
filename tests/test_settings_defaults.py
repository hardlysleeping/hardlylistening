"""The library stores each track in the format its source actually gave us.

"best" tells yt-dlp to keep the source audio stream: a SoundCloud MP3 stays MP3,
a YouTube Opus stays Opus. No decode, no re-encode, no second generation. Anything
that needs another format (a ZIP a DJ app must open) converts on the way out, not
in storage.
"""
import tempfile
import unittest
from pathlib import Path

import db
from downloaders import ytdlp


class SourceNativeStorage(unittest.TestCase):
    def test_store_default_keeps_the_source_format(self):
        self.assertEqual(db.DEFAULTS['audio_format'], 'best')

    def test_downloader_default_keeps_the_source_format(self):
        self.assertEqual(ytdlp.YtDlpSettings().audio_format, 'best')

    def test_settings_ui_offers_source_and_falls_back_to_it(self):
        page = (db.APP_DIR / 'web' / 'index.html').read_text()
        self.assertIn('<option value="best">', page)
        self.assertIn('s.audio_format || "best"', page)

    def test_best_is_passed_straight_to_yt_dlp(self):
        cmd = ytdlp.build_command('https://example.invalid/x', '/tmp/a.%(ext)s',
                                  ytdlp.YtDlpSettings(audio_format='best', archive=None))
        self.assertEqual(cmd[cmd.index('--audio-format') + 1], 'best')

    def test_a_title_with_brackets_is_found_and_discarded(self):
        """[Full Vocal Mix] in a title used to be read as a glob character class.

        The glob matched nothing, a finished file was reported as "yt-dlp wrote no
        file", and every retry wrote another copy of the same song.
        """
        stem = "10 - Raindrops (feat. Carmen Forbes) [Full Vocal Mix]"
        with tempfile.TemporaryDirectory() as folder:
            written = Path(folder) / (stem + ".mp3")
            written.write_bytes(b"\x00" * 4096)
            outtmpl = str(Path(folder) / (stem + ".%(ext)s"))
            self.assertEqual(ytdlp.find_output(outtmpl, "best"), written)
            self.assertEqual(ytdlp.discard_output(outtmpl), 1)
            self.assertFalse(written.exists())

    def test_whatever_yt_dlp_wrote_is_found_again(self):
        """A source-native download can land as .mp3, .opus or .webm; the queue must
        still find the file or it is thrown away as 'yt-dlp wrote no file'."""
        with tempfile.TemporaryDirectory() as folder:
            for suffix in ('.mp3', '.opus', '.webm', '.m4a'):
                written = Path(folder) / ('a' + suffix)
                written.write_bytes(b'\x00' * 4096)
                self.assertEqual(ytdlp.find_output(str(Path(folder) / 'a.%(ext)s'), 'best'), written)
                written.unlink()


if __name__ == '__main__':
    unittest.main()