"""Run the delegation acceptance test with BIND and Unbound on a private loopback lab."""

import os
import shutil
import socket
import subprocess
import sys
import tempfile
import time
import unittest
from pathlib import Path

import dns.flags
import dns.message
import dns.query
import dns.rcode
import dns.rdatatype

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src/opnsense/scripts/OPNsense/UnboundCatalog"))
from reconcile import Transaction, plan
from sync import Catalog, Poller, Settings
from unbound import Unbound

PARENT = "catalog-lab.net."
CHILD = "child." + PARENT
CATALOG = "members." + PARENT
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


def query(name, kind, host, server_port, recursive=True):
    packet = dns.message.make_query(name, kind)
    if not recursive:
        packet.flags &= ~dns.flags.RD
    return dns.query.udp(packet, host, port=server_port, timeout=2)


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


def catalog_zone(serial, version=2, member=PARENT):
    records = f'@ SOA {SOA.format(zone=CATALOG, serial=serial)}\n@ NS ns.{PARENT}\nversion TXT "{version}"\n'
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
                                  bind_config(folder, [PARENT, CATALOG], "127.0.0.1", parent_port, "::1"))
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
    qname-minimisation: no
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
            catalog = Catalog(CATALOG, ("::1", "127.0.0.1"), parent_port,
                              ("127.0.0.1", "::1"), parent_port, True)
            poller = Poller(Settings({CATALOG: catalog}, interval=1, retry_initial=1, timeout=1),
                            folder / "state.json", log, clock=lambda: now)
            transaction = Transaction(resolver, folder, log)

            def reconcile():
                self.assertTrue(poller.run_due())
                desired, conflicts = plan(poller.settings.catalogs, poller.state, transaction.current())
                self.assertEqual(conflicts, {})
                self.assertEqual(transaction.apply(desired), {})

            reconcile()
            self.assertEqual(poller.state[CATALOG]["serial"], 4294967295)
            self.assertEqual(set(transaction.current()), {PARENT})
            self.assertIn(PARENT, resolver.runtime()[1])
            assert_recursive(self, lambda name, kind: query(name, kind, "127.0.0.1", resolver_port))

            # A resolver restart must reproduce both effective files and runtime state.
            self.stop(unbound)
            control_socket.unlink(missing_ok=True)
            unbound = self.process(folder, "unbound", "-d", "-c", configuration)
            wait_until(control_socket.exists, folder)
            self.assertTrue(resolver.matches(transaction.current(), transaction.current()))
            assert_recursive(self, lambda name, kind: query(name, kind, "127.0.0.1", resolver_port))

            self.stop(parent)
            now += 1
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
            parent = self.process(folder, "named", "-g", "-n", "1", "-c", bind)
            wait_until(lambda: query(CATALOG, "SOA", "127.0.0.1", parent_port).answer, folder)
            now += 2
            reconcile()
            self.assertEqual(poller.state[CATALOG]["serial"], 0)
            self.assertEqual(transaction.current(), {})
            self.assertNotIn(PARENT, resolver.runtime()[1])
            self.assertFalse(resolver.persistent.exists())
            for process in (parent, child, unbound):
                self.stop(process)
