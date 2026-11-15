"""The page's inline scripts have to parse.

`web/index.html` is served as-is, so a syntax error in one of its inline `<script>` blocks is
not a build failure — it is a page that silently stops halfway. That happened: HL-306 added a
`function fmtBytes` next to the `const fmtBytes` from 0.11.0, two declarations of one name in
one script, and the whole first block — `$`, the library, the player, the attach that starts the
media session — never evaluated. The page still painted, so nothing looked wrong until a player
control did nothing.

Each block is checked on its own, exactly as the browser compiles it: one file per block, no
concatenation, `node --check`. That catches a duplicate declaration, an unbalanced brace, a
stray arrow, and any later edit that breaks the block it lands in.
"""
import re
import shutil
import subprocess
import tempfile
import unittest
from pathlib import Path

APP_DIR = Path(__file__).resolve().parents[1]
INDEX = APP_DIR / 'web' / 'index.html'
NODE = shutil.which('node')

#: Inline blocks only: a `src` script is a separate file with its own lifecycle. The negative
#: lookahead is what keeps `<script src=...>` from matching.
INLINE = re.compile(r'<script(?![^>]*\bsrc=)[^>]*>(.*?)</script>', re.S | re.I)


def blocks():
    return INLINE.findall(INDEX.read_text(encoding='utf-8'))


class InlineScripts(unittest.TestCase):
    def test_the_scan_finds_the_page_scripts(self):
        found = blocks()
        self.assertGreaterEqual(len(found), 4, 'the inline blocks moved or the scan drifted')
        # The big one is the app itself; a scan that only finds the small tail blocks would
        # pass every check below while never looking at the code that broke.
        self.assertGreater(max(len(b.splitlines()) for b in found), 1000)

    def test_every_block_parses(self):
        if not NODE:
            self.skipTest('node is not on PATH')
        found = blocks()
        self.assertTrue(found, 'no inline scripts found in web/index.html')
        with tempfile.TemporaryDirectory() as tmp:
            for index, body in enumerate(found, 1):
                path = Path(tmp) / f'block{index}.js'
                path.write_text(body, encoding='utf-8')
                result = subprocess.run([NODE, '--check', str(path)],
                                        capture_output=True, text=True, timeout=120)
                self.assertEqual(result.returncode, 0,
                                 f'inline script {index} does not parse:\n{result.stderr}')


if __name__ == '__main__':
    unittest.main()