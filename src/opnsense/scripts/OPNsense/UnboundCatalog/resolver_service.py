"""Fixed-purpose privileged configd entry point."""

import argparse
import fcntl
import json
import os
import pwd
import stat
import sys
import syslog
from pathlib import Path

# The package script directory is owned by root.
sys.path.insert(0, str(Path(__file__).resolve().parent))
from reconcile import ReconcileError, Transaction, plan, retiring
from status_report import report
from sync import STATE_LIMIT, Settings, atomic_json, load_json
from unbound import Unbound

STATE = Path("/var/db/unboundcatalog")
SETTINGS = Path("/var/run/unboundcatalog/settings.json")
UNBOUND = Path("/var/unbound/unbound.conf")
PERSISTENT = Path("/usr/local/etc/unbound.opnsense.d/catalog.conf")
ACTIVE = Path("/var/unbound/etc/catalog.conf")
TEMPLATE = Path("/usr/local/opnsense/service/templates/OPNsense/UnboundCatalog/stub-zones.j2")


def log(level, message):
    syslog.syslog(getattr(syslog, "LOG_" + {"error": "ERR", "warning": "WARNING", "info": "INFO"}[level]), message)


def worker_json(name):
    path = STATE / "catalogs" / name
    try:
        descriptor = os.open(path, os.O_RDONLY | os.O_NONBLOCK | os.O_NOFOLLOW)
    except FileNotFoundError:
        return {}
    with os.fdopen(descriptor, "rb") as stream:
        info = os.fstat(stream.fileno())
        if not stat.S_ISREG(info.st_mode) or info.st_uid != pwd.getpwnam("nobody").pw_uid:
            raise ReconcileError("Unexpected catalog snapshot file type or owner")
        raw = stream.read(STATE_LIMIT + 1)
    if len(raw) > STATE_LIMIT:
        raise ReconcileError("Catalog snapshot exceeds its limit")
    return json.loads(raw)


def snapshots():
    value = worker_json("state.json")
    if not value:
        return {}
    if value["version"] != 1:
        raise ReconcileError("Unsupported catalog snapshot version")
    return value["catalogs"]


def optional_json(path):
    try:
        return load_json(path)
    except FileNotFoundError:
        return {}


def have_listener():
    listener = worker_json("listener.json")
    try:
        pid = int(Path("/var/run/unbound_catalog_worker.pid").read_text())
        if pid <= 1:
            return []
        os.kill(pid, 0)
    except (OSError, ValueError):
        return []
    if listener.get("pid") == pid and isinstance(listener.get("families"), list):
        return sorted(set(listener["families"]) & {"AF_INET", "AF_INET6"})
    return []


def update_outcome(path, value):
    if optional_json(path) != value:
        atomic_json(path, value)


def eligible_snapshots(path, settings, records, retired, persist=False):
    previous = optional_json(path)
    configured = settings.configured_catalogs if settings.configured_catalogs is not None else settings.catalogs.keys()
    invalidated = {name: generation for name, generation in previous.items() if name in configured or name in records}
    for name in retired & records.keys():
        invalidated[name] = records[name].get("generation")
    eligible = {
        name: record for name, record in records.items()
        if name in settings.catalogs
        and (name not in invalidated or invalidated[name] != record.get("generation"))
    }
    for name in eligible:
        invalidated.pop(name, None)
    if persist and previous != invalidated:
        atomic_json(path, invalidated)
    return eligible


def run(command, wait=False):
    directory = STATE / "resolver"
    resolver = Unbound(UNBOUND, PERSISTENT, ACTIVE, TEMPLATE, directory)
    transaction = Transaction(resolver, directory, log)
    outcome = directory / "outcome.json"
    generations = directory / "invalidated_catalogs.json"
    if command in ("summary", "zones"):
        result = optional_json(outcome)
        result["pending"] = transaction.journal.exists()
        settings, records, old = Settings.read(SETTINGS), snapshots(), transaction.current()
        records = eligible_snapshots(generations, settings, records, retiring(settings, old, records))
        return report(settings, records, old, result, resolver, have_listener(), include_zones=command == "zones")

    directory.mkdir(mode=0o750, parents=True, exist_ok=True)
    with (directory / "lock").open("a") as lock:
        # Uninstall must finish after any configd request the stopped worker left in flight.
        flags = fcntl.LOCK_EX if wait or command == "remove" else fcntl.LOCK_EX | fcntl.LOCK_NB
        fcntl.flock(lock, flags)
        conflicts = {}
        try:
            transaction.recover()
            old = transaction.current()
            if command == "remove" and not old:
                return
            desired, disabled, retired = {}, frozenset(), frozenset()
            if command == "reconcile":
                settings, records = Settings.read(SETTINGS), snapshots()
                retired = retiring(settings, old, records)
                records = eligible_snapshots(generations, settings, records, retired, persist=True)
                disabled = settings.disabled_zones
                desired, conflicts = plan(settings.catalogs, records, old, disabled, retired)
            conflicts = transaction.apply(desired, cleanup=command == "remove", catalog_conflicts=conflicts,
                                          disabled=disabled, retired=retired)
            if conflicts:
                log("warning", "; ".join(f"{zone} {message}" for zone, message in conflicts.items())[:500])
                if command == "remove":
                    raise ReconcileError("Unbound has catalog ownership conflicts")
        except (OSError, ReconcileError, ValueError, TypeError, KeyError) as error:
            update_outcome(outcome, {"conflicts": conflicts, "error": str(error)[:500]})
            raise
        update_outcome(outcome, {"conflicts": conflicts, "error": None})


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("command", choices=("reconcile", "remove", "summary", "zones"))
    parser.add_argument("--wait", action="store_true")
    args = parser.parse_args()
    syslog.openlog("unboundcatalog", syslog.LOG_PID, syslog.LOG_DAEMON)
    try:
        result = run(args.command, wait=args.wait)
        if result is not None:
            print(json.dumps(result))
    except BlockingIOError:
        return 0
    except (OSError, ReconcileError, ValueError, TypeError, KeyError) as error:
        log("error", f"Unbound transaction failed: {error}")
        print(f"Unbound transaction failed: {error}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
