import os
import select
import signal
import subprocess
import sys
import unittest
from pathlib import Path
from types import SimpleNamespace

import jinja2

ROOT = Path(__file__).resolve().parents[1]
TEMPLATE = ROOT / "src/opnsense/service/templates/OPNsense/UnboundCatalog/rc.conf.d"
WORKER = ROOT / "src/opnsense/scripts/OPNsense/UnboundCatalog/worker.py"


class ServiceTemplateTest(unittest.TestCase):
    def render(self, enabled=None, catalogs=None, level=None):
        config = {
            "OPNsense.UnboundCatalog.general.enabled": enabled,
            "OPNsense.UnboundCatalog.general.log_level": level,
            "OPNsense.UnboundCatalog.catalogs.catalog": catalogs,
        }

        def to_list(key):
            value = config.get(key)
            if value is None:
                return []
            return value if isinstance(value, list) else [value]

        template = jinja2.Environment(trim_blocks=True).from_string(TEMPLATE.read_text())
        return template.render(
            helpers=SimpleNamespace(toList=to_list, getNodeByTag=config.get)
        )

    def test_fresh_install_does_not_enable_service(self):
        self.assertIn('unbound_catalog_enable="NO"', self.render())

    def test_enable_switch_alone_does_not_start_service(self):
        self.assertIn('unbound_catalog_enable="NO"', self.render(enabled="1"))

    def test_complete_enabled_catalog_is_required(self):
        catalog = {
            "enabled": "0",
            "name": "catalog.example.net.",
            "transfer_servers": "192.0.2.10",
        }
        self.assertIn('unbound_catalog_enable="NO"', self.render("1", catalog))
        catalog["enabled"] = "1"
        self.assertIn('unbound_catalog_enable="NO"', self.render("1", catalog))
        catalog["authoritative_servers"] = "2001:db8::10"
        self.assertIn('unbound_catalog_enable="NO"', self.render("0", catalog))
        self.assertIn('unbound_catalog_enable="YES"', self.render("1", [None, catalog]))
        self.assertIn('unbound_catalog_enable="YES"', self.render("1", catalog))

    def test_template_emits_only_supported_log_levels(self):
        self.assertIn('unbound_catalog_log_level="debug"', self.render(level="debug"))
        rendered = self.render(level='"; printf unexpected')
        self.assertIn('unbound_catalog_log_level="info"', rendered)
        self.assertNotIn("unexpected", rendered)


class WorkerTest(unittest.TestCase):
    def test_term_stops_worker_and_records_both_lifecycle_events(self):
        runner = """
import runpy
import sys
import syslog
syslog.syslog = lambda priority, message: print(message, flush=True)
worker = sys.argv.pop(1)
runpy.run_path(worker, run_name="__main__")
"""
        with subprocess.Popen(
            [sys.executable, "-c", runner, os.fspath(WORKER)],
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
        ) as worker:
            try:
                ready, _, _ = select.select([worker.stdout], [], [], 5)
                self.assertTrue(ready, "Worker failed to report startup")
                self.assertIn("foundation started", worker.stdout.readline())
                worker.send_signal(signal.SIGTERM)
                stdout, stderr = worker.communicate(timeout=5)
                self.assertEqual(worker.returncode, 0, stderr)
                self.assertIn("foundation stopped", stdout)
            finally:
                if worker.poll() is None:
                    worker.kill()


if __name__ == "__main__":
    unittest.main()
