"""Run the delegation acceptance test with BIND and Unbound on a private loopback lab."""

import ipaddress
import os
import shutil
import socket
import struct
import subprocess
import sys
import tempfile
import time
import unittest
from pathlib import Path
from threading import Thread

import dns.flags
import dns.message
import dns.opcode
import dns.query
import dns.rcode
import dns.rdatatype
import dns.rrset

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src/opnsense/scripts/OPNsense/UnboundCatalog"))
from catalog import CatalogError, transfer
from notify import Listener
from reconcile import Transaction, pack, plan
from sync import Catalog, Poller, Settings, atomic_json
from unbound import Unbound

PARENT = "catalog-lab.example.net."
CHILD = "child." + PARENT
CATALOG = "members." + PARENT
SECOND = "other-members." + PARENT
CHILD_ADDRESS = "127.0.0.1"
TTL = "$TTL 60\n"
SOA = "ns.{zone} hostmaster.{zone} {serial} 60 60 600 60"
ANSWERS = {
    ("", "NS"): f"ns.{CHILD}",
    ("", "SOA"): SOA.format(zone=CHILD, serial=1),
    ("", "A"): "192.0.2.42",
    ("", "AAAA"): "2001:db8::42",
    ("", "MX"): f"10 mail.{CHILD}",
    ("", "TXT"): '"delegated child"',
    ("alias.", "CNAME"): f"mail.{CHILD}",
}


def port():
    with socket.socket() as sock:
        sock.bind(("127.0.0.1", 0))
        return sock.getsockname()[1]


def query(name, kind, host, server_port, recursive=True, timeout=2):
    packet = dns.message.make_query(name, kind)
    if not recursive:
        packet.flags &= ~dns.flags.RD
    return dns.query.udp(packet, host, port=server_port, timeout=timeout)


def wait_until(check, folder, timeout=12):
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        try:
            if check():
                return
        except (OSError, dns.exception.Timeout):
            pass
        time.sleep(0.1)
    logs = "\n".join(path.read_text()[-3000:] for path in folder.glob("*.log"))
    raise RuntimeError(f"DNS fixture did not converge\n{logs}")


def catalog_zone(serial, version=2, member=PARENT, zone=CATALOG):
    records = f'@ SOA {SOA.format(zone=zone, serial=serial)}\n@ NS ns.{PARENT}\nversion TXT "{version}"\n'
    return TTL + records + (f"m.zones PTR {member}\n" if member else "")


def fixtures(folder, child_address):
    zones = {
        PARENT: TTL + f"""
@ SOA {SOA.format(zone=PARENT, serial=1)}
@ NS ns.{PARENT}
ns A 192.0.2.1
child NS ns.{CHILD}
ns.child A {child_address}
""",
        CHILD: TTL + "\n".join(f"{label.rstrip('.') or '@'} {kind} {value}"
                                for (label, kind), value in ANSWERS.items())
        + f"\nns A {child_address}\nmail A 192.0.2.43\n",
        CATALOG: catalog_zone(4294967295),
        SECOND: catalog_zone(7, zone=SECOND),
    }
    for zone, content in zones.items():
        (folder / (zone + "zone")).write_text(content)


def bind_config(folder, zones, address, server_port, ipv6=None):
    declarations = "\n".join(
        f'zone "{zone}" {{ type primary; file "{zone}zone"; }};' for zone in zones
    )
    ipv6_listener = f"port {server_port} {{ {ipv6}; }}" if ipv6 else "{ none; }"
    config = folder / f"named-{server_port}.conf"
    config.write_text(f"""
options {{
    directory "{folder}";
    pid-file "{folder}/named-{server_port}.pid";
    session-keyfile none;
    managed-keys-directory "{folder}";
    listen-on port {server_port} {{ {address}; }};
    listen-on-v6 {ipv6_listener};
    recursion no;
    notify no;
    allow-query {{ any; }};
    allow-transfer {{ any; }};
    dnssec-validation no;
    minimal-responses yes;
}};
controls {{ }};
{declarations}
""")
    return config


def assert_recursive(test, resolve):
    for (prefix, kind), expected in ANSWERS.items():
        with test.subTest(name=prefix + CHILD, kind=kind):
            response = resolve(prefix + CHILD, kind)
            test.assertEqual(response.rcode(), dns.rcode.NOERROR)
            test.assertTrue(response.flags & dns.flags.RA)
            test.assertFalse(response.flags & dns.flags.AA)
            test.assertEqual([(r.name.to_text(), dns.rdatatype.to_text(r.rdtype), [x.to_text() for x in r])
                              for r in response.answer], [(prefix + CHILD, kind, [expected])])
            test.assertEqual(response.authority, [])
    for prefix, code in (("absent.", dns.rcode.NXDOMAIN), ("mail.", dns.rcode.NOERROR)):
        response = resolve(prefix + CHILD, "TXT")
        test.assertEqual(response.rcode(), code)
        test.assertTrue(response.flags & dns.flags.RA)
        test.assertEqual(response.answer, [])
        test.assertEqual([(r.name.to_text(), r.rdtype, [x.to_text() for x in r]) for r in response.authority],
                         [(CHILD, dns.rdatatype.SOA, [SOA.format(zone=CHILD, serial=1)])])


@unittest.skipUnless(os.getenv("CATALOG_DNS_INTEGRATION") == "1", "requires BIND and Unbound")
class DelegationTest(unittest.TestCase):
    def process(self, folder, name, *args):
        binary = shutil.which(name)
        self.assertIsNotNone(binary, f"{name} is required")
        with (folder / (name + ".log")).open("ab") as log:
            process = subprocess.Popen([binary, *map(str, args)], stdout=log, stderr=log)
        self.addCleanup(self.stop, process)
        return process

    @staticmethod
    def stop(process):
        if process.poll() is None:
            process.terminate()
            process.communicate(timeout=10)

    def test_incomplete_axfr_over_tcp(self):
        answers = []
        with socket.socket() as server:
            server.bind(("127.0.0.1", 0))
            server.listen(1)
            server.settimeout(3)

            def close_after_soa():
                connection, _ = server.accept()
                with connection, connection.makefile("rb") as stream:
                    connection.settimeout(3)
                    length, = struct.unpack("!H", stream.read(2))
                    request = dns.message.from_wire(stream.read(length))
                    reply = dns.message.make_response(request)
                    reply.flags |= dns.flags.AA
                    reply.answer.append(dns.rrset.from_text(
                        CATALOG, 60, "IN", "SOA", SOA.format(zone=CATALOG, serial=1),
                    ))
                    payload = reply.to_wire()
                    connection.sendall(struct.pack("!H", len(payload)) + payload)
                    answers.append(payload)

            producer = Thread(target=close_after_soa)
            producer.start()
            try:
                with self.assertRaisesRegex(CatalogError, "Transfer ended before the closing SOA"):
                    transfer(CATALOG, "127.0.0.1", server.getsockname()[1], 3, 100, 1024)
            finally:
                producer.join(4)
            self.assertEqual(len(answers), 1)

    def test_transfer_stub_and_delegation(self):
        with tempfile.TemporaryDirectory() as temporary:
            folder = Path(temporary)
            for directory in ("persistent", "active"):
                (folder / directory).mkdir()
            fixtures(folder, CHILD_ADDRESS)
            parent_port, resolver_port = port(), port()
            child = self.process(folder, "named", "-g", "-n", "1", "-c",
                                 bind_config(folder, [CHILD], CHILD_ADDRESS, 53))
            parent = self.process(folder, "named", "-g", "-n", "1", "-c",
                                  bind_config(folder, [PARENT, CATALOG, SECOND], "127.0.0.1", parent_port, "::1"))
            wait_until(lambda: query(CATALOG, "SOA", "127.0.0.1", parent_port).answer, folder)
            wait_until(lambda: query(CHILD, "SOA", CHILD_ADDRESS, 53).answer, folder)
            referral = query(CHILD, "A", "127.0.0.1", parent_port, recursive=False)
            self.assertEqual(referral.answer, [])
            self.assertEqual([(r.name.to_text(), r.rdtype) for r in referral.authority], [(CHILD, dns.rdatatype.NS)])
            configuration = folder / "unbound.conf"
            control_socket = folder / "control.sock"
            configuration.write_text(f'''
server:
    chroot: ""
    username: ""
    directory: "{folder}"
    pidfile: "{folder}/unbound.pid"
    interface: 127.0.0.1
    port: {resolver_port}
    do-not-query-localhost: no
    use-syslog: no
    module-config: "validator iterator"
remote-control:
    control-enable: yes
    control-interface: "{control_socket}"
include-toplevel: "{folder}/active/*.conf"
''')
            template = ROOT / "src/opnsense/service/templates/OPNsense/UnboundCatalog/stub-zones.j2"
            resolver = Unbound(configuration, folder / "persistent/catalog.conf", folder / "active/catalog.conf",
                               template, folder, shutil.which("unbound-control"), shutil.which("unbound-checkconf"))
            unbound = self.process(folder, "unbound", "-d", "-c", configuration)
            wait_until(control_socket.exists, folder)
            now = 1
            def log(*_):
                pass
            authorities = ("127.0.0.1", "::1")
            sources = (ipaddress.ip_network("127.0.0.1"), ipaddress.ip_network("::1"))
            catalogs = {
                CATALOG: Catalog(CATALOG, ("::1",), parent_port, authorities, parent_port, True, sources),
                SECOND: Catalog(SECOND, ("127.0.0.1",), parent_port, authorities, parent_port, True, sources),
            }
            settings = Settings(catalogs, interval=10, retry_initial=1, timeout=1, notify_port=port())
            poller = Poller(settings, folder / "state.json", log, clock=lambda: now)
            transaction = Transaction(resolver, folder, log)

            def apply():
                desired, conflicts = plan(poller.settings.catalogs, poller.state, transaction.current())
                self.assertEqual(conflicts, {})
                return transaction.apply(desired)

            def reconcile():
                self.assertTrue(poller.run_due())
                self.assertEqual(apply(), {})

            self.assertTrue(poller.run_due())
            resolver.modify("forward_add", PARENT, "192.0.2.1")
            self.assertIn(PARENT, apply())
            self.assertFalse(resolver.persistent.exists())
            resolver.modify("forward_remove", PARENT)
            self.assertEqual(apply(), {})
            self.assertEqual(poller.state[CATALOG]["serial"], 4294967295)
            self.assertEqual(set(transaction.current()[PARENT].owners), {CATALOG, SECOND})
            self.assertIn(PARENT, resolver.runtime()[1])
            assert_recursive(self, lambda name, kind: query(name, kind, "127.0.0.1", resolver_port))

            # A resolver restart must reproduce both effective files and runtime state.
            self.stop(unbound)
            control_socket.unlink(missing_ok=True)
            unbound = self.process(folder, "unbound", "-d", "-c", configuration)
            wait_until(control_socket.exists, folder)
            self.assertTrue(resolver.matches(transaction.current(), transaction.current()))
            assert_recursive(self, lambda name, kind: query(name, kind, "127.0.0.1", resolver_port))

            # Restore a live stub after interrupting an uncommitted removal.
            atomic_json(transaction.journal, {"previous": pack(transaction.current()), "desired": {}})
            resolver.modify("stub_remove", PARENT)
            self.assertNotIn(PARENT, resolver.runtime()[0])
            transaction.recover()
            self.assertIn(PARENT, resolver.runtime()[0])

            # Packet reception and the transfer schedule are independent.
            def notify(name, address):
                packet = dns.message.make_query(name, "SOA")
                packet.set_opcode(dns.opcode.NOTIFY)
                packet.flags = (packet.flags & ~dns.flags.RD) | dns.flags.AA
                return dns.query.udp(packet, address, port=settings.notify_port, timeout=2)

            with Listener(poller) as listener:
                self.assertEqual(listener.active_families(), ["AF_INET", "AF_INET6"])
                self.assertEqual(notify(PARENT, "127.0.0.1").rcode(), dns.rcode.NOERROR)
                self.assertFalse(poller.requests)
                for address in ("127.0.0.1", "::1", "::1"):
                    self.assertEqual(notify(CATALOG, address).rcode(), dns.rcode.NOERROR)
                self.assertEqual(len(poller.requests), 1)
                now += settings.debounce
                reconcile()
                self.assertFalse(poller.run_due())

            self.stop(child)
            try:
                failed = query("outage." + CHILD, "A", "127.0.0.1", resolver_port)
            except dns.exception.Timeout:
                pass
            else:
                self.assertEqual(failed.rcode(), dns.rcode.SERVFAIL)
                self.assertEqual(failed.answer, [])
            self.assertIn(PARENT, transaction.current())
            child = self.process(folder, "named", "-g", "-n", "1", "-c", folder / "named-53.conf")
            wait_until(lambda: query(CHILD, "SOA", CHILD_ADDRESS, 53).answer, folder)
            resolver.control("flush_zone", PARENT)
            assert_recursive(self, lambda name, kind: query(name, kind, "127.0.0.1", resolver_port))

            self.stop(parent)
            now += settings.interval
            reconcile()
            self.assertIn(PARENT, transaction.current())
            self.assertTrue(poller.state[CATALOG]["error"])

            (folder / (CATALOG + "zone")).write_text(catalog_zone(0, version=1))
            bind = folder / f"named-{parent_port}.conf"
            parent = self.process(folder, "named", "-g", "-n", "1", "-c", bind)
            wait_until(lambda: query(CATALOG, "SOA", "127.0.0.1", parent_port).answer, folder)
            now += 1
            reconcile()
            self.assertIn(PARENT, transaction.current())
            self.assertEqual(poller.state[CATALOG]["serial"], 4294967295)

            self.stop(parent)
            (folder / (CATALOG + "zone")).write_text(catalog_zone(0, member=None))
            (folder / (SECOND + "zone")).write_text(catalog_zone(8, member=None, zone=SECOND))
            parent = self.process(folder, "named", "-g", "-n", "1", "-c", bind)
            wait_until(lambda: query(CATALOG, "SOA", "127.0.0.1", parent_port).answer, folder)
            now += settings.interval
            reconcile()
            self.assertEqual(poller.state[CATALOG]["serial"], 0)
            self.assertEqual(transaction.current(), {})
            self.assertNotIn(PARENT, resolver.runtime()[1])
            self.assertFalse(resolver.persistent.exists())
            for process in (parent, child, unbound):
                self.stop(process)
