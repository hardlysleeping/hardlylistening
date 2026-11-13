"""PLAYER-005: offline copies, and the filesystem rules under them.

The client half — which track is kept, the resume from a byte, the budget, the eviction order —
is exercised in `tests/test_player_offline.cjs` against a range-serving stub and a real temporary
directory. This file is the other half: the Rust module that owns the paths, and the agreement
between the two.

Four things are asserted here:

  * **The name rules, compiled.** `player/src-tauri/src/offline.rs` cannot be built on this
    machine (no `webkit2gtk-4.1`), but the block between its `pure rules` markers has no Tauri,
    no I/O and no network. It is extracted, compiled with `rustc --test` and run, so the
    sanitiser that a track id from the network goes through is executed rather than read. The
    same hostile table is asserted to be in the client test, so the two rules cannot drift into
    disagreeing about what a name is.
  * **Every command has a caller, and every caller a command.** The names `offline.js` invokes
    are the names the Rust module exports, both ways, so a half-renamed command is caught here
    rather than at runtime in a window nobody can open here.
  * **No path from the network.** Every `join` in the module takes a value that has been through
    `safe_component`/`account_name`/`file_name`, and a manifest entry's own `file` field is
    re-sanitised before it is used — a name that arrived as JSON is a claim, not permission.
  * **The store needs no capability.** The commands are the app's own, so `capabilities/*` still
    grants the core and nothing else: no filesystem plugin, no shell, no Rust-side HTTP.
"""
import json
import re
import shutil
import subprocess
import tempfile
import unittest
from pathlib import Path

APP_DIR = Path(__file__).resolve().parents[1]
PLAYER = APP_DIR / 'player'
OFFLINE_JS = PLAYER / 'shell' / 'offline.js'
OFFLINE_RS = PLAYER / 'src-tauri' / 'src' / 'offline.rs'
CAPABILITIES = PLAYER / 'src-tauri' / 'capabilities'
ASSEMBLE = PLAYER / 'assemble.mjs'
DOC = APP_DIR / 'docs' / 'player' / 'offline.md'

PURE_START = '// --- pure rules'
PURE_END = '// --- end pure rules'

#: Names the player will accept, and names it will not. The client test holds the same table; the
#: assertion below keeps them the same table.
ACCEPTED = ['abc123', 'a.b', 'track_1-2', 'A' * 64]
REFUSED = ['', '..', '.', '../etc/passwd', 'a/b', 'a\\b', 'C:evil.mp3', 'x\u0000y',
           '\u00e9t\u00e9', 'a' * 65, './x', '..\\..\\x', '/etc/passwd']

#: Every `join` in the Rust module. A new one is a new sentence about where a file may live, so it
#: has to be added here deliberately — that is the whole point of the list.
ALLOWED_JOINS = {
    'STORE_DIR',                                    # the store itself, a constant
    'account_name(account)',                        # the account directory, sanitised
    'MANIFEST_FILE',                                # a constant beside the copies
    'format!("{MANIFEST_FILE}{PART_EXT}")',         # the manifest's temporary, a constant
    '&name',                                        # from file_name(), sanitised
    'format!("{name}{PART_EXT}")',                  # the partial of a sanitised name
}

NODE = shutil.which('node')
RUSTC = shutil.which('rustc')
RUSTFMT = shutil.which('rustfmt')


def offline_source():
    return OFFLINE_RS.read_text()


def pure_block():
    """The part of the module that has no Tauri, no I/O and no network, ready to compile alone."""
    source = offline_source()
    if PURE_START not in source or PURE_END not in source:
        raise AssertionError('offline.rs lost its `pure rules` markers; the tests slice them')
    start = source.index(PURE_START)
    start = source.index('\n', start) + 1
    end = source.index(PURE_END)
    return source[start:end]


def rust_literal(value):
    out = []
    for character in value:
        code = ord(character)
        if character in '"\\':
            out.append('\\' + character)
        elif 32 <= code < 127:
            out.append(character)
        else:
            out.append('\\u{%x}' % code)
    return '"' + ''.join(out) + '"'


def rust_list(values):
    return '[' + ', '.join(rust_literal(value) for value in values) + ']'


def rust_harness():
    """The pure block plus the table, as one crate `rustc --test` can build."""
    return '''#![allow(dead_code)]
use std::path::{Path, PathBuf};

''' + pure_block() + '''
#[cfg(test)]
mod rules {
    use super::*;

    #[test]
    fn incoming_names_are_an_allow_list() {
        for good in ''' + rust_list(ACCEPTED) + ''' {
            assert_eq!(safe_component(good).as_deref(), Some(good), "{good:?} was refused");
        }
        for bad in ''' + rust_list(REFUSED) + ''' {
            assert!(safe_component(bad).is_none(), "{bad:?} was accepted as a file name");
        }
    }

    #[test]
    fn a_suffix_is_a_hint_and_never_a_path() {
        assert_eq!(safe_suffix("../x"), "x");
        assert_eq!(safe_suffix("../../etc/passwd"), "bin");
        assert_eq!(safe_suffix(".MP3"), "mp3");
        assert_eq!(safe_suffix(""), "bin");
        assert_eq!(file_name("abc", "mp3").as_deref(), Some("abc.mp3"));
        assert!(file_name("..", "mp3").is_none());
        assert!(file_name("a/b", "mp3").is_none());
    }

    #[test]
    fn an_account_is_one_legible_directory() {
        let name = account_name("https://music.example.com|bazzite");
        assert!(name.chars().all(|c| c.is_ascii_lowercase() || c.is_ascii_digit()
            || c == '-' || c == '_'), "{name}");
        assert!(!name.starts_with('.'));
        assert_eq!(name, account_name("https://music.example.com|bazzite"));
        assert_ne!(account_name("https://a/b|c"), account_name("https://a_b|c"));
        assert!(account_name("").starts_with("account-"));
        assert!(account_name("\\u{1F600}\\u{1F600}").starts_with("account-"));
    }

    #[test]
    fn a_path_is_one_entry_inside_the_directory() {
        let dir = Path::new("/srv/offline/bazzite-0123456789abcdef");
        assert!(inside(dir, &dir.join("track.mp3")));
        assert!(inside(dir, &dir.join("track.mp3.part")));
        assert!(!inside(dir, Path::new("/etc/passwd")));
        assert!(!inside(dir, &dir.join("a/b")));
        assert!(!inside(dir, &dir.join("..")));
        assert!(!inside(dir, &dir.join(".")));
        assert!(!inside(dir, dir));
    }

    #[test]
    fn a_chunk_survives_the_wire() {
        let bytes: Vec<u8> = (0u8..=255).collect();
        assert_eq!(b64_decode(&b64_encode(&bytes)).as_deref(), Some(&bytes[..]));
        assert_eq!(b64_encode(b"hello"), "aGVsbG8=");
        assert_eq!(b64_encode(&[1, 2]), "AQI=");
        assert_eq!(b64_decode("").map(|value| value.len()), Some(0));
        assert!(b64_decode("a*b").is_none());
    }

    #[test]
    fn displayed_text_loses_control_characters() {
        assert_eq!(sanitise_text("a\\u{0}b\\nc", 10), "abc");
        assert_eq!(sanitise_text(&"x".repeat(50), 4), "xxxx");
    }
}
'''


class TheNameRules(unittest.TestCase):
    """The Rust sanitiser is compiled and run, not read."""

    def test_the_pure_block_has_no_tauri_and_no_io(self):
        block = pure_block()
        for forbidden in ('tauri', 'AppHandle', 'serde_json', 'json!', 'fs::', 'PathBuf::from',
                          'Value::'):
            self.assertNotIn(forbidden, block,
                             f'{forbidden} in the pure block: it can no longer be compiled alone')

    def test_the_names_and_the_decoder_pass(self):
        if not RUSTC:
            self.skipTest('rustc is not on PATH')
        with tempfile.TemporaryDirectory() as tmp:
            source = Path(tmp) / 'rules.rs'
            binary = Path(tmp) / 'rules-test'
            source.write_text(rust_harness())
            build = subprocess.run([RUSTC, '--edition', '2021', '--test', str(source),
                                    '-o', str(binary)],
                                   capture_output=True, text=True, timeout=180)
            self.assertEqual(build.returncode, 0,
                             f'rustc refused the pure block:\n{build.stderr}')
            run = subprocess.run([str(binary)], capture_output=True, text=True, timeout=120)
            self.assertEqual(run.returncode, 0, run.stdout + run.stderr)
            self.assertIn('test result: ok', run.stdout)

    def test_rustfmt_accepts_the_module(self):
        if not RUSTFMT:
            self.skipTest('rustfmt is not on PATH')
        result = subprocess.run([RUSTFMT, '--edition', '2021', '--check', str(OFFLINE_RS)],
                                capture_output=True, text=True, timeout=120)
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)

    def test_the_client_holds_the_same_table(self):
        client = (PLAYER / 'shell' / 'offline.js').read_text()
        for name in ('safeComponent', 'safeSuffix', 'accountName'):
            self.assertIn(name, client, f'{name} left the client mirror')
        test = (APP_DIR / 'tests' / 'test_player_offline.cjs').read_text()
        for name in ('../etc/passwd', 'C:evil.mp3', '/etc/passwd', '"a/b"', '".."'):
            self.assertIn(name, test, f'the client test no longer covers {name!r}')


class TheCommands(unittest.TestCase):
    """The two halves name the same commands, and no path is built from a raw string."""

    @classmethod
    def setUpClass(cls):
        cls.rust = offline_source()
        cls.client = OFFLINE_JS.read_text()

    def test_the_client_and_the_module_agree_on_every_command(self):
        invoked = sorted(set(re.findall(r'"(offline_[a-z]+)"', self.client)))
        exported = sorted(set(re.findall(r'fn (offline_[a-z]+)\(', self.rust)))
        self.assertTrue(invoked, 'offline.js invokes nothing')
        self.assertEqual(invoked, exported,
                         'a command with no caller, or a caller with no command')

    def test_every_join_takes_a_sanitised_value(self):
        source = self.rust
        found = []
        for match in re.finditer(r'\.join\(', source):
            cursor = match.end()
            depth = 1
            argument = []
            while cursor < len(source) and depth:
                character = source[cursor]
                if character == '(':
                    depth += 1
                elif character == ')':
                    depth -= 1
                    if not depth:
                        break
                argument.append(character)
                cursor += 1
            found.append(''.join(argument).strip())
        self.assertTrue(found, 'no join in offline.rs: the path rules moved')
        unknown = [argument for argument in found if argument not in ALLOWED_JOINS]
        self.assertEqual(unknown, [],
                         'a new join must be deliberate: add it to ALLOWED_JOINS with the '
                         'sanitiser that produced its argument')

    def test_a_stored_name_is_never_used_unchecked(self):
        self.assertIn('fn entry_file(', self.rust)
        self.assertIn('safe_component(stored)', self.rust,
                      'the manifest\'s own file name has to be re-sanitised on use')
        self.assertIn('if !inside(dir, &path)', self.rust)
        # The entry a commit writes is rebuilt from known fields, never copied from the request.
        self.assertIn('fn clean_entry(', self.rust)
        self.assertIn('"file": file,', self.rust)

    def test_a_short_file_cannot_be_committed(self):
        self.assertIn('if declared == 0 || held != declared {', self.rust)

    def test_a_write_cannot_leave_a_gap(self):
        self.assertIn('if offset != held {', self.rust)

    def test_the_capability_still_grants_only_the_core(self):
        files = sorted(CAPABILITIES.glob('*.json'))
        self.assertTrue(files, 'no capability file means no window can load')
        for path in files:
            capability = json.loads(path.read_text())
            for permission in capability['permissions']:
                self.assertTrue(permission.startswith('core:'),
                                f'{path.name} grants {permission}; the store is the app\'s own '
                                'commands and needs no plugin permission')
            self.assertNotIn('offline', path.read_text().lower(),
                             f'{path.name} names the store: commands are not capabilities')


class ItShips(unittest.TestCase):
    def test_the_offline_module_is_assembled(self):
        self.assertTrue(OFFLINE_JS.is_file())
        if not NODE:
            self.skipTest('node is not on PATH')
        result = subprocess.run([NODE, str(ASSEMBLE), '--list'], cwd=APP_DIR,
                                capture_output=True, text=True, timeout=120)
        self.assertEqual(result.returncode, 0, result.stderr)
        plan = json.loads(result.stdout)
        self.assertIn('offline.js', plan['shell'],
                      'the shell directory is what ships; offline.js has to be in it')
        self.assertFalse(any(name.endswith('.py') for name in plan['shell']))


class TheDoc(unittest.TestCase):
    """A person operating this needs the rules stated, not implied."""

    @classmethod
    def setUpClass(cls):
        cls.text = DOC.read_text()

    def test_it_exists_beside_the_player_docs(self):
        self.assertTrue(DOC.is_file(), DOC)
        self.assertIn('PLAYER-005', self.text)

    def test_it_states_the_rules_the_code_enforces(self):
        for phrase in ('oldest', 'playing', 'Range', 'ETag', 'Content-Length', 'budget',
                       'rename', 'manifest', 'sanitis', 'truncat'):
            self.assertIn(phrase.lower(), self.text.lower(), f'the doc never says {phrase!r}')

    def test_it_is_honest_about_what_is_unverified(self):
        self.assertIn('unverified', self.text.lower())
        self.assertIn('webkit2gtk', self.text)


if __name__ == '__main__':
    unittest.main()