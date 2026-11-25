"""Catalog configuration, snapshots, and independent periodic polling."""

import ipaddress
import json
import os
import tempfile
import time
import uuid
from dataclasses import dataclass
from pathlib import Path
from threading import Lock

from catalog import CatalogError, dns_name, transfer
from dns.exception import DNSException

STATE_LIMIT = 32 * 1024 * 1024
MAX_CATALOGS = 64
MAX_SERVERS = 16
MAX_NOTIFY_SOURCES = 64


def load_json(path, limit=STATE_LIMIT):
    with path.open("rb") as stream:
        data = stream.read(limit + 1)
    if len(data) > limit:
        raise ValueError("State or configuration exceeds its size limit")
    return json.loads(data)


def atomic_bytes(path, payload, mode=0o640):
    fd, temporary = tempfile.mkstemp(dir=path.parent, prefix=f".{path.name}.")
    try:
        with os.fdopen(fd, "wb") as stream:
            os.fchmod(stream.fileno(), mode)
            stream.write(payload)
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(temporary, path)
        directory = os.open(path.parent, os.O_RDONLY | os.O_DIRECTORY)
        try:
            os.fsync(directory)
        finally:
            os.close(directory)
    finally:
        Path(temporary).unlink(missing_ok=True)


def atomic_json(path, data):
    payload = json.dumps(data, sort_keys=True, separators=(",", ":")).encode()
    if len(payload) > STATE_LIMIT:
        raise ValueError("State exceeds its size limit")
    atomic_bytes(path, payload)


def bounded_int(value, default, lower, upper):
    result = int(default if value in (None, "") else value)
    if not lower <= result <= upper:
        raise ValueError("Numeric configuration outside supported range")
    return result


def flag(value, default=False):
    if value is None:
        return default
    if value not in ("0", "1"):
        raise ValueError("Invalid Boolean configuration")
    return value == "1"


def addresses(value):
    result = tuple(dict.fromkeys(str(ipaddress.ip_address(item.strip())) for item in value.split(",")))
    if len(result) > MAX_SERVERS or any("%" in host for host in result):
        raise ValueError("Too many addresses or unsupported IPv6 scope")
    return result


@dataclass(frozen=True)
class Catalog:
    name: str
    transfers: tuple[str, ...]
    transfer_port: int
    authorities: tuple[str, ...]
    authoritative_port: int
    insecure: bool
    sources: tuple = ()

    @property
    def stub_addresses(self):
        return tuple(sorted({
            address if self.authoritative_port == 53 else f"{address}@{self.authoritative_port}"
            for address in self.authorities
        }))


@dataclass(frozen=True)
class Settings:
    catalogs: dict[str, Catalog]
    interval: int = 300
    timeout: int = 10
    max_records: int = 10000
    max_bytes: int = 4 * 1024 * 1024
    retry_initial: int = 300
    retry_max: int = 300
    notify_enabled: bool = True
    notify_port: int = 1053
    debounce: int = 2
    ipv4: bool = True
    ipv6: bool = True
    disabled_zones: frozenset[str] = frozenset()
    disabled_catalogs: frozenset[str] = frozenset()
    configured_catalogs: frozenset[str] | None = None
    enabled: bool = True

    def retired_catalogs(self, names):
        if not self.enabled:
            return self.disabled_catalogs | set(names)
        if self.configured_catalogs is None:
            return self.disabled_catalogs
        return self.disabled_catalogs | (set(names) - self.configured_catalogs)

    @classmethod
    def read(cls, path):
        config = load_json(path, 256 * 1024)
        general = config["general"]
        catalogs = {}
        if len(config["catalogs"]) > MAX_CATALOGS:
            raise ValueError("Too many configured catalogs")
        configured, disabled = set(), set()
        for row in config["catalogs"]:
            if row.get("name"):
                name = dns_name(row["name"]).to_text()
                configured.add(name)
                if not flag(row.get("enabled")):
                    disabled.add(name)
        enabled = flag(general.get("enabled"))
        if enabled:
            for row in config["catalogs"]:
                if not flag(row.get("enabled")):
                    continue
                name = dns_name(row["name"]).to_text()
                if name in catalogs:
                    raise ValueError("Duplicate catalog")
                sources = tuple(dict.fromkeys(
                    ipaddress.ip_network(value.strip(), strict=False)
                    for value in (row.get("notify_sources") or "").split(",") if value.strip()
                ))
                if len(sources) > MAX_NOTIFY_SOURCES or any("%" in str(source) for source in sources):
                    raise ValueError("Too many NOTIFY sources or unsupported IPv6 scope")
                catalogs[name] = Catalog(
                    name,
                    addresses(row["transfer_servers"]),
                    bounded_int(row.get("transfer_port"), 53, 1, 65535),
                    addresses(row["authoritative_servers"]),
                    bounded_int(row.get("authoritative_port"), 53, 1, 65535),
                    flag(row.get("disable_dnssec")),
                    sources,
                )
        # Old appliance backups can still supply the former five-second retry.
        initial = max(300, bounded_int(general.get("retry_initial"), 300, 1, 3600))
        return cls(
            catalogs,
            max(300, bounded_int(general.get("sync_interval"), 300, 10, 86400)),
            bounded_int(general.get("axfr_timeout"), 10, 1, 300),
            bounded_int(general.get("max_records"), 10000, 3, 100000),
            bounded_int(general.get("max_bytes"), 4194304, 512, 33554432),
            initial,
            max(initial, bounded_int(general.get("retry_max"), 300, 1, 3600)),
            flag(general.get("notify_enabled"), True),
            bounded_int(general.get("notify_port"), 1053, 1024, 65535),
            bounded_int(general.get("notify_debounce"), 2, 0, 300),
            flag(general.get("listen_ipv4"), True),
            flag(general.get("listen_ipv6"), True),
            frozenset(dns_name(zone).to_text() for zone in (config.get("disabled_zones") or "").split(",") if zone),
            frozenset(disabled),
            frozenset(configured),
            enabled,
        )


class Poller:
    def __init__(self, settings, state_path, logger, fetch=transfer, clock=time.monotonic, wall=time.time):
        self.settings = settings
        self.path = state_path
        self.log = logger
        self.fetch = fetch
        self.clock = clock
        self.wall = wall
        try:
            saved = load_json(state_path)
            if saved["version"] != 1:
                raise ValueError("Unsupported snapshot format")
        except FileNotFoundError:
            saved = {"catalogs": {}}
        records = saved["catalogs"]
        retired = settings.retired_catalogs(records)
        if retired & records.keys():
            records = {name: record for name, record in records.items() if name not in retired}
            atomic_json(state_path, {"version": 1, "catalogs": records})
        self.state = {name: records.get(name, {}) for name in settings.catalogs}
        self.due = dict.fromkeys(settings.catalogs, (self.clock(), "pull (startup)"))
        self.failures = dict.fromkeys(settings.catalogs, 0)
        self.requests = {}
        self.request_lock = Lock()
        self._index_members()

    def _index_members(self):
        members = {}
        for catalog, entry in self.state.items():
            for zone in entry.get("members", {}).values():
                members.setdefault(zone, []).append(catalog)
        self.member_owners = members

    def request(self, name, source):
        with self.request_lock:
            self.requests.setdefault(name, (
                self.clock() + self.settings.debounce, f"push (DNS NOTIFY from {source})",
            ))

    def _schedule_requests(self):
        with self.request_lock:
            for name, requested in self.requests.items():
                if not self.failures[name] and requested[0] < self.due[name][0]:
                    self.due[name] = requested
            self.requests.clear()

    def synchronize(self):
        with self.request_lock:
            self.requests.clear()
            self.due = dict.fromkeys(self.settings.catalogs, (self.clock(), "pull (manual sync)"))

    def next_delay(self):
        now = self.clock()
        return max(0, min((deadline for deadline, _ in self.due.values()), default=now + 60) - now)

    def run_due(self):
        self._schedule_requests()
        ran = False
        for name, (due, trigger) in self.due.items():
            if due <= self.clock():
                self._poll(self.settings.catalogs[name], trigger)
                ran = True
        return ran

    def _poll(self, catalog, trigger):
        name = catalog.name
        previous = self.state[name]
        current = dict(previous, attempted=int(self.wall()))
        errors = []
        context = f"{name} AXFR [{trigger}]"
        self.log("debug", f"{context} started")
        for server in catalog.transfers:
            try:
                snapshot = self.fetch(
                    name, server, catalog.transfer_port, self.settings.timeout,
                    self.settings.max_records, self.settings.max_bytes,
                )
            except (CatalogError, DNSException, OSError) as error:
                errors.append(f"{server}: {error}")
                continue
            self.failures[name] = 0
            current.update(members=snapshot.members, serial=snapshot.serial, succeeded=int(self.wall()),
                           generation=uuid.uuid4().hex, error=None)
            delay = self.settings.interval
            next_trigger = "pull (periodic poll)"
            self.log("info", f"{context} from {server} succeeded, serial {snapshot.serial}, "
                     f"{len(snapshot.members)} members")
            break
        else:
            failure = min(self.failures[name] + 1, 12)
            self.failures[name] = failure
            delay = min(self.settings.retry_max, self.settings.retry_initial * 2 ** (failure - 1))
            current["error"] = " ".join(" ".join(errors).split())[:500] or "No transfer server configured"
            next_trigger = "pull (retry)"
            self.log("warning", f"{context} failed: {current['error']}")
        self.due[name] = (self.clock() + delay, next_trigger)
        current["next"] = int(self.wall() + delay)
        updated = dict(self.state, **{name: current})
        atomic_json(self.path, {"version": 1, "catalogs": updated})
        self.state = updated
        self._index_members()
        with self.request_lock:
            self.requests.pop(name, None)
