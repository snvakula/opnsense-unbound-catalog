# Architecture

## Configuration and process boundaries

The versioned native MVC model stores settings under
`//OPNsense/UnboundCatalog`. The existing OPNsense ACL protects the Web
GUI and API. Native configd renders `/etc/rc.conf.d/unbound_catalog` and
`/var/run/unboundcatalog/settings.json`. Root owns the rendered settings and
grants the worker read access. Service registration and startup use
`plugins.inc.d` and a FreeBSD rc script. OPNsense syslog-ng handles filtering,
search, severity and rotation.

The unprivileged worker runs as `nobody`. It uses dnspython to validate a
bounded TCP AXFR stream and a thread to receive RFC 1996 NOTIFY on separate
IPv4 and IPv6 sockets. Transfers validate SOA boundaries, version, member
records, duplicates, and limits before replacing a snapshot. Short
NOTIFY requests schedule work independently of the transfer thread.
The worker retains the last validated catalog on failure and compares
normalized membership on each valid transfer, including after serial
rollover.

The root helper accepts a fixed configd action with no user-supplied shell
arguments. It reads regular worker-owned snapshots through an input size
limit, validates zone names, and derives authoritative addresses from the
root-owned settings. It controls Unbound through the native control
socket and has exclusive access to the applied-state and transaction records.

## Resolver integration and recovery

The persistent include is `/usr/local/etc/unbound.opnsense.d/catalog.conf`.
The OPNsense Unbound startup script copies it into `/var/unbound/etc`.
Reconciliation renders a candidate using the plugin's Jinja template and
validates both live and next-start include sets with `unbound-checkconf`
from `/var/unbound`. It leaves core-generated and administrator includes
untouched.

The helper detects exact-name conflicts in resolved configuration and
Unbound's live stub, forward, local, and authoritative zones. Parent and
child names can coexist. Identical catalog contributions share ownership.
Differing contributions block the pending configuration transaction and keep
the previous state. A catalog failure retains its previous ownership and
endpoints.

Reconciliation holds an exclusive file lock. It journals the previous and
desired state before any change, applies runtime deltas with
`unbound-control -c /var/unbound/unbound.conf`, updates the two owned files
atomically, checks both files and live state, and commits applied state.
An interrupted transaction rolls back unless its final applied-state
record is already durable. A scoped `flush_zone` invalidates cached data
when an affected stub or DNSSEC policy changes. Routine reconciliation
does not restart Unbound.

Unbound's `list_stubs` reports runtime addresses without port numbers.
The helper verifies ports in the generated include and explicitly updates
the runtime stub on configured port changes. Runtime-only changes limited
to a port cannot be observed through that command. The DNS acceptance
test uses a non-default parent port and verifies actual queries.

Durable catalog snapshots and applied state are stored separately under
`/var/db/unboundcatalog`. At startup and during periodic checks, the worker
reconciles with current runtime state. Unbound startup can load the
persistent include even if the producer is unavailable. A restore that
only contains settings populates operational state after a valid transfer.

## Upstream interfaces

- [OPNsense core 26.7.9](https://github.com/opnsense/core/tree/26.7.9)
- [OPNsense plugins 26.7.9](https://github.com/opnsense/plugins/tree/26.7.9)
- [OPNsense Unbound advanced configuration](https://docs.opnsense.org/manual/unbound.html#advanced-configurations)
- [Unbound control manual](https://unbound.docs.nlnetlabs.nl/en/latest/manpages/unbound-control.html)
- [RFC 9432 DNS Catalog Zones](https://www.rfc-editor.org/rfc/rfc9432)
- [RFC 1996 DNS NOTIFY](https://www.rfc-editor.org/rfc/rfc1996)
