"""HOST-006: the window shows an address only when it is actually reachable.

The card is "show a usable address when Tailscale already provides one; do not manage
Tailscale", and most of the work is in the second half plus a third thing the card implies: a
LAN address is not automatically usable either. This file proves the detector,
`desktop/host/addresses.py`, does three things:

  * **Reads, never manages.** The only command it can run is `tailscale status --json` — the
    constant is asserted, the default reader refuses anything else, and every test injects a
    reader so no test (and no CI machine) ever executes the binary.
  * **Fails soft and fast.** No binary, a daemon that is down, a timeout, or JSON it cannot
    understand are four reported states, not four exceptions; and a loopback bind asks nothing
    at all, so a window on a machine with no Tailscale paints immediately.
  * **Says nothing rather than guess.** Every precedence chain ends in `primary is None` with a
    sentence explaining it, and a bind that is not reachable produces no offer — not
    `127.0.0.1` dressed up as a network address, and not the LAN address the machine happens to
    have.

The fixtures are JSON captured from the shape of `tailscale status --json` (BackendState, Self,
DNSName, TailscaleIPs) and interface lists, so the parsing is what is proven here. Nothing here
touches the network, starts a service, or writes a file.
"""
import importlib.util
import ipaddress
import json
import subprocess
import unittest
from pathlib import Path

APP_DIR = Path(__file__).resolve().parents[1]
MODULE_PATH = APP_DIR / 'desktop' / 'host' / 'addresses.py'


def load_addresses():
    spec = importlib.util.spec_from_file_location('desktop_host_addresses', MODULE_PATH)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


addresses = load_addresses()
Interface = addresses.Interface


def status(backend='Running', dnsname='studio.example.ts.net.', **extra):
    """A `tailscale status --json` body, in the shape the daemon actually prints."""
    payload = {
        'BackendState': backend,
        'Self': {
            'DNSName': dnsname,
            'TailscaleIPs': list(extra.pop('ips', ('100.101.102.103',
                                                  'fd7a:115c:a1e0::2'))),
            'Online': True,
        },
    }
    payload.update(extra)
    return json.dumps(payload)


class Reader:
    """A stand-in for `subprocess`: records what it was asked, answers with a fixture."""

    def __init__(self, result=None, error=None):
        self.result = result
        self.error = error
        self.calls = []

    def __call__(self, argv):
        self.calls.append(list(argv))
        if self.error is not None:
            raise self.error
        return self.result


def up_reader(dnsname='studio.example.ts.net.', ips=('100.101.102.103', 'fd7a:115c:a1e0::2')):
    return Reader((0, status(dnsname=dnsname, ips=ips), ''))


def down_reader():
    return Reader((1, '', 'failed to connect to local tailscaled; it does not appear to be running'))


def absent_reader():
    return Reader(error=FileNotFoundError(2, 'No such file or directory', 'tailscale'))


LAN = (Interface('wlp6s0', '192.168.1.40'),
       Interface('wlp6s0', '2601:db8:1::40'))


class ItOnlyEverReads(unittest.TestCase):
    def test_the_only_command_is_status(self):
        self.assertEqual(addresses.TAILSCALE_COMMAND, ('tailscale', 'status', '--json'))

    def test_the_default_reader_refuses_to_be_a_way_to_manage_the_tailnet(self):
        # The guard and the constant are the same decision, so the guard is executable rather
        # than a comment: `up`, `down`, `login` and `set` cannot be reached through this module.
        for argv in (('tailscale', 'up'), ('tailscale', 'down'), ('tailscale', 'login'),
                     ('tailscale', 'set', '--ssh'), ('tailscale', 'serve')):
            with self.assertRaises(ValueError):
                addresses._default_reader(argv)

    def test_detection_asks_tailscale_exactly_one_question(self):
        reader = up_reader()
        addresses.detect('0.0.0.0', interfaces=LAN, tailscale_reader=reader)
        self.assertEqual(reader.calls, [['tailscale', 'status', '--json']])

    def test_a_loopback_bind_asks_nothing_at_all(self):
        # Rule 2 and rule 1's speed: no reachable address exists, so no daemon is consulted and
        # a machine without Tailscale never waits on one.
        reader = up_reader()
        answer = addresses.detect('127.0.0.1', interfaces=LAN, tailscale_reader=reader)
        self.assertEqual(reader.calls, [])
        self.assertEqual(answer.tailscale, 'unread')

    def test_a_concrete_bind_asks_nothing_either(self):
        reader = up_reader()
        answer = addresses.detect('192.168.1.50', interfaces=LAN, tailscale_reader=reader)
        self.assertEqual(reader.calls, [])
        self.assertEqual(answer.primary.kind, 'bind')

    def test_the_probe_reads_the_interfaces_and_nothing_else(self):
        # A real probe, because it is the one path the fixtures cannot vouch for. It opens no
        # connection: a UDP `connect` only asks the kernel which source address it would use.
        found = addresses.probe_interfaces()
        self.assertIsInstance(found, tuple)
        for interface in found:
            ipaddress.ip_address(interface.address)
        self.assertEqual(addresses.detect('0.0.0.0', interfaces=found,
                                          tailscale_reader=absent_reader()).scope, 'any')


class TailscaleStates(unittest.TestCase):
    def test_a_running_tailnet_reports_its_name_and_addresses(self):
        state = addresses.tailscale_state(up_reader())
        self.assertEqual(state.state, 'up')
        self.assertEqual(state.magicdns, 'studio.example.ts.net')  # the trailing dot is DNS
        self.assertIn('100.101.102.103', state.addresses)
        self.assertIn('fd7a:115c:a1e0::2', state.addresses)

    def test_a_missing_binary_is_a_state_not_an_exception(self):
        state = addresses.tailscale_state(absent_reader())
        self.assertEqual(state.state, 'absent')
        self.assertIn('not installed', state.detail)

    def test_a_daemon_that_is_down_is_reported_as_down(self):
        state = addresses.tailscale_state(down_reader())
        self.assertEqual(state.state, 'down')
        self.assertIn('tailscaled', state.detail)

    def test_not_logged_in_is_down_not_up(self):
        for backend in ('NeedsLogin', 'Stopped', 'NoState', 'Starting'):
            state = addresses.tailscale_state(Reader((0, status(backend=backend), '')))
            self.assertEqual(state.state, 'down', backend)

    def test_a_timeout_is_unknown_and_does_not_escape(self):
        error = subprocess.TimeoutExpired(cmd=list(addresses.TAILSCALE_COMMAND), timeout=2)
        state = addresses.tailscale_state(Reader(error=error))
        self.assertEqual(state.state, 'unknown')
        self.assertIn('2 s', state.detail)

    def test_output_that_is_not_json_is_unknown(self):
        state = addresses.tailscale_state(Reader((0, 'tailscale is not a fish', '')))
        self.assertEqual(state.state, 'unknown')

    def test_json_that_is_not_an_object_is_unknown(self):
        state = addresses.tailscale_state(Reader((0, '["not", "an", "object"]', '')))
        self.assertEqual(state.state, 'unknown')

    def test_a_reader_that_reports_no_binary_at_all(self):
        self.assertEqual(addresses.tailscale_state(Reader(None)).state, 'absent')

    def test_a_running_tailnet_with_no_self_section_offers_nothing(self):
        body = json.dumps({'BackendState': 'Running'})
        state = addresses.tailscale_state(Reader((0, body, '')))
        self.assertEqual(state.state, 'up')
        self.assertEqual(state.addresses, ())
        self.assertEqual(state.magicdns, '')

    def test_a_literal_in_the_name_field_is_not_treated_as_a_name(self):
        state = addresses.tailscale_state(up_reader(dnsname='100.101.102.103'))
        self.assertEqual(state.magicdns, '')
        self.assertIn('100.101.102.103', state.addresses)


class Classification(unittest.TestCase):
    def kind(self, name, address):
        return addresses.classify(Interface(name, address))

    def test_the_shapes_that_are_never_offered(self):
        self.assertEqual(self.kind('lo', '127.0.0.1'), 'loopback')
        self.assertEqual(self.kind('lo', '::1'), 'loopback')
        self.assertEqual(self.kind('wlp6s0', '169.254.10.20'), 'link-local')
        self.assertEqual(self.kind('wlp6s0', 'fe80::1'), 'link-local')
        self.assertEqual(self.kind('docker0', '172.17.0.1'), 'virtual')
        self.assertEqual(self.kind('br-2e2fbf7e6619', '192.168.16.1'), 'virtual')
        self.assertEqual(self.kind('veth1234', '172.18.0.2'), 'virtual')
        self.assertEqual(self.kind('not-an-address', 'hello'), 'other')

    def test_carrier_nat_is_not_a_lan_address(self):
        # 100.64/10 is where Tailscale also lives. On a card that is not tailscale0 it is
        # somebody's carrier NAT and dialable by nobody on the local network.
        self.assertEqual(self.kind('wwan0', '100.64.5.6'), 'other')
        self.assertEqual(self.kind('tailscale0', '100.64.5.6'), 'tailscale')

    def test_tailnet_addresses_are_recognised_by_name_and_by_range(self):
        self.assertEqual(self.kind('tailscale0', '100.101.102.103'), 'tailscale')
        self.assertEqual(self.kind('utun0', 'fd7a:115c:a1e0::2'), 'tailscale')

    def test_ordinary_addresses_are_lan(self):
        self.assertEqual(self.kind('wlp6s0', '192.168.1.40'), 'lan')
        self.assertEqual(self.kind('enp5s0', '10.1.2.3'), 'lan')
        self.assertEqual(self.kind('wlp6s0', '2601:db8:1::40'), 'lan')


class BindAwareness(unittest.TestCase):
    def test_scope_of_every_shape_the_resolver_can_hand_over(self):
        self.assertEqual(addresses.scope_of('127.0.0.1'), 'loopback')
        self.assertEqual(addresses.scope_of('::1'), 'loopback')
        self.assertEqual(addresses.scope_of('[::1]'), 'loopback')
        self.assertEqual(addresses.scope_of('localhost'), 'loopback')
        self.assertEqual(addresses.scope_of('0.0.0.0'), 'any')
        self.assertEqual(addresses.scope_of('::'), 'any')
        self.assertEqual(addresses.scope_of(''), 'any')
        self.assertEqual(addresses.scope_of('*'), 'any')
        self.assertEqual(addresses.scope_of('192.168.1.50'), 'bind')

    def test_a_loopback_bind_offers_no_network_address_and_says_why(self):
        answer = addresses.detect('127.0.0.1', interfaces=LAN, tailscale_reader=up_reader())
        self.assertIsNone(answer.primary)
        self.assertEqual(answer.others, ())
        self.assertEqual(answer.scope, 'loopback')
        self.assertIn('this computer only', answer.note)
        # Not the LAN address the machine has, and not 127.0.0.1 dressed up as shareable.
        self.assertNotIn('192.168', json.dumps(answer.to_dict()))
        self.assertNotIn('127.0.0.1', json.dumps(answer.to_dict()['primary']))

    def test_a_concrete_bind_is_the_answer(self):
        answer = addresses.detect('192.168.1.50', interfaces=(), tailscale_reader=absent_reader())
        self.assertEqual(answer.primary.kind, 'bind')
        self.assertEqual(answer.primary.host, '192.168.1.50')
        self.assertEqual(answer.others, ())

    def test_a_bind_on_a_tailnet_address_needs_no_detection(self):
        # The tailnet address as an explicit bind is still a bind: it is what was asked for, and
        # nothing else about the machine's interfaces can change that.
        answer = addresses.detect('100.101.102.103', interfaces=LAN, tailscale_reader=up_reader())
        self.assertEqual(answer.primary.kind, 'bind')
        self.assertEqual(answer.primary.host, '100.101.102.103')
        self.assertEqual(answer.tailscale, 'unread')


class Precedence(unittest.TestCase):
    def test_a_tailnet_name_wins_over_its_own_literal_and_over_the_lan(self):
        answer = addresses.detect('0.0.0.0', interfaces=LAN, tailscale_reader=up_reader())
        self.assertEqual(answer.primary.kind, 'magicdns')
        self.assertEqual(answer.primary.host, 'studio.example.ts.net')
        kinds = [offer.kind for offer in answer.offers()]
        self.assertEqual(kinds, ['magicdns', 'tailscale', 'tailscale', 'lan', 'lan'])
        self.assertEqual(answer.others[0].host, '100.101.102.103')  # IPv4 before IPv6

    def test_a_nameless_tailnet_falls_back_to_its_literal(self):
        reader = up_reader(dnsname='')
        answer = addresses.detect('0.0.0.0', interfaces=LAN, tailscale_reader=reader)
        self.assertEqual(answer.primary.kind, 'tailscale')
        self.assertEqual(answer.primary.host, '100.101.102.103')

    def test_no_tailnet_falls_back_to_the_lan_address(self):
        answer = addresses.detect('0.0.0.0', interfaces=LAN, tailscale_reader=down_reader())
        self.assertEqual(answer.tailscale, 'down')
        self.assertEqual(answer.primary.kind, 'lan')
        self.assertEqual(answer.primary.host, '192.168.1.40')
        self.assertEqual(answer.note, '')

    def test_ipv4_is_preferred_over_ipv6(self):
        flipped = (Interface('wlp6s0', '2601:db8:1::40'), Interface('wlp6s0', '192.168.1.40'))
        answer = addresses.detect('0.0.0.0', interfaces=flipped,
                                  tailscale_reader=absent_reader())
        self.assertEqual(answer.primary.host, '192.168.1.40')
        self.assertEqual(answer.others[0].host, '2601:db8:1::40')

    def test_an_interface_with_only_ipv6_still_answers(self):
        answer = addresses.detect('::', interfaces=(Interface('wlp6s0', '2601:db8:1::40'),),
                                  tailscale_reader=absent_reader())
        self.assertEqual(answer.primary.host, '2601:db8:1::40')
        self.assertEqual(answer.primary.kind, 'lan')

    def test_container_bridges_and_loopback_are_not_offered(self):
        fixtures = (Interface('lo', '127.0.0.1'), Interface('docker0', '172.17.0.1'),
                    Interface('br-2e2fbf7e6619', '192.168.16.1'),
                    Interface('wlp6s0', '169.254.9.9'))
        answer = addresses.detect('0.0.0.0', interfaces=fixtures,
                                  tailscale_reader=absent_reader())
        self.assertIsNone(answer.primary)
        self.assertEqual(answer.others, ())
        self.assertIn('no network address', answer.note)

    def test_the_same_address_twice_is_offered_once(self):
        fixtures = (Interface('wlp6s0', '192.168.1.40'), Interface('', '192.168.1.40'))
        answer = addresses.detect('0.0.0.0', interfaces=fixtures,
                                  tailscale_reader=absent_reader())
        self.assertEqual([offer.host for offer in answer.offers()], ['192.168.1.40'])


class SilenceIsTheDefault(unittest.TestCase):
    def test_nothing_found_says_nothing_and_explains(self):
        answer = addresses.detect('0.0.0.0', interfaces=(), tailscale_reader=absent_reader())
        self.assertIsNone(answer.primary)
        self.assertIn('Tailscale is not installed', answer.note)
        self.assertIn('no network address', answer.note)
        self.assertEqual(answer.to_dict()['primary'], None)

    def test_a_daemon_that_cannot_be_asked_is_named_as_such(self):
        answer = addresses.detect('0.0.0.0', interfaces=(), tailscale_reader=down_reader())
        self.assertIn('not running', answer.note)
        self.assertEqual(answer.to_dict()['tailscale'], 'down')

    def test_a_running_tailnet_with_no_address_is_not_a_usable_answer(self):
        body = json.dumps({'BackendState': 'Running', 'Self': {}})
        answer = addresses.detect('0.0.0.0', interfaces=(), tailscale_reader=Reader((0, body, '')))
        self.assertIsNone(answer.primary)
        self.assertIn('reported no usable address', answer.note)

    def test_an_unreadable_daemon_is_not_called_down(self):
        answer = addresses.detect('0.0.0.0', interfaces=(),
                                  tailscale_reader=Reader((0, 'garbage', '')))
        self.assertEqual(answer.tailscale, 'unknown')
        self.assertIn('could not be asked', answer.note)


class TheShapeTheWindowReads(unittest.TestCase):
    def test_to_dict_is_json_safe_and_carries_every_key_the_ui_reads(self):
        answer = addresses.detect('0.0.0.0', interfaces=LAN, tailscale_reader=up_reader())
        payload = json.loads(json.dumps(answer.to_dict()))
        for key in ('bind', 'scope', 'tailscale', 'tailscale_detail', 'primary', 'others',
                    'note'):
            self.assertIn(key, payload)
        for key in ('host', 'kind', 'why'):
            self.assertIn(key, payload['primary'])
            self.assertTrue(payload['primary'][key])
        self.assertEqual([item['kind'] for item in payload['others']],
                         ['tailscale', 'tailscale', 'lan', 'lan'])

    def test_a_silent_answer_still_carries_its_note_and_a_null_primary(self):
        payload = addresses.detect('127.0.0.1', interfaces=LAN,
                                   tailscale_reader=up_reader()).to_dict()
        self.assertIsNone(payload['primary'])
        self.assertEqual(payload['others'], [])
        self.assertTrue(payload['note'])

    def test_the_module_writes_nothing_and_opens_no_server(self):
        text = MODULE_PATH.read_text()
        for forbidden in ('"w"', "'w'", 'write_text', 'write_bytes', 'os.remove', 'socket.bind',
                          'listen(', 'serve_forever'):
            self.assertNotIn(forbidden, text, f'{forbidden} does not belong in a detector')


class TheMainEntryPoint(unittest.TestCase):
    def test_it_prints_the_answer_as_json_and_exits_cleanly(self):
        import contextlib
        import io

        buffer = io.StringIO()
        with contextlib.redirect_stdout(buffer):
            code = addresses.main(['127.0.0.1'])
        self.assertEqual(code, 0)
        payload = json.loads(buffer.getvalue())
        self.assertEqual(payload['scope'], 'loopback')
        self.assertIsNone(payload['primary'])


if __name__ == '__main__':
    unittest.main()