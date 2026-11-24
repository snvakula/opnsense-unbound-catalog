# Architecture

## Milestone 0.1.0

The foundation owns one versioned MVC model, one menu entry under Unbound DNS,
one service, and one native log scope. Its only worker lifecycle events are
startup and shutdown. The worker runs as FreeBSD's `nobody` account and requires
only the Python standard library. The native service is disabled by default.
It starts only when the global enable switch is set and at least one enabled
catalog has a name and both server lists.

Settings live under `//OPNsense/UnboundCatalog`, so they participate in standard
configuration backup and restore. The OPNsense template engine renders only a
fixed service enable value and a supported log level into
`/etc/rc.conf.d/unbound_catalog`. The template receives configuration through
configd. The plugin has a boot hook and specific configd actions. Apply
regenerates the rc settings and restarts or stops the worker under a lifecycle
lock. Local components use neither HTTP credentials nor direct config XML
editing. Catalog delete uses the native confirmation dialog. Disabling or
deleting a catalog affects worker eligibility when applied. This release
creates no managed zones or DNSSEC exceptions.

Keep the eventual DNS protocol code independent of OPNsense. Future milestones
will add AXFR, persistent snapshots and periodic scheduling, then NOTIFY, then
Unbound reconciliation. The 0.1.0 UI identifies those capabilities as
unavailable and only reports service state that can be measured locally.
Native syslog-ng handles storage, filtering, search and rotation.

## Implementation sequence

1. Define and validate the model, CRUD API, ACL and nested menu.
2. Wire the native rc service, boot hook, template, backend actions, and
   package lifecycle. Keep fresh installation disabled.
3. Build the five tabs with native forms, grids, service controls and logs.
4. Add source checks, lifecycle tests, a FreeBSD package build job, and a
   native OPNsense acceptance procedure. Review this milestone before adding
   DNS behavior.

## Verified interfaces and follow-up

OPNsense core exposes service hooks through `plugins.inc.d`, templating
through `configctl template reload`, native service and model controller bases,
and a standard log viewer backed by `OPNsense/Syslog/local` filters.
[OPNsense core 26.7.9](https://github.com/opnsense/core/tree/26.7.9)

The official plugin tree uses `Mk/plugins.mk` for staging, configd registration,
migrations, generated template reloads and `pkg create`.
[OPNsense plugins 26.7.9](https://github.com/opnsense/plugins/tree/26.7.9)

The build configuration for OPNsense 26.7 specifies FreeBSD 15.1 and Python 3.13.
[OPNsense build configuration](https://github.com/opnsense/tools/tree/master/config/26.7)

For the future Unbound milestone, OPNsense documents
`/usr/local/etc/unbound.opnsense.d/` as the persistent include directory. Its
26.7.9 startup script copies those files to `/var/unbound/etc/`, which the
main configuration includes. A write to the persistent directory alone is
therefore insufficient to update the live copy. The core
`configctl unbound check` wrapper runs from `/var/unbound` and pipes output
through `sed`. A transaction should also run `unbound-checkconf` directly from
that directory and check its exit code. [OPNsense Unbound documentation](https://docs.opnsense.org/manual/unbound.html#advanced-configurations)

The Unbound manual documents `stub_add [+ipt] zone addr …`, `stub_remove [+i] zone`,
`list_stubs`, `list_forwards`, `list_local_zones`, `list_auth_zones`,
`list_insecure`, `insecure_add`, `insecure_remove` and `flush_zone`.
[Unbound control manual](https://unbound.docs.nlnetlabs.nl/en/latest/manpages/unbound-control.html)

Use `unbound-control -c /var/unbound/unbound.conf` on OPNsense. Verify syntax,
output formats, fail-closed defaults, privilege separation and restart behavior
on the installed resolver before implementing a transaction. Native Jinja
targets are written directly so future candidate generation and installation
will need an atomic staging boundary, a rollback journal and runtime
verification. Never render unchecked AXFR data directly into an active include.

Catalog membership must eventually become stub zones so Unbound can follow
normal delegations. The catalog supplies membership. Member data still comes
from authoritative servers and follows normal TTLs. Use
[RFC 9432](https://www.rfc-editor.org/rfc/rfc9432) and
[RFC 1996](https://www.rfc-editor.org/rfc/rfc1996) for the later protocol work.

The two BSD-2-Clause Kea plugins, `tkreagan/os-kea-ubnd-ddns` and
`JameZUK/os-kea-unbound`, show different approaches to restart reconciliation
and native DDNS integration. Their input is Kea lease data. The community
`Opnwall/opnsense-repo` provides examples of repository packaging for
FreeBSD 15 amd64, with a repository-level MIT license. This foundation uses
official upstream conventions and incorporates no third-party source.

FreeBSD package construction verifies package metadata and ABI. Native
functional checks run on OPNsense. The test procedure and lab coverage are
recorded in the installation guide. [Native acceptance](installation.md#native-acceptance)
