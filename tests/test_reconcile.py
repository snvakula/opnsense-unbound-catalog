import fcntl
import sys
import tempfile
import unittest
from pathlib import Path
from threading import Event, Thread
from unittest.mock import Mock, patch

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src/opnsense/scripts/OPNsense/UnboundCatalog"))
from reconcile import ReconcileError, Stub, Transaction, pack, plan
from resolver_service import eligible_snapshots, run
from sync import Catalog, Poller, Settings, atomic_json, load_json
from unbound import Unbound

TEMPLATE = ROOT / "src/opnsense/service/templates/OPNsense/UnboundCatalog/stub-zones.j2"
PARENT = "parent.example.net."
CHILD = "child." + PARENT
CATALOG = "catalog.example.net."
SECOND = "second.example.net."


def make_catalog(name, address="192.0.2.1"):
    return Catalog(name, ("192.0.2.3",), 53, (address, "2001:db8::1"), 5354, True)


class PlanTest(unittest.TestCase):
    def test_ownership_conflicts_and_failed_transfers(self):
        catalogs = {name: make_catalog(name) for name in (CATALOG, SECOND)}
        records = {CATALOG: {"members": {"a": PARENT, "b": CHILD}},
                   SECOND: {"members": {"c": PARENT}}}
        desired, conflicts = plan(catalogs, records, {})
        self.assertFalse(conflicts)
        self.assertEqual(desired[PARENT].owners, {CATALOG: "a", SECOND: "c"})
        self.assertEqual(desired[PARENT].addresses, ("192.0.2.1@5354", "2001:db8::1@5354"))
        self.assertEqual(desired[CHILD].owners, {CATALOG: "b"})

        # Disable the first catalog and lose contact with the second.
        records[SECOND]["error"] = "timed out"
        retained, conflicts = plan({SECOND: catalogs[SECOND]}, records, desired)
        self.assertFalse(conflicts)
        self.assertEqual(retained[PARENT].owners, {CATALOG: "a", SECOND: "c"})
        self.assertEqual(retained[CHILD], desired[CHILD])

        # Only a successful empty transfer removes that catalog's ownership.
        records[CATALOG]["members"] = {}
        self.assertNotIn(CHILD, plan(catalogs, records, desired)[0])
        records[CATALOG]["members"] = {"a": PARENT}
        catalogs[CATALOG] = make_catalog(CATALOG, "192.0.2.2")
        self.assertIn(PARENT, plan(catalogs, records, desired)[1])

    def test_explicit_withdrawal_removes_only_selected_ownership(self):
        old = {
            PARENT: Stub(("192.0.2.1",), True, {CATALOG: "a", SECOND: "b"}),
            CHILD: Stub(("192.0.2.1",), True, {CATALOG: "c"}),
        }
        settings = Settings({}, configured_catalogs=frozenset({SECOND}))
        retired = settings.retired_catalogs({CATALOG, SECOND})
        self.assertEqual(retired, {CATALOG})
        desired, conflicts = plan({}, {}, old, retired=retired)
        self.assertFalse(conflicts)
        self.assertEqual(set(desired), {PARENT})
        self.assertEqual(desired[PARENT].owners, {SECOND: "b"})
        self.assertEqual(plan({}, {}, old, disabled={PARENT}, retired=retired), ({}, {}))

    def test_disabling_module_retires_all_catalog_owners(self):
        old = {PARENT: Stub(("192.0.2.1",), True, {CATALOG: "a", SECOND: "b"})}
        settings = Settings({}, configured_catalogs=frozenset({CATALOG, SECOND}), enabled=False)
        retired = settings.retired_catalogs({CATALOG, SECOND})
        self.assertEqual(retired, {CATALOG, SECOND})
        self.assertEqual(plan({}, {}, old, retired=retired), ({}, {}))

    def test_worker_start_discards_removed_and_disabled_snapshots(self):
        with tempfile.TemporaryDirectory() as temporary:
            filename = Path(temporary) / "state.json"
            removed = "removed.example.net."
            records = {name: {"members": {"x": PARENT}} for name in (CATALOG, SECOND, removed)}
            atomic_json(filename, {"version": 1, "catalogs": records})
            settings = Settings({}, disabled_catalogs=frozenset({SECOND}),
                                configured_catalogs=frozenset({CATALOG, SECOND}))
            Poller(settings, filename, Mock())
            self.assertEqual(load_json(filename)["catalogs"], {CATALOG: records[CATALOG]})

    def test_reactivated_catalog_requires_a_new_generation(self):
        with tempfile.TemporaryDirectory() as temporary:
            marker = Path(temporary) / "invalidated.json"
            row = make_catalog(CATALOG)
            settings = Settings({CATALOG: row}, configured_catalogs=frozenset({CATALOG}))
            records = {CATALOG: {"members": {"x": PARENT}, "serial": 5}}
            self.assertEqual(eligible_snapshots(marker, Settings({}), records, {CATALOG}, persist=True), {})
            self.assertEqual(eligible_snapshots(marker, settings, records, set()), {})
            records[CATALOG]["error"] = "timed out"
            self.assertEqual(eligible_snapshots(marker, settings, records, set()), {})
            records[CATALOG].update(error=None, generation="new")
            self.assertEqual(eligible_snapshots(marker, settings, records, set(), persist=True), records)

    def test_render_and_exact_name_conflicts(self):
        stub = Stub(("192.0.2.1", "2001:db8::1@5354"), True, {CATALOG: "a"})
        with tempfile.TemporaryDirectory() as temporary:
            folder = Path(temporary)
            configuration = folder / "unbound.conf"
            configuration.write_text(f'local-zone: "{PARENT}" static\n')
            resolver = Unbound(configuration, folder / "persistent/catalog.conf",
                               folder / "active/catalog.conf", TEMPLATE, folder)
            resolver.control = Mock(return_value="")
            rendered = resolver.render({CHILD: stub}).decode()
            self.assertIn('domain-insecure: "' + CHILD + '"', rendered)
            self.assertIn("stub-addr: 192.0.2.1\n", rendered)
            self.assertIn("stub-addr: 2001:db8::1@5354\n", rendered)
            self.assertFalse(resolver.conflicts({}, {CHILD: stub}))
            self.assertEqual(resolver.conflicts({}, {PARENT: stub}), {PARENT: "Existing Unbound local-zone (static)"})

    def test_local_zone_types_in_configuration_and_at_runtime(self):
        stub = Stub(("192.0.2.1",), False, {CATALOG: "a"})
        compatible = ("transparent", "typetransparent", "always_transparent", "inform", "noview")
        blocking = (
            "static", "redirect", "refuse", "deny", "inform_redirect", "inform_deny",
            "always_refuse", "always_deny", "always_nxdomain", "always_nodata", "always_null",
            "block_a", "block_aaaa", "block_a_wdata", "block_aaaa_wdata", "truncate", "future_type",
        )
        with tempfile.TemporaryDirectory() as temporary:
            folder = Path(temporary)
            configuration = folder / "unbound.conf"
            resolver = Unbound(configuration, folder / "persistent/catalog.conf",
                               folder / "active/catalog.conf", TEMPLATE, folder)
            runtime = {}
            resolver.control = Mock(side_effect=lambda command: runtime.get(command, ""))
            for kind in (*compatible, "nodefault", "ipset", *blocking):
                with self.subTest(kind=kind, source="configuration"):
                    configuration.write_text(f'server:\n    local-zone: "{PARENT}" {kind}\n')
                    conflicts = resolver.conflicts({}, {PARENT: stub, CHILD: stub})
                    self.assertEqual(conflicts, {PARENT: f"Existing Unbound local-zone ({kind})"} if kind in blocking else {})
            configuration.write_text("server:\n")
            for kind in (*compatible, *blocking):
                with self.subTest(kind=kind, source="runtime"):
                    runtime["list_local_zones"] = f"\n{PARENT.upper()}\t{kind}\nlocalhost. static\n"
                    conflicts = resolver.conflicts({}, {PARENT: stub, CHILD: stub})
                    self.assertEqual(conflicts, {PARENT: f"Existing Unbound local-zone ({kind})"} if kind in blocking else {})

    def test_compatible_local_zone_does_not_hide_other_conflicts(self):
        stub = Stub(("192.0.2.1",), True, {CATALOG: "a"})
        with tempfile.TemporaryDirectory() as temporary:
            folder = Path(temporary)
            configuration = folder / "unbound.conf"
            hosts = folder / "host_entries.conf"
            hosts.write_text(f'local-zone: "{PARENT}" transparent\nlocal-data: "host.{PARENT} A 192.0.2.3"\n')
            configuration.write_text(
                f'forward-zone:\n    name: "{PARENT}"\nserver:\n    include: "{hosts}"\n'
            )
            resolver = Unbound(configuration, folder / "persistent/catalog.conf",
                               folder / "active/catalog.conf", TEMPLATE, folder)
            runtime = {"list_local_zones": f"{PARENT} transparent"}
            resolver.control = Mock(side_effect=lambda command: runtime.get(command, ""))
            self.assertEqual(resolver.conflicts({}, {PARENT: stub}), {PARENT: "Existing Unbound forward-zone"})

            runtime["list_local_zones"] = f"{PARENT} deny"
            configuration.write_text(f'server:\n    include: "{hosts}"\n')
            self.assertEqual(resolver.conflicts({}, {PARENT: stub}), {PARENT: "Existing Unbound local-zone (deny)"})

            runtime["list_local_zones"] = f"{PARENT} transparent"
            configuration.write_text(
                f'server:\n    include: "{hosts}"\n    local-zone-override: "{PARENT}" 192.0.2.0/24 refuse\n'
            )
            self.assertEqual(
                resolver.conflicts({}, {PARENT: stub}), {PARENT: "Existing Unbound local-zone-override (refuse)"}
            )

            runtime["list_insecure"] = PARENT
            configuration.write_text(f'server:\n    include: "{hosts}"\n    domain-insecure: "{PARENT}"\n')
            self.assertEqual(
                resolver.conflicts({PARENT: stub}, {}), {PARENT: "Existing administrator DNSSEC exception"}
            )


    def test_legacy_include_migrates_only_when_it_matches_applied_state(self):
        stub = Stub(("192.0.2.1",), True, {CATALOG: "a"})
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            for name in ("active", "manual", "private"):
                (root / name).mkdir()
            active = root / "active/catalog.conf"
            legacy = root / "manual/catalog.conf"
            persistent = root / "private/catalog.conf"
            config = root / "unbound.conf"
            config.write_text(f"include: {active.parent}/*.conf\n")
            resolver = Unbound(config, persistent, active, TEMPLATE, root, legacy=legacy)
            zones = {PARENT: stub}
            legacy.write_bytes(resolver.render(zones))
            active.write_bytes(legacy.read_bytes())
            resolver.runtime = Mock(return_value=({PARENT: (("192.0.2.1",), True)}, {PARENT}))
            resolver.validation_enabled = Mock(return_value=True)
            resolver.command = Mock()
            self.assertNotIn(PARENT, resolver.admin_zones(zones))
            self.assertFalse(resolver.files_match(zones))
            resolver.validate(zones, zones)
            resolver.transition(zones, zones)
            self.assertFalse(legacy.exists())
            self.assertEqual(persistent.read_bytes(), active.read_bytes())
            self.assertTrue(resolver.files_match(zones))

            manual = f'local-zone: "{CHILD}" transparent\n'
            legacy.write_text(manual)
            with self.assertRaisesRegex(ReconcileError, "Administrator file occupies"):
                resolver.validate(zones, zones)
            self.assertEqual(legacy.read_text(), manual)


class TransactionTest(unittest.TestCase):
    def test_conflicts_do_not_block_other_changes(self):
        with tempfile.TemporaryDirectory() as temporary:
            resolver = Mock()
            resolver.conflicts.return_value = {PARENT: "existing local zone"}
            resolver.matches.return_value = False
            transaction = Transaction(resolver, Path(temporary), Mock())
            stub = Stub(("192.0.2.1",), True, {CATALOG: "x"})
            atomic_json(transaction.applied, pack({PARENT: stub}))
            desired = {CHILD: stub}
            self.assertEqual(transaction.apply(desired), {PARENT: "existing local zone"})
            self.assertEqual(transaction.current(), desired | {PARENT: stub})
            resolver.transition.assert_called_once_with({PARENT: stub}, desired | {PARENT: stub},
                                                        skipped=frozenset({PARENT}))
            resolver.transition.reset_mock()
            self.assertEqual(transaction.apply(desired, disabled={PARENT}), {})
            self.assertEqual(transaction.current(), desired)
            resolver.transition.assert_called_once_with(desired | {PARENT: stub}, desired,
                                                        skipped=frozenset({PARENT}))

    def test_rollback_recovers_previous_state(self):
        with tempfile.TemporaryDirectory() as temporary:
            resolver = Mock()
            resolver.conflicts.return_value = {}
            resolver.matches.return_value = False
            resolver.transition.side_effect = [ReconcileError("interrupted"), None]
            transaction = Transaction(resolver, Path(temporary), Mock())
            desired = {PARENT: Stub(("192.0.2.1",), False, {CATALOG: "a"})}
            with self.assertRaisesRegex(ReconcileError, "interrupted"):
                transaction.apply(desired)
            self.assertEqual(transaction.current(), {})
            self.assertFalse(transaction.journal.exists())
            resolver.transition.assert_called_with(desired, {}, recovering=True, skipped=frozenset())

    def test_recovery_retains_a_committed_transaction(self):
        with tempfile.TemporaryDirectory() as temporary:
            resolver = Mock()
            transaction = Transaction(resolver, Path(temporary), Mock())
            desired = {PARENT: Stub(("192.0.2.1",), True, {CATALOG: "a"})}
            atomic_json(transaction.journal, {"previous": {}, "desired": pack(desired)})
            atomic_json(transaction.applied, pack(desired))
            transaction.recover()
            resolver.transition.assert_called_once_with({}, desired, recovering=True, skipped=frozenset())
            self.assertEqual(transaction.current(), desired)
            self.assertFalse(transaction.journal.exists())


class RemovalLockTest(unittest.TestCase):
    def test_removal_waits_for_in_flight_reconciliation(self):
        with tempfile.TemporaryDirectory() as temporary:
            directory = Path(temporary) / "resolver"
            directory.mkdir()
            entered, finished = Event(), Event()

            def remove():
                entered.set()
                run("remove")
                finished.set()

            with patch("resolver_service.STATE", directory.parent), patch("resolver_service.Transaction") as state:
                state.return_value.current.return_value = {}
                with (directory / "lock").open("a") as holder:
                    fcntl.flock(holder, fcntl.LOCK_EX)
                    worker = Thread(target=remove)
                    worker.start()
                    self.assertTrue(entered.wait(2))
                    self.assertFalse(finished.wait(0.1))
                    self.assertRaises(BlockingIOError, run, "reconcile")
                worker.join(2)
                self.assertTrue(finished.is_set())
