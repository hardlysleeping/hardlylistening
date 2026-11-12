"""Which address the rate limiter counts: the socket peer, or Cloudflare's header.

Behind the tunnel every connection arrives from loopback, so a limiter keyed on the peer
gives the whole internet one bucket and lets one stranger spend it. CF-Connecting-IP is
trusted only when the peer really is loopback, which keeps a direct client from choosing
its own bucket.
"""
import unittest

from security import Security


class Peer:
    def __init__(self, address=None, headers=None):
        if address is not None:
            self.client_address = (address, 1234)
        self.headers = headers or {}


class ClientIP(unittest.TestCase):
    def test_a_loopback_peer_is_identified_by_the_forwarded_address(self):
        handler = Peer('127.0.0.1', {'CF-Connecting-IP': '203.0.113.7'})
        self.assertEqual(Security.client_ip(handler), '203.0.113.7')

    def test_ipv6_loopback_is_recognised_too(self):
        for peer in ('::1', '::ffff:127.0.0.1'):
            handler = Peer(peer, {'CF-Connecting-IP': '2001:db8::1'})
            self.assertEqual(Security.client_ip(handler), '2001:db8::1', peer)

    def test_a_loopback_peer_without_the_header_keeps_the_peer(self):
        self.assertEqual(Security.client_ip(Peer('127.0.0.1')), '127.0.0.1')

    def test_a_direct_peer_cannot_choose_its_own_address(self):
        handler = Peer('198.51.100.9', {'CF-Connecting-IP': '203.0.113.7'})
        self.assertEqual(Security.client_ip(handler), '198.51.100.9')

    def test_nonsense_falls_back_to_the_peer_instead_of_failing(self):
        for value in ('', '   ', 'not-an-ip', '1.2.3.4, 5.6.7.8', '999.1.1.1'):
            handler = Peer('127.0.0.1', {'CF-Connecting-IP': value})
            self.assertEqual(Security.client_ip(handler), '127.0.0.1', value)

    def test_a_missing_peer_is_still_a_stable_identity(self):
        self.assertEqual(Security.client_ip(Peer()), 'unknown')

    def test_the_header_is_trimmed_and_normalised(self):
        handler = Peer('127.0.0.1', {'CF-Connecting-IP': '  203.0.113.7 \t'})
        self.assertEqual(Security.client_ip(handler), '203.0.113.7')
        # An IPv6 address is returned in its canonical form, so one client is one bucket.
        handler = Peer('127.0.0.1', {'CF-Connecting-IP': '2001:0DB8:0:0::1'})
        self.assertEqual(Security.client_ip(handler), '2001:db8::1')

    def test_two_forwarded_addresses_do_not_share_a_bucket(self):
        security = Security()
        security.limit(Security.client_ip(Peer('127.0.0.1', {'CF-Connecting-IP': '203.0.113.7'})),
                       'login', 1)
        with self.assertRaises(Exception):
            security.limit(Security.client_ip(Peer('127.0.0.1', {'CF-Connecting-IP': '203.0.113.7'})),
                           'login', 1)
        # A different stranger still gets their own allowance.
        security.limit(Security.client_ip(Peer('127.0.0.1', {'CF-Connecting-IP': '203.0.113.8'})),
                       'login', 1)


if __name__ == '__main__':
    unittest.main()