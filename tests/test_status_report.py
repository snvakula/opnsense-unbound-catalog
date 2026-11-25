import sys
import unittest
from pathlib import Path
from unittest.mock import Mock

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src/opnsense/scripts/OPNsense/UnboundCatalog"))
from reconcile import ReconcileError, Stub
from status_report import report
from sync import Catalog, Settings

NAME = "catalog.example.net."
ZONE = "member.example.net."
ADDR = "192.0.2.53"


class StatusTest(unittest.TestCase):
    def test_live_status_and_retained_zones(self):
        catalog = Catalog(NAME, (ADDR,), 53, (ADDR,), 53, False)
        settings = Settings({NAME: catalog})
        records = {NAME: {"members": {"key": ZONE}, "succeeded": 1, "next": 301}}
        old = {ZONE: Stub((ADDR,), False, {NAME: "key"}, 1)}
        resolver = Mock()
        resolver.runtime.return_value = ({}, set())
        resolver.validation_enabled.return_value = True
        resolver.files_match.return_value = True

        def rows(configuration=settings, outcome=None):
            return report(configuration, records, old, outcome or {}, resolver, [], True)

        self.assertEqual(rows()[0]["status"], "pending")
        resolver.runtime.return_value = ({ZONE: ((ADDR,), True)}, set())
        self.assertEqual(rows()[0]["status"], "active")
        self.assertEqual(rows(Settings({}))[0]["status"], "disabled")
        self.assertEqual(rows(outcome={"conflicts": {ZONE: "ownership conflict"}})[0]["status"], "conflict")
        records[NAME]["error"] = "transfer timed out"
        self.assertEqual(rows()[0]["status"], "stale")
        summary = report(settings, records, old, {}, resolver, ["AF_INET6"], False)
        self.assertEqual(summary["counts"]["active"], 1)
        self.assertEqual(summary["last_error"], "transfer timed out")
        self.assertEqual(summary["listener"], ["AF_INET6"])
        resolver.runtime.side_effect = ReconcileError("connection refused")
        self.assertEqual(rows()[0]["status"], "error")


    def test_old_conflicts_without_catalog_ownership_are_hidden(self):
        settings = Settings({}, configured_catalogs=frozenset())
        records = {NAME: {"members": {"key": ZONE}}}
        outcome = {"conflicts": {ZONE: "Existing Unbound list_forwards"}}
        resolver = Mock()
        resolver.runtime.return_value = ({}, set())
        self.assertEqual(report(settings, records, {}, outcome, resolver, [], True), [])
        summary = report(settings, records, {}, outcome, resolver, [], False)
        self.assertEqual(summary["counts"]["conflict"], 0)
        self.assertIsNone(summary["last_error"])
