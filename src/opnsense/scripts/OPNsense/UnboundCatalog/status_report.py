"""Compute read-only status from catalog snapshots and live Unbound state."""

from collections import defaultdict

from reconcile import ReconcileError, plan, retiring
from unbound import visible_stub


def report(settings, records, applied, outcome, resolver, listener, include_zones):
    retired = retiring(settings, applied, records)
    desired, catalog_conflicts = plan(settings.catalogs, records, applied, settings.disabled_zones, retired)
    conflicts = outcome.get("conflicts", {}) | catalog_conflicts
    resolver_error = outcome.get("error")
    pending = outcome.get("pending", False)
    stubs, insecure, validator = {}, set(), False
    live_ok = False
    try:
        stubs, insecure = resolver.runtime()
        validator = resolver.validation_enabled()
        live_ok = True
        persistent_ok = resolver.files_match(applied)
    except (OSError, ReconcileError, ValueError) as error:
        persistent_ok = False
        resolver_error = str(error)

    proposed_owners = defaultdict(set)
    for name, record in records.items():
        if name in settings.catalogs:
            for zone in record.get("members", {}).values():
                proposed_owners[zone].add(name)

    names = settings.catalogs.keys() | {name for stub in applied.values() for name in stub.owners}
    catalogs = {}
    for name in sorted(names):
        record = records.get(name, {})
        catalogs[name] = {
            "attempted": record.get("attempted"), "succeeded": record.get("succeeded"),
            "next": record.get("next"), "serial": record.get("serial"), "error": record.get("error"),
            "zones": 0, "conflicts": 0,
        }
    zones = applied.keys() | desired.keys() | proposed_owners.keys() | conflicts.keys()
    counts = dict.fromkeys(("managed", "active", "conflict", "pending"), 0)
    rows = []
    for zone in sorted(zones):
        old, new = applied.get(zone), desired.get(zone)
        expected = new or old
        owners = set(expected.owners) if expected else set()
        owners.update(proposed_owners[zone])
        errors = [catalogs[name]["error"] for name in sorted(owners & catalogs.keys()) if catalogs[name]["error"]]
        active = bool(
            live_ok and old and stubs.get(zone) == visible_stub(old)
            and (not validator or (zone in insecure) == old.insecure)
        )
        if zone in conflicts:
            state, error = "conflict", conflicts[zone]
        elif zone in settings.disabled_zones and old is None:
            state, error = "disabled", None
        elif zone in settings.disabled_zones or (owners and owners <= retired):
            state, error = ("error", resolver_error) if resolver_error else ("pending", None)
        elif not owners.intersection(settings.catalogs):
            state, error = "disabled", None
        elif resolver_error:
            state, error = "error", resolver_error
        elif errors:
            state, error = "stale", errors[0]
        elif pending or not active or new is None or old.effective != new.effective or not persistent_ok:
            state, error = "pending", None
        else:
            state, error = "active", None
        counts["managed"] += old is not None
        counts["active"] += active
        counts["conflict"] += state == "conflict"
        counts["pending"] += state in ("pending", "error")
        for name in owners & catalogs.keys():
            catalogs[name]["zones"] += bool(old and name in old.owners)
            catalogs[name]["conflicts"] += state == "conflict"
        if include_zones:
            proposed = sorted({address for name in owners & settings.catalogs.keys()
                               for address in settings.catalogs[name].stub_addresses})
            rows.append({
                "zone": zone,
                "catalog": ", ".join(sorted(owners)),
                "authoritative_servers": ", ".join(proposed if zone in conflicts or not expected else expected.addresses),
                "dnssec": expected.insecure if expected else None,
                "runtime_servers": ", ".join(stubs.get(zone, ((), False))[0]),
                "runtime_dnssec": zone in insecure if zone in stubs and validator else None,
                "status": state,
                "last_change": old.changed if old else None,
                "error": error,
            })

    if include_zones:
        return rows
    current = [catalogs[name] for name in settings.catalogs]
    failures = [entry for entry in current if entry["error"]]
    last_failure = max(failures, key=lambda entry: entry["attempted"] or 0, default={})
    return {
        "counts": counts,
        "catalog_status": catalogs,
        "listener": listener,
        "notify_enabled": settings.notify_enabled,
        "notify_families": int(settings.ipv4) + int(settings.ipv6),
        "notify_port": settings.notify_port,
        "attempted": max((record["attempted"] or 0 for record in current), default=0),
        "succeeded": max((record["succeeded"] or 0 for record in current), default=0),
        "next": min((record["next"] for record in current if record["next"]), default=None),
        "last_error": resolver_error or next(iter(conflicts.values()), None) or last_failure.get("error"),
        "configuration": (
            "error" if resolver_error else "conflict" if conflicts else
            "pending" if pending or not persistent_ok or desired != applied or counts["pending"] else "synced"
        ),
    }
