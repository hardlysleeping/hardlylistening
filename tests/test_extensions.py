"""The extension host's bookkeeping, which is where the security actually lives.

Nothing here loads or executes extension code — this module has no such surface yet — so the tests
are about grants: what a manifest may ask for, what an install grants (nothing), what an
enable/revoke/remove actually changes, and the two refusals the plan calls out by name (acquisition
is not a capability; a typo fails the manifest instead of shrinking it).
"""
import json
import os
import tempfile
import unittest
from pathlib import Path

import extensions
from db import Store


MANIFEST = {
    'id': 'example.shows', 'name': 'Example', 'version': '1.0.0',
    'description': 'A test extension', 'entry': 'main.py',
    'capabilities': ['library.read', 'network'], 'hosts': ['example.org'],
}


class Host(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory(dir=os.environ.get('TMPDIR', '/tmp'))
        self.addCleanup(self.tmp.cleanup)
        self.store = Store(Path(self.tmp.name) / 'x.sqlite')
        self.addCleanup(lambda: self.store.conn.close())

    def test_an_install_grants_nothing(self):
        extensions.install(self.store, MANIFEST, by='admin-1')
        record = extensions.installed(self.store)[0]
        self.assertEqual(record['id'], 'example.shows')
        self.assertFalse(record['enabled'], 'installing is not trusting')
        self.assertEqual(record['capabilities'], [],
                         'a disabled extension holds no capability, whatever it asked for')
        self.assertEqual(record['requested'], ['library.read', 'network'])
        self.assertFalse(extensions.may(self.store, 'example.shows', 'library.read'))

    def test_enabling_needs_an_administrator_and_records_who(self):
        extensions.install(self.store, MANIFEST)
        with self.assertRaises(extensions.ExtensionError):
            extensions.enable(self.store, 'example.shows', by='')
        extensions.enable(self.store, 'example.shows', by='admin-1')
        record = extensions.installed(self.store)[0]
        self.assertTrue(record['enabled'])
        self.assertEqual(record['trusted_by'], 'admin-1')
        self.assertGreater(record['trusted_at'], 0)
        self.assertEqual(extensions.granted(self.store, 'example.shows'),
                         {'library.read', 'network'})
        self.assertTrue(extensions.may(self.store, 'example.shows', 'network'))
        self.assertFalse(extensions.may(self.store, 'example.shows', 'social.write'))

    def test_a_revocation_takes_every_grant_back_at_once(self):
        extensions.install(self.store, MANIFEST, by='admin-1')
        extensions.enable(self.store, 'example.shows', by='admin-1')
        extensions.revoke(self.store, 'example.shows')
        record = extensions.installed(self.store)[0]
        self.assertFalse(record['enabled'])
        self.assertTrue(record['revoked'])
        self.assertEqual(record['capabilities'], [])
        self.assertEqual(extensions.granted(self.store, 'example.shows'), set())
        self.assertFalse(extensions.may(self.store, 'example.shows', 'library.read'))

    def test_a_revoked_extension_is_not_revived_by_enabling_it(self):
        extensions.install(self.store, MANIFEST, by='admin-1')
        extensions.revoke(self.store, 'example.shows')
        with self.assertRaises(extensions.ExtensionError) as caught:
            extensions.enable(self.store, 'example.shows', by='admin-1')
        self.assertIn('reinstall', str(caught.exception))

    def test_remove_forgets_it_entirely(self):
        extensions.install(self.store, MANIFEST, by='admin-1')
        extensions.remove(self.store, 'example.shows')
        self.assertEqual(extensions.installed(self.store), [])
        with self.assertRaises(extensions.ExtensionError):
            extensions.granted(self.store, 'example.shows')

    # -- what a manifest may ask for -----------------------------------------
    def test_acquisition_is_not_a_capability_the_host_can_grant(self):
        # The plan is explicit: membership, player access and administrator status must never
        # imply acquisition. Refusing the word at install time is what keeps it that way.
        bad = dict(MANIFEST, capabilities=['acquisition'])
        with self.assertRaises(extensions.ExtensionError) as caught:
            extensions.install(self.store, bad)
        self.assertIn('per-member permission', str(caught.exception))
        self.assertEqual(extensions.installed(self.store), [])

    def test_an_unknown_capability_fails_the_manifest_rather_than_being_dropped(self):
        bad = dict(MANIFEST, capabilities=['library.read', 'library.reed'])
        with self.assertRaises(extensions.ExtensionError) as caught:
            extensions.validate(bad)
        self.assertIn('library.reed', str(caught.exception))

    def test_network_hosts_must_be_declared_with_the_network_capability(self):
        bad = dict(MANIFEST, capabilities=['library.read'])
        with self.assertRaises(extensions.ExtensionError):
            extensions.validate(bad)

    def test_a_manifest_cannot_name_a_path_outside_itself(self):
        for entry in ('../other/main.py', '/usr/bin/python3', 'a/../../b.py'):
            with self.assertRaises(extensions.ExtensionError, msg=entry):
                extensions.validate(dict(MANIFEST, entry=entry))

    def test_an_id_is_a_slug_and_a_version_is_semantic(self):
        for bad in ('Example', 'a', 'x/y', '', '../x'):
            with self.assertRaises(extensions.ExtensionError, msg=bad):
                extensions.validate(dict(MANIFEST, id=bad))
        for bad in ('1.0', 'v1.0.0', '', 'latest'):
            with self.assertRaises(extensions.ExtensionError, msg=bad):
                extensions.validate(dict(MANIFEST, version=bad))

    def test_an_extension_that_asks_for_nothing_is_allowed(self):
        # A manifest is not required to want anything; it simply cannot then reach anything.
        clean = extensions.validate({'id': 'quiet.one', 'name': 'Quiet', 'version': '0.1.0'})
        self.assertEqual(clean['capabilities'], [])
        extensions.install(self.store, clean, by='admin-1')
        self.assertEqual(extensions.granted(self.store, 'quiet.one'), set())

    def test_an_unknown_manifest_schema_is_refused(self):
        with self.assertRaises(extensions.ExtensionError):
            extensions.validate(dict(MANIFEST, schema=99))

    def test_reinstalling_the_same_version_is_refused_and_a_new_one_replaces_it(self):
        extensions.install(self.store, MANIFEST, by='admin-1')
        with self.assertRaises(extensions.ExtensionError):
            extensions.install(self.store, MANIFEST, by='admin-1')
        newer = dict(MANIFEST, version='1.1.0', capabilities=['library.read'], hosts=[])
        extensions.install(self.store, newer, by='admin-1')
        record = extensions.installed(self.store)[0]
        self.assertEqual(record['version'], '1.1.0')
        self.assertEqual(record['requested'], ['library.read'])
        self.assertFalse(record['enabled'], 'a replacement is a new decision, not a kept grant')

    def test_an_unknown_capability_is_an_error_not_a_false(self):
        extensions.install(self.store, MANIFEST, by='admin-1')
        extensions.enable(self.store, 'example.shows', by='admin-1')
        with self.assertRaises(extensions.ExtensionError):
            extensions.may(self.store, 'example.shows', 'teleport')

    def test_acquisition_is_never_granted_even_if_the_word_is_asked(self):
        extensions.install(self.store, MANIFEST, by='admin-1')
        extensions.enable(self.store, 'example.shows', by='admin-1')
        self.assertFalse(extensions.may(self.store, 'example.shows', 'acquisition'))

    def test_the_stored_manifest_round_trips(self):
        extensions.install(self.store, MANIFEST, by='admin-1')
        stored = extensions.manifest_of(self.store, 'example.shows')
        self.assertEqual(stored, extensions.validate(MANIFEST))
        self.assertEqual(json.loads(json.dumps(stored)), stored)


if __name__ == '__main__':
    unittest.main()