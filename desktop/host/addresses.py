"""Which address this machine can offer — and silence when it cannot.

HOST-006. `resolve.Address` answers *where the server listens*; this module answers the
neighbour question: **what can a person type on another device to reach it?** The two are not
the same question, which is the whole card: a server bound to `127.0.0.1` has interfaces, has a
LAN address, and is still not reachable from the phone in the kitchen.

Three rules, in the order they constrain the code:

1. **Detection is read-only.** LAN addresses come from the interfaces this machine already has.
   The Tailscale address is read from `tailscale status --json` and from nothing else: this
   module never runs `up`, `down`, `login` or `set`, never writes a preference, never touches
   the tailnet. A binary that is not installed and a daemon that is not running are *facts it
   reports*, not errors it raises — and the call is skipped altogether when the bind already
   settled the question (rule 2), so a loopback window never waits on a wedged daemon.
2. **The bind decides usability; the interface list only supplies candidates.** Zero or six LAN
   addresses is equally irrelevant to a server bound to loopback. Offering `192.168.x.x` for it
   would be a guess about a server that is not listening there; offering `127.0.0.1` as if it
   were shareable is the same lie in the other direction. Both end in `primary = None` plus a
   sentence saying why.
3. **Say nothing rather than guess.** The precedence below ends in `None`, and the window shows
   no address at all. There is no "closest match", no first-element fallback, no default LAN
   guess.

Precedence, for a server bound to every interface (`0.0.0.0`, `::`, `*` or nothing):

  1. the bound address itself, when it is concrete and dialable ......... kind `bind`
  2. the Tailscale MagicDNS name, when Tailscale is already up .......... kind `magicdns`
  3. the Tailscale `100.x` address, when it is up but has no name ....... kind `tailscale`
  4. this machine's own network address, IPv4 first ..................... kind `lan`
  5. nothing ............................................................ `primary is None`

A name beats its literal because a tailnet name survives the address changing, which is what
happens whenever a device is re-keyed. Tailscale beats the LAN address because it reaches every
device the person already signed in — on the same wifi too, where the connection is direct — so
it is the more useful of two working answers. The LAN address is what remains when there is no
tailnet, which is the common case and must not need Tailscale at all.

Nothing here listens, binds, or starts a process. `tests/test_host_addresses.py` injects both
readers, so no test and no CI machine ever runs `tailscale`.
"""
from __future__ import annotations

import ipaddress
import json
import subprocess
import sys
from dataclasses import dataclass
from typing import Callable, Iterable, Sequence

#: The server's documented default bind (`config.py`), repeated for the same reason
#: `resolve.py` repeats it: this package imports no server module.
DEFAULT_HOST = '127.0.0.1'

#: The only external command this module will ever run. `status` reads the daemon's own view of
#: the world; it changes nothing. There is deliberately no second command constant.
TAILSCALE_COMMAND = ('tailscale', 'status', '--json')

#: A window must paint without waiting on a wedged daemon. Past this, Tailscale is `unknown` and
#: the precedence falls through to the LAN, which needs no daemon at all.
TIMEOUT = 2.0

#: Closed on purpose (`subprocess.run(..., timeout=)`), and a UDP connect sends no packet: the
#: routing table is consulted and nothing leaves the machine.
_ROUTE_PROBES = ((4, '203.0.113.1'), (6, '2001:db8::1'))

_CGNAT = ipaddress.ip_network('100.64.0.0/10')
#: Tailscale's own IPv6 range, so a tailnet address is recognised even if the interface is not
#: named the way Linux names it.
_TAILSCALE_ULA = ipaddress.ip_network('fd7a:115c:a1e0::/48')

_ANY = {'', '*', '0.0.0.0', '::', '[::]'}
_LOOPBACK = {'127.0.0.1', 'localhost', '::1', '[::1]'}

#: Interfaces that are not a route from another device to this one. A container bridge answers
#: `ip addr` exactly like a network card does, and handing `172.17.0.1` to a phone is a bug.
_VIRTUAL_PREFIXES = ('docker', 'br-', 'veth', 'virbr', 'vnet', 'vmnet', 'vboxnet', 'podman',
                     'cni', 'flannel', 'kube', 'lxc', 'lxd', 'tun', 'tap', 'wg', 'zt')

#: `tailscale status --json` reports one of these in `BackendState`.
_TAILSCALE_UP = 'Running'


@dataclass(frozen=True)
class Interface:
    """One address a network interface says it has. Read-only knowledge, nothing more."""

    name: str
    address: str


@dataclass(frozen=True)
class Tailscale:
    """Tailscale's answer about itself: `up`, `down`, `absent`, `unknown`, or `unread`.

    `unread` is not a failure: it means the bind made the question pointless, so no command was
    run. `unknown` means one was run and could not be understood — different fact, different
    sentence in the window.
    """

    state: str = 'unknown'
    magicdns: str = ''
    addresses: tuple[str, ...] = ()
    detail: str = ''


@dataclass(frozen=True)
class Offer:
    """One address worth putting in front of a person, and the reason it is that one."""

    host: str
    kind: str  # 'bind' | 'magicdns' | 'tailscale' | 'lan'
    why: str


@dataclass(frozen=True)
class Answer:
    """What the window may show, or the plain reason it shows nothing.

    `primary is None` is the honest answer more often than one would like: no interfaces, no
    tailnet, or a bind that is not reachable at all. `note` then carries the sentence a person
    reads instead of a plausible-looking address.
    """

    bind: str
    scope: str  # 'loopback' | 'any' | 'bind'
    primary: Offer | None
    others: tuple[Offer, ...] = ()
    tailscale: str = 'unread'
    note: str = ''
    tailscale_detail: str = ''

    def offers(self) -> tuple[Offer, ...]:
        return (self.primary,) + self.others if self.primary else self.others

    def to_dict(self) -> dict:
        """JSON-safe, in the shape `desktop/ui/address.js` reads. Tuples are lists here."""
        return {
            'bind': self.bind,
            'scope': self.scope,
            'tailscale': self.tailscale,
            'tailscale_detail': self.tailscale_detail,
            'primary': _offer_dict(self.primary),
            'others': [_offer_dict(offer) for offer in self.others],
            'note': self.note,
        }


def _offer_dict(offer: Offer | None) -> dict | None:
    if offer is None:
        return None
    return {'host': offer.host, 'kind': offer.kind, 'why': offer.why}


def normalize(host: str | None) -> str:
    """The bind host as a comparison key: no brackets, no stray whitespace."""
    text = (host or '').strip()
    if text.startswith('[') and text.endswith(']'):
        text = text[1:-1]
    if text == '*':
        return ''
    return text


def _ip(text: str):
    """An `ipaddress` object for a bare literal, or `None`. Never resolves a name."""
    try:
        return ipaddress.ip_address(str(text).strip().strip('[]'))
    except ValueError:
        return None


def scope_of(host: str | None) -> str:
    """What the bind says about reachability: `loopback`, `any` or a concrete `bind`."""
    text = normalize(host)
    if text in _ANY:
        return 'any'
    ip = _ip(text)
    if ip is not None and ip.is_loopback:
        return 'loopback'
    if text.lower() in _LOOPBACK:
        return 'loopback'
    return 'bind'


def classify(interface: Interface) -> str:
    """`loopback`, `link-local`, `tailscale`, `virtual`, `lan` or `other`.

    Only `tailscale` and `lan` are ever offered as a network address; the rest exist so a
    reader can see *why* an interface was passed over instead of wondering.
    """
    ip = _ip(interface.address)
    if ip is None:
        return 'other'
    if ip.is_loopback:
        return 'loopback'
    if ip.is_link_local:
        return 'link-local'
    name = interface.name.lower()
    if name.startswith('tailscale'):
        return 'tailscale'
    if ip.version == 6 and ip in _TAILSCALE_ULA:
        return 'tailscale'
    # Carrier NAT and container bridges are private by every definition and reachable by
    # nobody: `is_private` is checked after the two shapes that only look local.
    if ip.version == 4 and ip in _CGNAT:
        return 'other'
    if name.startswith(_VIRTUAL_PREFIXES):
        return 'virtual'
    if ip.is_private:
        return 'lan'
    if ip.is_global:
        return 'lan'
    return 'other'


def probe_interfaces() -> tuple[Interface, ...]:
    """The interfaces this machine already has. No command, no socket that sends anything.

    On Linux every name `if_nameindex` lists is asked for its IPv4 address with `SIOCGIFADDR`;
    elsewhere that ioctl number is a different call, the request fails and is skipped, and the
    routed address is still found, because a UDP `connect` only fills in the kernel's chosen
    source address. Names are cosmetic: an empty name is not a reason to drop an address.
    """
    found: dict[str, Interface] = {}
    for interface in _posix_interfaces():
        found.setdefault(interface.address, interface)
    for interface in _routed_addresses():
        found.setdefault(interface.address, interface)
    return tuple(found.values())


def _posix_interfaces() -> Iterable[Interface]:
    try:
        import fcntl
        import socket
        import struct
    except ImportError:  # Windows: no ioctl, the routed address is still probed below.
        return
    SIOCGIFADDR = 0x8915
    try:
        names = [name for _, name in socket.if_nameindex()]
    except (AttributeError, OSError):
        return
    for name in names:
        try:
            with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as sock:
                request = struct.pack('256s', name[:15].encode('utf-8'))
                answer = fcntl.ioctl(sock.fileno(), SIOCGIFADDR, request)
                yield Interface(name, socket.inet_ntoa(answer[20:24]))
        except OSError:
            continue


def _routed_addresses() -> Iterable[Interface]:
    import socket

    for version, probe in _ROUTE_PROBES:
        family = socket.AF_INET if version == 4 else socket.AF_INET6
        try:
            with socket.socket(family, socket.SOCK_DGRAM) as sock:
                sock.settimeout(TIMEOUT)
                sock.connect((probe, 9))
                address = sock.getsockname()[0]
        except OSError:
            continue
        yield Interface('', str(address).split('%')[0])


def _default_reader(argv: Sequence[str]) -> tuple[int, str, str]:
    """Run one read-only command and hand back what it said.

    The command is fixed by `TAILSCALE_COMMAND` and checked again here, so a caller cannot turn
    this module into a way to reach `tailscale up`. A missing binary raises `FileNotFoundError`
    and a wedged daemon raises `TimeoutExpired`; both are handled by `tailscale_state`.
    """
    if tuple(argv) != TAILSCALE_COMMAND:
        raise ValueError(f'refusing to run {list(argv)}: only {list(TAILSCALE_COMMAND)} is read')
    done = subprocess.run(list(argv), capture_output=True, text=True,
                          timeout=TIMEOUT, stdin=subprocess.DEVNULL)
    return done.returncode, done.stdout, done.stderr


def tailscale_state(reader: Callable[[Sequence[str]], tuple[int, str, str] | None] | None = None
                    ) -> Tailscale:
    """Ask Tailscale about itself, and never make the window's problem worse for the asking.

    Every failure mode is a state, not an exception: not installed, not running, not
    understood, or timed out. `tests/test_host_addresses.py` always injects the reader, so the
    test suite never executes the binary — the seam exists so the *parsing* is what is proven.
    """
    read = _default_reader if reader is None else reader
    try:
        result = read(TAILSCALE_COMMAND)
    except FileNotFoundError:
        return Tailscale('absent', detail='tailscale is not installed')
    except subprocess.TimeoutExpired:
        return Tailscale('unknown', detail=f'`{" ".join(TAILSCALE_COMMAND)}` did not answer in '
                                           f'{TIMEOUT:g} s')
    except OSError as error:
        return Tailscale('unknown', detail=str(error.strerror or error))
    except Exception as error:  # a reader is a seam; a broken one is a fact, not a crash
        return Tailscale('unknown', detail=str(error) or type(error).__name__)

    if result is None:  # a reader may report "no binary" instead of raising
        return Tailscale('absent', detail='tailscale is not installed')

    code, out, err = result
    payload = _status_json(out)
    if payload is None:
        detail = _first_line(err) or _first_line(out)
        if code != 0:
            # The normal shape of "the daemon is not running" or "not logged in": a non-zero
            # exit and a sentence on stderr. It is down, not unknown — nothing to fall back to.
            return Tailscale('down', detail=detail or f'tailscale exited {code}')
        return Tailscale('unknown', detail=detail or 'tailscale printed no status')
    return _status_state(payload)


def _status_json(text: str) -> dict | None:
    try:
        payload = json.loads(text or '')
    except (ValueError, UnicodeDecodeError):
        return None
    return payload if isinstance(payload, dict) else None


def _first_line(text: str) -> str:
    for line in (text or '').strip().splitlines():
        if line.strip():
            return line.strip()
    return ''


def _status_state(payload: dict) -> Tailscale:
    backend = str(payload.get('BackendState') or '').strip()
    if backend != _TAILSCALE_UP:
        return Tailscale('down', detail=f'Tailscale is {backend or "not reporting a state"}')

    self_node = payload.get('Self')
    if not isinstance(self_node, dict):
        return Tailscale('up', detail='Tailscale is running but reported no address')
    magicdns = str(self_node.get('DNSName') or '').strip().rstrip('.')
    if _ip(magicdns) is not None or ' ' in magicdns or '/' in magicdns:
        magicdns = ''  # a literal or garbage is not a name; fall back to the address
    addresses = tuple(str(value) for value in (self_node.get('TailscaleIPs') or [])
                      if _ip(value) is not None)
    if not magicdns and not addresses:
        return Tailscale('up', detail='Tailscale is running but reported no address')
    return Tailscale('up', magicdns=magicdns, addresses=addresses)


def _literal_rank(address: str) -> tuple:
    ip = _ip(address)
    if ip is None:
        return (9, 9, address)
    return (0 if ip.version == 4 else 1, 0 if ip.is_private else 1, str(ip))


def _interface_rank(interface: Interface) -> tuple:
    return _literal_rank(interface.address) + (interface.name,)


def _dedupe(offers: Iterable[Offer]) -> tuple[Offer, ...]:
    seen: set[str] = set()
    kept = []
    for offer in offers:
        key = normalize(offer.host).lower()
        if not key or key in seen:
            continue
        seen.add(key)
        kept.append(offer)
    return tuple(kept)


def _nothing_note(tailscale: Tailscale) -> str:
    reason = {
        'absent': 'Tailscale is not installed',
        'down': 'Tailscale is not running',
        'unknown': 'Tailscale could not be asked for its address',
        'up': 'Tailscale is running but reported no usable address',
        'unread': 'Tailscale was not asked',
    }.get(tailscale.state, 'Tailscale could not be asked for its address')
    return f'{reason}, and this computer has no network address to offer.'


def detect(host: str | None = DEFAULT_HOST, *,
           interfaces: Iterable[Interface] | None = None,
           tailscale_reader: Callable[[Sequence[str]], tuple[int, str, str] | None] | None = None
           ) -> Answer:
    """The one address to show for this bind, or an `Answer` that explains its silence.

    `interfaces` and `tailscale_reader` are the two seams: fixtures in the tests, the real OS
    and the real binary in the window. Everything decision-shaped is here, so the Rust side and
    `desktop/ui/address.js` have nothing left to decide.
    """
    bind = normalize(host)
    scope = scope_of(bind)

    if scope == 'loopback':
        # Rule 2, and rule 1's speed: no probe at all. There is a LAN address on this machine
        # and it is not this server's, so neither it nor `127.0.0.1` is offered as shareable.
        return Answer(bind=bind, scope=scope, primary=None, tailscale='unread',
                      note='This server is listening on this computer only, so there is no '
                           'network address to share. Restart it with a network host to reach '
                           'it from another device.')

    if scope == 'bind':
        # A concrete bind is its own answer, and asking the machine what else it has would
        # invite a second address for a server that is not listening on it.
        return Answer(bind=bind, scope=scope,
                      primary=Offer(bind, 'bind',
                                    'The server is bound to this address, so this is the one '
                                    'that answers.'))

    found = tuple(interfaces) if interfaces is not None else probe_interfaces()
    tailscale = tailscale_state(tailscale_reader)

    offers: list[Offer] = []
    if tailscale.state == 'up':
        if tailscale.magicdns:
            offers.append(Offer(
                tailscale.magicdns, 'magicdns',
                'Tailscale is running and gives this machine a name, which keeps working when '
                'the address behind it changes.'))
        for address in sorted(tailscale.addresses, key=_literal_rank):
            offers.append(Offer(
                address, 'tailscale',
                'Tailscale is running, so any device signed in to the tailnet can reach this '
                'address.'))

    lan = [interface for interface in found if classify(interface) == 'lan']
    for interface in sorted(lan, key=_interface_rank):
        where = f' on {interface.name}' if interface.name else ''
        offers.append(Offer(
            interface.address, 'lan',
            f'This computer has this address{where}; devices on the same network can reach '
            f'it, and so can nothing else.'))

    if not offers:
        return Answer(bind=bind, scope=scope, primary=None, tailscale=tailscale.state,
                      note=_nothing_note(tailscale),
                      tailscale_detail=tailscale.detail)

    ranked = _dedupe(offers)
    return Answer(bind=bind, scope=scope, primary=ranked[0], others=ranked[1:],
                  tailscale=tailscale.state, tailscale_detail=tailscale.detail)


def main(argv: Sequence[str] | None = None) -> int:
    """`python3 -m host.addresses [host]` — the detection, printed, for a human checking it.

    Read-only like everything else here; useful for answering "what would the window show on
    this machine" without building a window.
    """
    argv = sys.argv[1:] if argv is None else argv
    answer = detect(argv[0] if argv else DEFAULT_HOST)
    print(json.dumps(answer.to_dict(), indent=2, sort_keys=True))
    return 0


if __name__ == '__main__':
    raise SystemExit(main())