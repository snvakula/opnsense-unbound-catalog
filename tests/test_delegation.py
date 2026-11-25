"""Exercise BIND and PowerDNS catalogs with Unbound on a private loopback lab."""

import ipaddress
import os
import shutil
import socket
import sqlite3
import struct
import subprocess
import sys
import tempfile
import time
import unittest
from dataclasses import replace
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


@unittest.skipUnless(os.getenv("CATALOG_DNS_INTEGRATION") == "1", "requires a live DNS integration environment")
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

    def resolver_fixture(self, folder, resolver_port):
        for directory in ("persistent", "active"):
            (folder / directory).mkdir()
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
    include: "{folder}/host_entries.conf"
remote-control:
    control-enable: yes
    control-interface: "{control_socket}"
include-toplevel: "{folder}/active/*.conf"
''')
        (folder / "host_entries.conf").touch()
        template = ROOT / "src/opnsense/service/templates/OPNsense/UnboundCatalog/stub-zones.j2"
        return Unbound(configuration, folder / "persistent/catalog.conf", folder / "active/catalog.conf",
                       template, folder, shutil.which("unbound-control"), shutil.which("unbound-checkconf"))

    @unittest.skipUnless(os.getenv("CATALOG_POWERDNS_INTEGRATION") == "1", "requires PowerDNS Authoritative")
    def test_powerdns_catalog_and_transparent_local_zone(self):
        with tempfile.TemporaryDirectory() as temporary:
            folder = Path(temporary)
            authoritative_port, resolver_port, notify_port = port(), port(), port()
            resolver = self.resolver_fixture(folder, resolver_port)
            local, remote = "local." + PARENT, "remote." + PARENT
            hosts = folder / "host_entries.conf"
            hosts.write_text(f'local-zone: "{PARENT}" transparent\nlocal-data: "{local} 60 IN A 192.0.2.90"\n')
            original_hosts = hosts.read_bytes()
            original_configuration = resolver.configuration.read_bytes()

            schema = Path(os.getenv("CATALOG_POWERDNS_SCHEMA", "/usr/share/doc/pdns-backend-sqlite3/schema.sqlite3.sql"))
            database = folder / "pdns.sqlite3"
            with sqlite3.connect(database) as connection:
                connection.executescript(schema.read_text())
            modules = os.getenv("CATALOG_POWERDNS_MODULE_DIR")
            (folder / "pdns.conf").write_text(f"""
launch=gsqlite3
gsqlite3-database={database}
local-address=127.0.0.1
local-port={authoritative_port}
socket-dir={folder}
allow-axfr-ips=
primary=yes
""" + (f"module-dir={modules}\n" if modules else ""))
            member = folder / "member.zone"
            member.write_text(TTL + f"""
@ SOA {SOA.format(zone=PARENT, serial=1)}
@ NS ns.{PARENT}
ns A 192.0.2.1
{local} A 192.0.2.42
{remote} A 192.0.2.43
""")
            config_dir = f"--config-dir={folder}"
            util = shutil.which("pdnsutil")
            self.assertIsNotNone(util, "pdnsutil is required")
            modern = int(subprocess.check_output([util, "--version"], text=True).split()[1].split(".")[0]) >= 5

            def pdnsutil(modern_command, legacy_command, *args):
                command = modern_command.split() if modern else [legacy_command]
                result = subprocess.run(
                    [util, config_dir, *command, *map(str, args)], check=False, capture_output=True, text=True,
                )
                self.assertEqual(result.returncode, 0, result.stdout + result.stderr)

            pdnsutil("zone create", "create-zone", CATALOG, f"ns.{PARENT}")
            pdnsutil("zone set-kind", "set-kind", CATALOG, "producer")
            pdnsutil("zone load", "load-zone", PARENT, member)
            pdnsutil("zone set-kind", "set-kind", PARENT, "primary")
            pdnsutil("catalog set", "set-catalog", PARENT, CATALOG)
            pdnsutil("metadata set", "set-meta", CATALOG, "ALLOW-AXFR-FROM", "127.0.0.1")
            pdnsutil("metadata set", "set-meta", CATALOG, "ALSO-NOTIFY", f"127.0.0.1:{notify_port}")
            producer = self.process(folder, "pdns_server", config_dir, "--daemon=no")
            wait_until(lambda: query(CATALOG, "SOA", "127.0.0.1", authoritative_port).answer, folder)
            unbound = self.process(folder, "unbound", "-d", "-c", resolver.configuration)
            wait_until((folder / "control.sock").exists, folder)

            def addresses(name, server_port=resolver_port):
                return [record.to_text() for rrset in query(name, "A", "127.0.0.1", server_port).answer
                        for record in rrset]

            self.assertEqual(addresses(local, authoritative_port), ["192.0.2.42"])
            self.assertEqual(addresses(local), ["192.0.2.90"])
            local_zones = resolver.control("list_local_zones")
            self.assertIn(f"{PARENT} transparent", local_zones.splitlines())
            local_data = resolver.control("list_local_data")
            catalog = Catalog(CATALOG, ("127.0.0.1",), authoritative_port, ("127.0.0.1",), authoritative_port,
                              True, (ipaddress.ip_network("127.0.0.1"),))
            settings = Settings({CATALOG: catalog}, notify_port=notify_port, debounce=0, ipv6=False)
            logs = []
            poller = Poller(settings, folder / "state.json", lambda *entry: logs.append(entry))
            self.assertTrue(poller.run_due(), logs)
            self.assertEqual(set(poller.state[CATALOG]["members"].values()), {PARENT}, logs)
            desired, conflicts = plan(settings.catalogs, poller.state, {})
            self.assertEqual(conflicts, {})

            transaction = Transaction(resolver, folder, lambda *_: None)
            self.assertEqual(transaction.apply(desired), {})
            self.assertIn(PARENT, resolver.runtime()[0])
            self.assertEqual(addresses(local), ["192.0.2.90"])
            self.assertEqual(addresses(remote), ["192.0.2.43"])
            self.assertEqual(resolver.control("list_local_zones"), local_zones)
            self.assertEqual(resolver.control("list_local_data"), local_data)

            control = shutil.which("pdns_control")
            self.assertIsNotNone(control, "pdns_control is required")
            with Listener(poller):
                subprocess.run([control, config_dir, "notify", CATALOG], check=True, capture_output=True, text=True)
                wait_until(poller.run_due, folder)
            self.assertTrue(any(level == "info" and "[push (DNS NOTIFY from 127.0.0.1)]" in message
                                for level, message in logs), logs)

            self.stop(unbound)
            (folder / "control.sock").unlink(missing_ok=True)
            unbound = self.process(folder, "unbound", "-d", "-c", resolver.configuration)
            wait_until((folder / "control.sock").exists, folder)
            self.assertEqual(addresses(local), ["192.0.2.90"])
            self.assertEqual(addresses(remote), ["192.0.2.43"])
            self.assertEqual(transaction.apply({}), {})
            self.assertNotIn(PARENT, resolver.runtime()[0])
            self.assertEqual(addresses(local), ["192.0.2.90"])
            self.assertEqual(resolver.control("list_local_zones"), local_zones)
            self.assertEqual(resolver.control("list_local_data"), local_data)
            self.assertEqual(hosts.read_bytes(), original_hosts)
            self.assertEqual(resolver.configuration.read_bytes(), original_configuration)
            self.stop(unbound)
            self.stop(producer)

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
            resolver = self.resolver_fixture(folder, resolver_port)
            configuration = resolver.configuration
            control_socket = folder / "control.sock"
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

            previous = transaction.current()
            blocked, healthy = "blocked." + PARENT, "healthy." + PARENT
            resolver.modify("forward_add", blocked, "192.0.2.1")
            desired = previous | dict.fromkeys((blocked, healthy), previous[PARENT])
            self.assertEqual(set(transaction.apply(desired)), {blocked})
            self.assertIn(healthy, resolver.runtime()[0])
            self.assertNotIn(blocked, resolver.runtime()[0])
            self.assertIn(blocked, resolver.control("list_forwards"))
            self.assertEqual(transaction.apply(previous, disabled={healthy}), {})
            self.assertNotIn(healthy, resolver.runtime()[0])
            self.assertNotIn(healthy, resolver.runtime()[1])
            assert_recursive(self, lambda name, kind: query(name, kind, "127.0.0.1", resolver_port))
            resolver.modify("forward_remove", blocked)

            for address in ("::1", "127.0.0.1"):
                family_catalogs = {name: replace(row, authorities=(address,)) for name, row in catalogs.items()}
                poller.settings = replace(settings, catalogs=family_catalogs)
                self.assertEqual(apply(), {})
                self.assertEqual(resolver.runtime()[0][PARENT], ((address,), True))
                assert_recursive(self, lambda name, kind: query(name, kind, "127.0.0.1", resolver_port))
            poller.settings = settings
            self.assertEqual(apply(), {})

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
