# Changelog

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
