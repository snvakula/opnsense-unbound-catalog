import sys
import tempfile
import unittest
from pathlib import Path

import dns.flags
import dns.message
import dns.rrset

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src/opnsense/scripts/OPNsense/UnboundCatalog"))
from catalog import CatalogError, CatalogTransfer, Snapshot
from sync import Catalog, Poller, Settings, load_json

ZONE = "catalog.example."
SOA = "ns.example. admin.example. 4294967295 60 60 600 60"


def rr(name, kind, data):
    return dns.rrset.from_text(name, 60, "IN", kind, data)


def frame(receiver, records, question=True):
    message = dns.message.make_response(receiver.query)
    message.flags |= dns.flags.AA
    if not question:
        message.question = []
    message.answer = records
    return dns.message.from_wire(message.to_wire(), xfr=True, one_rr_per_rrset=True)


def header(serial=SOA, version='"2"'):
    return [rr(ZONE, "SOA", serial), rr(ZONE, "NS", "invalid."), rr("version." + ZONE, "TXT", version)]


class CatalogTest(unittest.TestCase):
    def test_multiframe_and_unknown_records(self):
        receiver = CatalogTransfer("CATALOG.EXAMPLE", 12)
        receiver.feed(frame(receiver, header() + [
            rr("A.zones." + ZONE, "PTR", "MiXeD.example."),
            rr("unrelated." + ZONE, "PTR", "ignore.example."),
            rr("group.a.zones." + ZONE, "TXT", '"unknown"'),
        ]))
        receiver.feed(frame(receiver, [rr(ZONE, "SOA", SOA)], question=False))
        self.assertEqual(receiver.finish(), Snapshot(4294967295, {"a": "mixed.example."}))

    def test_valid_empty_and_rollover(self):
        receiver = CatalogTransfer(ZONE, 4)
        soa = SOA.replace("4294967295", "0")
        receiver.feed(frame(receiver, header(soa) + [rr(ZONE, "SOA", soa)]))
        self.assertEqual(receiver.finish(), Snapshot(0, {}))

    def test_reject_invalid_or_incomplete(self):
        member = rr("a.zones." + ZONE, "PTR", "member.example.")
        final = rr(ZONE, "SOA", SOA)
        for records in [
            header(version='"1"') + [final],
            header() + [member],
            header() + [member, member, final],
            header() + [member, rr("b.zones." + ZONE, "PTR", "member.example."), final],
            header() + [rr("outside.example.", "NS", "invalid."), final],
            header() + [rr("coo.a.zones." + ZONE, "PTR", "elsewhere.example."), final],
            header() + [rr(ZONE, "SOA", SOA.replace("4294967295", "7"))],
            header() + [final, member],
        ]:
            receiver = CatalogTransfer(ZONE, 100)
            with self.subTest(records=records), self.assertRaises(CatalogError):
                receiver.feed(frame(receiver, records))
                receiver.finish()
        limited = CatalogTransfer(ZONE, 3)
        with self.assertRaisesRegex(CatalogError, "limit"):
            limited.feed(frame(limited, header() + [final]))


class PollerTest(unittest.TestCase):
    def test_outage_and_restart_preserve_last_good_membership(self):
        catalog = Catalog(ZONE, ("192.0.2.1", "2001:db8::1"), 53, ("192.0.2.2",), 53, False)
        config = Settings({ZONE: catalog})
        now = 100
        calls = []

        def fetch(_name, server, *_args):
            calls.append(server)
            if server == "192.0.2.1" or now == 400:
                raise OSError("unavailable")
            return Snapshot(now, {} if now > 400 else {"a": "member.example."})

        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "state.json"
            def make_poller():
                return Poller(config, path, lambda *_: None, fetch, lambda: now, lambda: now)

            poller = make_poller()
            self.assertTrue(poller.run_due())
            self.assertEqual(calls, list(catalog.transfers))
            self.assertFalse(poller.run_due())
            now = 400
            poller.run_due()
            status = make_poller().state[ZONE]
            self.assertEqual(status["members"], {"a": "member.example."})
            self.assertEqual(status["serial"], 100)
            self.assertEqual(poller.next_delay(), 5)
            now = 405
            poller.run_due()
            self.assertEqual(load_json(path)["catalogs"][ZONE]["members"], {})

    def test_debounce_keeps_first_deadline(self):
        catalog = Catalog(ZONE, ("192.0.2.1",), 53, ("192.0.2.2",), 53, False)
        now = 100
        calls = []

        def fetch(*_args):
            calls.append(now)
            return Snapshot(1, {})

        with tempfile.TemporaryDirectory() as directory:
            poller = Poller(Settings({ZONE: catalog}), Path(directory) / "state.json",
                            lambda *_: None, fetch, lambda: now, lambda: now)
            poller.run_due()
            now = 110
            poller.request(ZONE)
            now = 111
            poller.request(ZONE)
            self.assertFalse(poller.run_due())
            now = 112
            self.assertTrue(poller.run_due())
            self.assertEqual(calls, [100, 112])
