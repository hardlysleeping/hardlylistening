"""Cover art: one image per distinct cover, a display-size copy inside each track.

The point of these tests is that the byte surgery is invisible: the audio decodes
to the same checksum, every tag frame survives verbatim, and a track that already
carries a small picture is left alone rather than grown.
"""
import hashlib
import os
import subprocess
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from db import Store
from library import art_store, artwork, id3art


def ffmpeg(*args):
    subprocess.run(["ffmpeg", "-v", "error", "-y", *args], check=True)


def make_mp3(path: Path, cover: str = "noise", size: int = 1200) -> Path:
    """A real mp3 with an attached picture, built by ffmpeg."""
    source = (f"nullsrc=s={size}x{size},geq=random(1)*255:128:128" if cover == "noise"
              else f"color=c=black:s={size}x{size}")
    ffmpeg("-f", "lavfi", "-i", "sine=frequency=440:duration=2",
           "-f", "lavfi", "-i", source,
           "-map", "0:a", "-map", "1:v", "-frames:v", "1",
           "-c:a", "libmp3lame", "-q:a", "5", "-c:v", "mjpeg",
           "-disposition:v", "attached_pic", "-id3v2_version", "3",
           "-metadata", "title=Fixture", "-metadata", "artist=Artist",
           "-metadata", "album=Album", str(path))
    return path


def make_m4a(path: Path, size: int = 1200) -> Path:
    """A real m4a with an attached picture — the format the sweep used to skip."""
    ffmpeg("-f", "lavfi", "-i", "sine=frequency=440:duration=2",
           "-f", "lavfi", "-i", f"nullsrc=s={size}x{size},geq=random(1)*255:128:128",
           "-map", "0:a", "-map", "1:v", "-frames:v", "1",
           "-c:a", "aac", "-b:a", "96k", "-c:v", "mjpeg",
           "-disposition:v", "attached_pic",
           "-metadata", "title=Fixture", "-metadata", "artist=Artist",
           str(path))
    return path


def audio_md5(path: Path) -> str:
    result = subprocess.run(["ffmpeg", "-v", "error", "-i", str(path), "-map", "0:a", "-f", "md5", "-"],
                            capture_output=True, text=True, check=True)
    return result.stdout.strip()


class TagSurgery(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.tmp = tempfile.TemporaryDirectory(dir=os.environ.get("TMPDIR", "/tmp"))
        cls.source = make_mp3(Path(cls.tmp.name) / "source.mp3")

    @classmethod
    def tearDownClass(cls):
        cls.tmp.cleanup()

    def test_every_other_frame_is_copied_verbatim(self):
        raw = self.source.read_bytes()
        _, before, _ = id3art.split(raw)
        jpeg = b"\xff\xd8\xff" + b"x" * 2000
        new = id3art.embed(raw, jpeg)
        _, after, _ = id3art.split(new)
        self.assertEqual([frame for ident, frame in before if ident != b"APIC"],
                         [frame for ident, frame in after if ident != b"APIC"])
        self.assertEqual([ident for ident, _ in after].count(b"APIC"), 1)

    def test_the_audio_never_moves_by_a_byte(self):
        raw = self.source.read_bytes()
        new = id3art.embed(raw, b"\xff\xd8\xff" + b"x" * 2000)
        self.assertEqual(id3art.audio(new), id3art.audio(raw))
        self.assertLess(len(new), len(raw))

    def test_the_new_cover_is_the_one_that_comes_back(self):
        raw = self.source.read_bytes()
        jpeg = b"\xff\xd8\xff" + b"z" * 4000
        new = id3art.embed(raw, jpeg)
        self.assertEqual(id3art.covers(new), [("image/jpeg", jpeg)])

    def test_embedding_the_same_cover_twice_reports_nothing_to_do(self):
        raw = self.source.read_bytes()
        once = id3art.embed(raw, b"\xff\xd8\xff" + b"q" * 3000)
        self.assertIsNone(id3art.embed(once, b"\xff\xd8\xff" + b"q" * 3000))

    def test_a_file_without_a_tag_is_refused_not_guessed(self):
        plain = Path(self.tmp.name) / "plain.mp3"
        ffmpeg("-f", "lavfi", "-i", "sine=frequency=440:duration=1",
               "-c:a", "libmp3lame", "-write_xing", "0", "-id3v2_version", "0", str(plain))
        with self.assertRaises(id3art.Unsupported):
            id3art.embed(plain.read_bytes(), b"\xff\xd8\xff" + b"x" * 100)

    def test_an_id3v1_only_file_is_refused(self):
        with self.assertRaises(id3art.Unsupported):
            id3art.embed(b"TAG" + b"\x00" * 125, b"\xff\xd8\xff" + b"x" * 100)


class Dedupe(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory(dir=os.environ.get("TMPDIR", "/tmp"))
        self.addCleanup(self.tmp.cleanup)
        base = Path(self.tmp.name)
        self.root = base / "music"
        self.root.mkdir()
        self.store = Store(base / "fixture.db")
        self.addCleanup(self.store.conn.close)
        self.store.set_settings({"root": str(self.root)})
        self.cache = base / "cache"
        for module in (art_store, artwork):
            patcher = patch.object(module, "CACHE", self.cache)
            patcher.start()
            self.addCleanup(patcher.stop)

    def add(self, name: str, path: Path) -> int:
        track_id = self.store.track("local", name, name)
        with self.store.lock:
            self.store.conn.execute("UPDATE tracks SET file=? WHERE id=?", (str(path), track_id))
            self.store.conn.commit()
        return track_id

    def test_two_tracks_with_one_cover_share_one_image(self):
        first = make_mp3(self.root / "one.mp3")
        second = self.root / "two.mp3"
        second.write_bytes(first.read_bytes())          # identical cover, identical file
        a, b = self.add("one", first), self.add("two", second)
        self.assertEqual(art_store.normalise(self.store, a), "slimmed")
        # The second file is a copy, so it still carries the full-size picture: it
        # slims too, but must land on the same stored image.
        self.assertEqual(art_store.normalise(self.store, b), "slimmed")
        images = [p for p in art_store.folder(self.store).iterdir() if p.is_file()]
        self.assertEqual(len(images), 1)
        rows = self.store.conn.execute("SELECT art_hash FROM tracks").fetchall()
        self.assertEqual(rows[0]["art_hash"], rows[1]["art_hash"])
        self.assertTrue(rows[0]["art_hash"])

    def test_the_file_keeps_a_display_size_cover_and_its_audio(self):
        path = make_mp3(self.root / "a.mp3")
        before = audio_md5(path)
        self.store.conn.execute("UPDATE tracks SET file='' WHERE 0")
        track = self.add("a", path)
        self.assertEqual(art_store.normalise(self.store, track), "slimmed")
        probe = subprocess.run(["ffprobe", "-v", "error", "-select_streams", "v:0",
                                "-show_entries", "stream=width", "-of", "csv=p=0", str(path)],
                               capture_output=True, text=True, check=True)
        self.assertEqual(probe.stdout.strip(), "640")
        self.assertEqual(audio_md5(path), before)
        titles = subprocess.run(["ffprobe", "-v", "error", "-show_entries", "format_tags=title",
                                 "-of", "csv=p=0", str(path)], capture_output=True, text=True)
        self.assertIn("Fixture", titles.stdout)

    def test_running_it_twice_changes_nothing_the_second_time(self):
        path = make_mp3(self.root / "a.mp3")
        track = self.add("a", path)
        self.assertEqual(art_store.normalise(self.store, track), "slimmed")
        digest = path.read_bytes()
        self.assertEqual(art_store.normalise(self.store, track), "already")
        self.assertEqual(path.read_bytes(), digest)

    def test_a_cover_already_smaller_than_the_display_copy_is_left_alone(self):
        # A rewrite that makes the file bigger is never worth it. Forced here rather
        # than hunting for a cover that happens to land on that side of the line.
        path = make_mp3(self.root / "tiny.mp3")
        before = path.read_bytes()
        bigger = self.root / "bigger.jpg"
        bigger.write_bytes(b"\xff\xd8\xff" + b"x" * (len(before) + 1))
        with patch.object(art_store, "display", return_value=bigger):
            track = self.add("tiny", path)
            self.assertEqual(art_store.normalise(self.store, track), "kept")
        self.assertEqual(path.read_bytes(), before)

    def test_an_mp4_is_never_rewritten_but_its_cover_is_filed(self):
        # MP4 tag surgery is out of scope: the cover is extracted and the file is
        # left byte for byte as it was.
        path = make_m4a(self.root / "x.m4a")
        before = path.read_bytes()
        self.assertEqual(art_store.normalise(self.store, self.add("x", path)), "kept")
        self.assertEqual(path.read_bytes(), before)
        self.assertTrue(self.store.conn.execute(
            "SELECT art_hash FROM tracks WHERE id="
            "(SELECT id FROM tracks ORDER BY id DESC LIMIT 1)").fetchone()[0])

    def test_a_container_with_no_cover_is_marked_checked(self):
        path = self.root / "x.m4a"
        path.write_bytes(b"\x00\x00\x00\x18ftypM4A ")
        before = path.read_bytes()
        self.assertEqual(art_store.normalise(self.store, self.add("x", path)), "no art")
        self.assertEqual(path.read_bytes(), before)

    def test_a_row_whose_file_is_gone_is_marked_so_it_is_not_retried_forever(self):
        track = self.add("gone", self.root / "gone.mp3")
        self.assertEqual(art_store.normalise(self.store, track), "missing")
        self.assertEqual(art_store.candidates(self.store), [])
        self.assertGreater(self.store.conn.execute(
            "SELECT art_checked FROM tracks WHERE id=?", (track,)).fetchone()[0], 0)

    def test_a_file_no_track_points_at_is_slimmed_too(self):
        path = make_mp3(self.root / "loose.mp3")
        before = path.stat().st_size
        self.assertEqual(art_store.sweep(self.store)["slimmed"], 1)
        self.assertLess(path.stat().st_size, before)
        self.assertTrue(self.store.conn.execute(
            "SELECT art_hash FROM art_files WHERE path=?", (str(path),)).fetchone()[0])
        # Second pass: the file is remembered by size and mtime, so nothing is re-read.
        self.assertEqual(art_store.sweep(self.store)["slimmed"], 0)

    def test_a_loose_file_that_changes_is_looked_at_again(self):
        path = make_mp3(self.root / "loose.mp3")
        art_store.sweep(self.store)
        path.write_bytes(make_mp3(self.root / "again.mp3").read_bytes())
        self.assertIn(path, art_store.loose_files(self.store))

    def test_the_sweep_does_the_work_and_nothing_else_queues(self):
        a = self.add("a", make_mp3(self.root / "a.mp3"))
        b = self.add("b", make_mp3(self.root / "b.mp3", cover="black", size=900))
        seen = art_store.sweep(self.store)
        self.assertEqual(seen["slimmed"], 2)
        self.assertEqual(art_store.candidates(self.store), [])
        self.assertEqual(len({row[0] for row in self.store.conn.execute(
            "SELECT art_hash FROM tracks WHERE id IN (?,? )", (a, b))}), 2)


def make_image(path: Path, suffix: str = "jpg", size: int = 800) -> bytes:
    ffmpeg("-f", "lavfi", "-i", f"nullsrc=s={size}x{size},geq=random(1)*255:128:128",
           "-frames:v", "1", str(path))
    return path.read_bytes()


@unittest.skipUnless(art_store._tool("cjxl") and art_store._tool("djxl"),
                     "cjxl/djxl are not installed")
class JpegXl(unittest.TestCase):
    """The store's canonical format: one smaller file that still holds the original."""

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory(dir=os.environ.get("TMPDIR", "/tmp"))
        self.addCleanup(self.tmp.cleanup)
        base = Path(self.tmp.name)
        self.root = base / "music"
        self.root.mkdir()
        self.store = Store(base / "fixture.db")
        self.addCleanup(self.store.conn.close)
        self.store.set_settings({"root": str(self.root)})
        self.cache = base / "cache"
        for module in (art_store, artwork):
            patcher = patch.object(module, "CACHE", self.cache)
            patcher.start()
            self.addCleanup(patcher.stop)
        self.jpeg = make_image(base / "cover.jpg")
        self.png = make_image(base / "cover.png", suffix="png")

    def test_a_jpeg_is_stored_as_jxl_and_is_smaller(self):
        key = art_store.put(self.store, self.jpeg)
        stored = art_store.image(self.store, key)
        self.assertEqual(stored.suffix, ".jxl")
        self.assertLess(stored.stat().st_size, len(self.jpeg))

    def test_the_stored_copy_still_contains_the_original_jpeg_exactly(self):
        key = art_store.put(self.store, self.jpeg)
        out = Path(self.tmp.name) / "back.jpg"
        subprocess.run([art_store._tool("djxl"), str(art_store.image(self.store, key)),
                        str(out)], check=True, capture_output=True)
        self.assertEqual(out.read_bytes(), self.jpeg)

    def test_the_same_image_is_not_stored_twice(self):
        first = art_store.put(self.store, self.jpeg)
        second = art_store.put(self.store, self.jpeg)
        self.assertEqual(first, second)
        self.assertEqual(len(list(art_store.folder(self.store).glob("*.jxl"))), 1)

    def test_the_display_copy_is_still_a_small_jpeg(self):
        key = art_store.put(self.store, self.jpeg)
        jpeg = art_store.display(self.store, key)
        self.assertIsNotNone(jpeg)
        self.assertEqual(jpeg.read_bytes()[:3], b"\xff\xd8\xff")
        probe = subprocess.run(["ffprobe", "-v", "error", "-select_streams", "v:0",
                                "-show_entries", "stream=width", "-of", "csv=p=0", str(jpeg)],
                               capture_output=True, text=True, check=True)
        self.assertEqual(probe.stdout.strip(), "640")

    def test_existing_jpeg_and_png_copies_are_converted_and_the_originals_go(self):
        folder = art_store.folder(self.store)
        folder.mkdir(parents=True, exist_ok=True)
        (folder / "aaa.jpg").write_bytes(self.jpeg)
        (folder / "bbb.png").write_bytes(self.png)
        seen = art_store.migrate(self.store)
        self.assertEqual(seen["converted"], 2)
        self.assertEqual(sorted(p.suffix for p in folder.iterdir()), [".jxl", ".jxl"])
        self.assertGreater(seen["saved"], 0)

    def test_a_file_that_cannot_be_converted_is_kept(self):
        folder = art_store.folder(self.store)
        folder.mkdir(parents=True, exist_ok=True)
        broken = folder / "ccc.jpg"
        broken.write_bytes(b"\xff\xd8\xff" + b"not really a jpeg" * 10)
        seen = art_store.migrate(self.store)
        self.assertEqual(seen["kept"], 1)
        self.assertTrue(broken.exists())


class Covers(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory(dir=os.environ.get("TMPDIR", "/tmp"))
        self.addCleanup(self.tmp.cleanup)
        base = Path(self.tmp.name)
        self.root = base / "music"
        self.root.mkdir()
        self.store = Store(base / "fixture.db")
        self.addCleanup(self.store.conn.close)
        self.store.set_settings({"root": str(self.root)})
        self.cache = base / "cache"
        for module in (art_store, artwork):
            patcher = patch.object(module, "CACHE", self.cache)
            patcher.start()
            self.addCleanup(patcher.stop)

    def track(self, name: str, path: Path) -> int:
        track_id = self.store.track("local", name, name)
        with self.store.lock:
            self.store.conn.execute("UPDATE tracks SET file=? WHERE id=?", (str(path), track_id))
            self.store.conn.commit()
        return track_id

    def test_a_request_before_the_sweep_still_answers_and_links_the_cover(self):
        path = make_mp3(self.root / "a.mp3")
        track = self.track("a", path)
        jpeg = artwork.for_track(self.store, track)
        self.assertIsNotNone(jpeg)
        self.assertTrue(jpeg.is_file())
        self.assertGreater(self.store.conn.execute(
            "SELECT art_hash FROM tracks WHERE id=?", (track,)).fetchone()[0].__len__(), 0)
        # Asking again must not touch ffmpeg at all, because the hash is recorded.
        with patch.object(artwork.subprocess, "run", side_effect=AssertionError("re-extracted")):
            self.assertEqual(artwork.for_track(self.store, track), jpeg)

    def test_a_track_without_a_cover_says_so_and_stops_asking(self):
        path = Path(self.root / "bare.mp3")
        ffmpeg("-f", "lavfi", "-i", "sine=frequency=440:duration=1",
               "-c:a", "libmp3lame", str(path))
        track = self.track("bare", path)
        self.assertIsNone(artwork.for_track(self.store, track))
        self.assertIsNone(artwork.for_track(self.store, track))


if __name__ == "__main__":
    unittest.main()