import ipaddress
import unittest
from types import SimpleNamespace

import dns.flags
import dns.message
import dns.opcode
import dns.rcode
from notify import Notify, RateLimit
from test_catalog import ZONE


def packet(name=ZONE):
    request = dns.message.make_query(name, "SOA")
    request.set_opcode(dns.opcode.NOTIFY)
    request.flags |= dns.flags.AA
    request.flags &= ~dns.flags.RD
    return request


class NotifyTest(unittest.TestCase):
    def setUp(self):
        self.requests = []
        config = SimpleNamespace(sources=(ipaddress.ip_network("192.0.2.0/24"), ipaddress.ip_network("2001:db8::/64")))
        poller = SimpleNamespace(
            settings=SimpleNamespace(catalogs={ZONE: config}),
            log=lambda *_: None,
            request=self.requests.append,
            member_owners={"member.example.": [ZONE]},
        )
        self.listener = Notify(poller)

    def test_valid_notifications_preserve_id(self):
        request = packet()
        response = dns.message.from_wire(self.listener.receive(request.to_wire(), "2001:db8::10"))
        self.assertEqual(response.id, request.id)
        self.assertEqual(response.opcode(), dns.opcode.NOTIFY)
        self.assertEqual(response.flags & ~0x7800, dns.flags.QR | dns.flags.AA)
        self.assertEqual(self.requests, [ZONE])

    def test_member_unknown_unauthorized_and_malformed(self):
        self.assertIsNotNone(self.listener.receive(packet("member.example.").to_wire(), "192.0.2.10"))
        unknown = self.listener.receive(packet("unknown.example.").to_wire(), "192.0.2.10")
        self.assertEqual(dns.message.from_wire(unknown).rcode(), dns.rcode.REFUSED)
        self.assertIsNone(self.listener.receive(packet().to_wire(), "198.51.100.1"))
        self.assertIsNone(self.listener.receive(b"invalid", "192.0.2.10"))
        self.assertIsNone(self.listener.receive(dns.message.make_query(ZONE, "A").to_wire(), "192.0.2.10"))
        self.assertEqual(self.requests, [])

    def test_rate_limit_refills(self):
        now = 0
        limit = RateLimit(lambda: now)
        self.assertTrue(all(limit.allow("192.0.2.10") for _ in range(10)))
        self.assertFalse(limit.allow("192.0.2.10"))
        now = 1
        self.assertTrue(limit.allow("192.0.2.10"))
