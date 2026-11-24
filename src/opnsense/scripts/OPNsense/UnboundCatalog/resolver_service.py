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
from reconcile import ReconcileError, Transaction, pack, plan
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


def snapshots():
    path = STATE / "catalogs/state.json"
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
    value = json.loads(raw)
    if value["version"] != 1:
        raise ReconcileError("Unsupported catalog snapshot version")
    return value["catalogs"]


def run(command):
    directory = STATE / "resolver"
    directory.mkdir(mode=0o750, parents=True, exist_ok=True)
    resolver = Unbound(UNBOUND, PERSISTENT, ACTIVE, TEMPLATE, directory)
    with (directory / "lock").open("a") as lock:
        fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        transaction = Transaction(resolver, directory, log)
        transaction.recover()
        old = transaction.current()
        desired, conflicts = {}, {}
        if command == "remove" and not old:
            return
        if command == "reconcile":
            settings = Settings.read(SETTINGS)
            desired, conflicts = plan(settings.catalogs, snapshots(), old)
        if not conflicts:
            conflicts = transaction.apply(desired, cleanup=command == "remove")
        outcome = directory / "outcome.json"
        report = {"desired": pack(desired), "conflicts": conflicts, "error": None}
        try:
            previous = load_json(outcome)
        except FileNotFoundError:
            previous = None
        if previous != report:
            atomic_json(outcome, report)
        if conflicts:
            log("warning", "; ".join(f"{zone} {message}" for zone, message in conflicts.items())[:500])
            if command == "remove":
                raise ReconcileError("Unbound has catalog ownership conflicts")


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("command", choices=("reconcile", "remove"))
    args = parser.parse_args()
    syslog.openlog("unboundcatalog", syslog.LOG_PID, syslog.LOG_DAEMON)
    try:
        run(args.command)
    except BlockingIOError:
        return 0
    except (OSError, ReconcileError, ValueError, TypeError, KeyError) as error:
        log("error", f"Unbound transaction failed: {error}")
        print(f"Unbound transaction failed: {error}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
