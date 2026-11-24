import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import Mock

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src/opnsense/scripts/OPNsense/UnboundCatalog"))
from reconcile import ReconcileError, Stub, Transaction, pack, plan
from sync import Catalog, atomic_json
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
            resolver.transition.assert_called_with(desired, {}, recovering=True)

    def test_recovery_retains_a_committed_transaction(self):
        with tempfile.TemporaryDirectory() as temporary:
            resolver = Mock()
            transaction = Transaction(resolver, Path(temporary), Mock())
            desired = {PARENT: Stub(("192.0.2.1",), True, {CATALOG: "a"})}
            atomic_json(transaction.journal, {"previous": {}, "desired": pack(desired)})
            atomic_json(transaction.applied, pack(desired))
            transaction.recover()
            resolver.transition.assert_called_once_with({}, desired, recovering=True)
            self.assertEqual(transaction.current(), desired)
            self.assertFalse(transaction.journal.exists())
