"""Plan and recover catalog-owned resolver changes."""

import time
from collections import defaultdict
from dataclasses import dataclass

from catalog import dns_name
from sync import atomic_json, load_json


class ReconcileError(RuntimeError):
    pass


@dataclass(frozen=True)
class Stub:
    addresses: tuple[str, ...]
    insecure: bool
    owners: dict[str, str]
    changed: int = 0

    @property
    def effective(self):
        return self.addresses, self.insecure


def unpack(data):
    return {
        dns_name(name).to_text(): Stub(
            tuple(item["addresses"]), item["insecure"], item["owners"], item["changed"],
        )
        for name, item in data.items()
    }


def pack(zones):
    return {
        name: {"addresses": list(stub.addresses), "insecure": stub.insecure, "owners": stub.owners, "changed": stub.changed}
        for name, stub in zones.items()
    }


def plan(settings, records, current):
    proposals = defaultdict(list)
    fresh = {
        name for name in settings
        if "members" in records.get(name, {}) and not records[name].get("error")
    }
    for zone, old in current.items():
        remaining = {owner: label for owner, label in old.owners.items() if owner not in fresh}
        if remaining:
            proposals[zone].append(Stub(old.addresses, old.insecure, remaining))

    for name in sorted(fresh):
        catalog = settings[name]
        addresses = catalog.stub_addresses
        if not addresses:
            raise ReconcileError(f"{name} has no authoritative addresses")
        for label, target in records[name]["members"].items():
            zone = dns_name(target).to_text()
            proposals[zone].append(Stub(addresses, catalog.insecure, {name: label}))

    result, conflicts = {}, {}
    for zone, entries in sorted(proposals.items()):
        first = entries[0]
        owners = {owner: member for item in entries for owner, member in item.owners.items()}
        if any(item.effective != first.effective for item in entries):
            conflicts[zone] = "Different catalog settings for " + ", ".join(sorted(owners))
            continue
        changed = int(time.time())
        if zone in current and current[zone].effective == first.effective and current[zone].owners == owners:
            changed = current[zone].changed
        result[zone] = Stub(first.addresses, first.insecure, owners, changed)
    return result, conflicts


class Transaction:
    def __init__(self, resolver, directory, log):
        self.resolver = resolver
        self.log = log
        self.applied = directory / "applied.json"
        self.journal = directory / "transaction.json"

    def current(self):
        try:
            return unpack(load_json(self.applied))
        except FileNotFoundError:
            return {}

    def recover(self):
        try:
            data = load_json(self.journal)
        except FileNotFoundError:
            return
        old = unpack(data["previous"])
        new = unpack(data["desired"])
        self.log("warning", "Recovering interrupted Unbound transaction")
        # Once the commit is durable only the journal unlink can remain.
        goal, other = (new, old) if self.current() == new else (old, new)
        self.resolver.transition(other, goal, recovering=True)
        atomic_json(self.applied, pack(goal))
        self.journal.unlink()

    def apply(self, desired, cleanup=False):
        self.recover()
        old = self.current()
        conflicts = self.resolver.conflicts(old, desired, cleanup=cleanup)
        if conflicts:
            return conflicts
        if self.resolver.matches(desired, previous=old):
            if old != desired:
                atomic_json(self.applied, pack(desired))
            return {}
        self.resolver.validate(old, desired)
        atomic_json(self.journal, {"previous": pack(old), "desired": pack(desired)})
        try:
            self.resolver.transition(old, desired)
            atomic_json(self.applied, pack(desired))
        except (OSError, ReconcileError, ValueError):
            self.log("warning", "Rolling back catalog-owned Unbound changes")
            self.recover()
            raise
        self.journal.unlink()
        for zone in sorted(old.keys() | desired.keys()):
            if old.get(zone) != desired.get(zone):
                outcome = "removed" if zone not in desired else "added" if zone not in old else "updated"
                self.log("info", f"{zone} managed stub {outcome}")
        return {}
