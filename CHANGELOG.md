# Changelog

## 1.2.0

- Use standard OPNsense table commands and icon-only zone controls.
- Keep the generated persistent include outside the manual override directory.
- Schedule automatic AXFR attempts at a minimum of five minutes.
- Withdraw all managed zones when the module is disabled and applied.
- Document GitHub Releases and the package upgrade path.

## 1.1.0

- Apply healthy zones independently of conflicts.
- Add per-zone exclusions to the Zones tab.
- Start the enabled service even with an empty catalog list.
- Discard stale catalog snapshots and ownerless conflict records.
- Withdraw catalog-owned zones when the catalog is disabled or deleted and
  require a fresh transfer on reactivation.

## 1.0.0

- Added a signed-repository FreeBSD build and tagged release workflow with SHA-256 checksums.
- Documented catalog producers, firewall flows, cache behavior, upgrade, recovery, and uninstallation.
- Expanded real DNS coverage for multi-catalog ownership, conflicts, interruptions, and source outages.
- Enforced server and source list limits in the native model and made shutdown interrupt active transfers.

## 0.5.0

- Added live resolver, membership, listener, and synchronization status to the native GUI.
- Added immediate synchronization through the existing worker and native service controls.
- Reconcile saved configuration at startup before attempting the next transfers.

## 0.4.0

- Generate and validate plugin-owned Unbound Stub Zone configuration.
- Reconcile runtime changes with recovery and targeted cache invalidation.
- Preserve applied configuration on catalog outages and report exact-name conflicts.
- Verify delegated recursive DNS answers with BIND and Unbound.

## 0.3.0

- Added source-restricted dual-stack DNS NOTIFY with debouncing, rate limits,
  and member-zone acknowledgements.

## 0.2.0

- Added bounded IPv4 and IPv6 AXFR, RFC 9432 validation, periodic polling,
  server failover, retry settings and persistent catalog snapshots.

## 0.1.0

- Added native OPNsense MVC configuration and validation for global settings
  and multiple catalogs.
- Added the Catalog Sync menu, five GUI tabs, service controls and an ACL.
- Added configd actions, an unprivileged foundation worker and native logging.
- Added native package metadata, installation and removal hooks, and CI for
  source checks and FreeBSD package artifacts.
