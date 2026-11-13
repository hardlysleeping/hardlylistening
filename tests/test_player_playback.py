"""PLAYER-004: the playback layer of the player shell.

The behaviour is pinned by `tests/test_player_playback.cjs`, which drives the module with a
fake `navigator.mediaSession`, a fake audio element and a fake queue. What is asserted here are
the things a runtime test cannot see:

  * the shape the shell depends on — one classic script, no modules, no dependency, no storage;
  * the promise that makes a backgrounded WebView survivable — **nothing is scheduled on a
    timer**, so a suspended page has no missed decisions to replay;
  * the protocol points a scrobble is owed at, and the fact that a second "now playing" report
    is opt-in because this server counts every call it receives;
  * that the queue it drives is the app's own (`libQueue`, `playingId`, `playFromLibrary`,
    `stepTrack`) rather than a second one grown in the player, and that `web/index.html` still
    offers those names;
  * that it ships, because `player/shell/` is the assembly's shell allow-list, and that the
    document a reader is sent to says what is true and what is unverified.
"""
import json
import re
import shutil
import subprocess
import unittest
from pathlib import Path

APP_DIR = Path(__file__).resolve().parents[1]
PLAYER = APP_DIR / 'player'
SHELL = PLAYER / 'shell'
PLAYBACK = APP_DIR / 'web' / 'js' / 'playback.js'
ASSEMBLE = PLAYER / 'assemble.mjs'
INDEX = APP_DIR / 'web' / 'index.html'
DOC = APP_DIR / 'docs' / 'player' / 'playback.md'
NODE = shutil.which('node')


def source():
    return PLAYBACK.read_text()


def code_only(text):
    """Statements only: a comment may name what is absent, code may not reach for it."""
    text = re.sub(r'/\*[\s\S]*?\*/', '', text)
    return re.sub(r'^\s*//.*$', '', text, flags=re.M)


class ModuleShape(unittest.TestCase):
    """One classic script, loaded by the shell page, with no build step and no dependency."""

    def test_it_is_a_classic_script_and_not_a_module(self):
        text = source()
        code = code_only(text)
        self.assertNotRegex(code, r'(?m)^\s*import\s')
        self.assertNotIn('require(', code)
        self.assertNotRegex(code, r'(?m)^\s*export\s')
        # The same guard the connect screen uses, so the file can be exercised outside a browser.
        self.assertIn('typeof window === "undefined" ? globalThis : window', text)
        self.assertIn('root.PlayerPlayback', text)

    def test_it_exposes_the_api_the_shell_and_the_tests_use(self):
        text = source()
        for name in ('attach', 'appQueue', 'metadataFor', 'thresholdSeconds', 'clampPosition',
                     'scrobbleUrl', 'artworkType'):
            self.assertIn(f'    {name}: {name},', text, f'{name} is not on the public API')

    def test_nothing_is_scheduled_on_a_timer(self):
        # This is the whole answer to a suspended WebView: if a decision waited on a timer, the
        # platform could freeze it and the player would resume into a queue of stale decisions.
        code = code_only(source())
        for call in ('setInterval', 'setTimeout', 'requestAnimationFrame',
                     'requestIdleCallback', 'queueMicrotask'):
            self.assertNotIn(call, code, f'{call} would not survive a suspension')
        self.assertIn('timeupdate', code, 'the position comes from the element, not a clock')
        self.assertIn('ended', code)

    def test_it_writes_nothing_to_storage(self):
        code = code_only(source())
        for call in ('localStorage', 'sessionStorage', 'indexedDB', 'document.cookie', 'setItem'):
            self.assertNotIn(call, code, f'{call} belongs to the app, not the playback layer')

    def test_it_never_loads_audio_itself(self):
        # A second `src` would be a second player; the app's own play path owns the resource.
        code = code_only(source())
        self.assertNotIn('audio.src', code)
        self.assertEqual(code.count('audio.currentTime ='), 1,
                         'the timeline is written in exactly one place: a seek')

    def test_it_claims_the_transport_and_refuses_the_seek_pair(self):
        text = source()
        code = code_only(text)
        claimed = re.search(r'const TRANSPORT = \[(.*?)\];', text, re.S)
        self.assertIsNotNone(claimed)
        actions = re.findall(r'"([a-z]+)"', claimed.group(1))
        self.assertEqual(actions, ['play', 'pause', 'previoustrack', 'nexttrack', 'seekto'])
        refused = re.search(r'const UNCLAIMED = \[(.*?)\];', text, re.S)
        self.assertEqual(re.findall(r'"([a-z]+)"', refused.group(1)),
                         ['seekbackward', 'seekforward'])
        # Cleared with null rather than claimed, and the reason is written down next to it.
        self.assertIn('session.setActionHandler(action, null)', code)
        self.assertIn('±10', text)

    def test_the_os_gets_the_four_fields_a_lock_screen_draws(self):
        text = source()
        for field in ('title:', 'artist:', 'album:', 'artwork:'):
            self.assertIn(field, text)
        self.assertIn('sizes: ARTWORK_SIZES', text)
        self.assertIn('type: artworkType(src)', text,
                      'WebKit draws no artwork entry without a declared type')


class Scrobbling(unittest.TestCase):
    """The protocol's endpoint, and the one point a play is owed at."""

    def test_it_reports_at_the_specs_endpoint(self):
        text = source()
        self.assertIn('/rest/scrobble.view', text)
        for parameter in ('submission:', 'time:', 'id:', 'u:', 'p:', 'v:', 'c:'):
            self.assertIn(parameter, text)
        self.assertIn('PROTOCOL_VERSION = "1.16.1"', text)
        self.assertIn('CLIENT_NAME = "hardlylistening-player"', text)

    def test_the_threshold_is_four_minutes_or_half_the_song(self):
        text = source()
        self.assertIn('SCROBBLE_AFTER = 240', text)
        self.assertIn('Math.min(SCROBBLE_AFTER, seconds / 2)', code_only(text))

    def test_a_start_notification_is_opt_in_and_says_why(self):
        # The server's `scrobble` records a play for every call it receives, whatever the
        # `submission` flag says — so announcing a start would count every song twice.
        text = source()
        self.assertIn('config.announce === true', text)
        self.assertIn('counts every call', text)
        self.assertIn('submission: false', text)

    def test_the_credentials_come_from_the_session_the_connect_screen_holds(self):
        text = source()
        self.assertIn('root.PlayerConnect', text)
        self.assertIn('connect.current()', text)
        # No second copy of the address and no password in the module's own state.
        self.assertNotIn('localStorage', code_only(text))
        self.assertIn('if (!session || !session.server || !session.credentials) return "";',
                      text)


class TheQueueItDrives(unittest.TestCase):
    """The app's queue, not a second one, and the names it depends on stay in `web/`."""

    def test_the_adapter_reads_the_apps_own_queue(self):
        code = code_only(source())
        for name in ('libQueue', 'playingId', 'playFromLibrary', 'stepTrack',
                     'commitPlaybackQueue'):
            self.assertIn(name, code, f'the adapter must reach the app\'s {name}')

    def test_it_never_mutates_the_queue_in_place(self):
        code = code_only(source())
        for call in ('libQueue.push', 'libQueue.splice', 'libQueue.unshift', 'libQueue.sort',
                     'libQueue.reverse'):
            self.assertNotIn(call, code, f'{call} would be a second owner of the order')
        assignments = re.findall(r'(?<![=!<>])libQueue\s*=(?!=)', code)
        self.assertEqual(len(assignments), 1,
                         'the order is replaced once, in setOrder, through the app\'s commit')
        self.assertIn('libQueue = order;', code)

    def test_web_still_offers_what_the_adapter_names(self):
        page = INDEX.read_text()
        for declaration in ('let libQueue', 'let playingId', 'let libAll',
                            'function playFromLibrary(', 'function stepTrack('):
            self.assertIn(declaration, page,
                          f'{declaration} is what the player adapter binds to; renaming it '
                          'breaks the player without breaking the app')
        # The queue is committed by the queue module, not by the page's inline script.
        queue = (APP_DIR / 'web' / 'js' / 'playback-queue.js').read_text()
        self.assertIn('function commitPlaybackQueue(', queue)

    def test_it_does_not_duplicate_the_players_transport_surface(self):
        # The element, the queue and the shuffle order are the app's; this module owns only the
        # media session, the scrobble and the position it publishes.
        code = code_only(source())
        for function in ('function playerToggle', 'function playerNext', 'function playerPrev',
                         'function togglePlaybackPanel'):
            self.assertNotIn(function, code)


class Documentation(unittest.TestCase):
    """The page a reader is sent to has to be true about this machine."""

    def test_the_document_exists_and_covers_the_os_surface(self):
        self.assertTrue(DOC.is_file(), 'docs/player/playback.md is the record of this card')
        text = DOC.read_text()
        self.assertIn('# Playback', text)
        for heading in ('What the OS gets', 'WebView', 'Unverified'):
            self.assertIn(heading, text, f'the doc has no "{heading}" section')
        for claim in ('scrobble', '240', 'lock screen', 'Bluetooth', 'seek', 'shuffle',
                      'repeat'):
            self.assertIn(claim, text)

    def test_it_says_what_actually_keeps_playing_when_the_page_is_hidden(self):
        text = DOC.read_text()
        self.assertIn('audio element', text)
        self.assertIn('timer', text)
        self.assertIn('suspend', text)

    def test_the_document_and_the_module_agree(self):
        doc = DOC.read_text()
        self.assertIn(str(240), doc)
        self.assertIn('submission', doc)
        self.assertIn('seekbackward', doc, 'the seek pair trade-off has to be written down')


class Ships(unittest.TestCase):
    """The module lives in the app's own `web/js/`, which both the browser and the player serve."""

    def test_the_module_is_in_the_app_tree(self):
        self.assertTrue(PLAYBACK.is_file())
        self.assertEqual(PLAYBACK.parent, APP_DIR / 'web' / 'js')
        self.assertFalse((SHELL / 'playback.js').exists(),
                         'the module moved to web/js; a copy in the shell would be a second one')

    def test_the_assembly_plan_carries_it(self):
        if not NODE:
            self.skipTest('node is not on PATH')
        result = subprocess.run([NODE, str(ASSEMBLE), '--list'], cwd=APP_DIR,
                                capture_output=True, text=True, timeout=120)
        self.assertEqual(result.returncode, 0, result.stderr)
        plan = json.loads(result.stdout)
        self.assertIn('js/playback.js', plan['web'])
        self.assertIn('js/playback.js', plan['assembled'] if 'assembled' in plan else plan['web'])
        self.assertNotIn('js/playback.js', [rel for rel, _ in plan['excluded']])


if __name__ == '__main__':
    unittest.main()