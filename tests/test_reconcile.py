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
            configuration.write_text(f'local-zone: "{PARENT}" transparent\n')
            resolver = Unbound(configuration, folder / "persistent/catalog.conf",
                               folder / "active/catalog.conf", TEMPLATE, folder)
            resolver.control = Mock(return_value="")
            rendered = resolver.render({CHILD: stub}).decode()
            self.assertIn('domain-insecure: "' + CHILD + '"', rendered)
            self.assertIn("stub-addr: 192.0.2.1\n", rendered)
            self.assertIn("stub-addr: 2001:db8::1@5354\n", rendered)
            self.assertFalse(resolver.conflicts({}, {CHILD: stub}))
            self.assertIn(PARENT, resolver.conflicts({}, {PARENT: stub}))


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
