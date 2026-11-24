# opnsense-unbound-catalog

DNS Catalog Zone synchronisation for Unbound on OPNsense (RFC 9432)

Version 0.1.0 provides the native configuration and service foundation for
OPNsense 26.7 on FreeBSD 15.1 amd64. It adds **Services → Unbound DNS →
Catalog Sync** with five tabs, catalog and service settings, and native logging.
The worker is disabled by default. AXFR, DNS NOTIFY, and Unbound stub zone
management are planned for later milestones.

- [Installation, configuration, testing, and removal](docs/installation.md)
- [Architecture and upstream interfaces](docs/architecture.md)
- [Changelog](CHANGELOG.md)
- [Contributing](CONTRIBUTING.md)
- [Security](SECURITY.md)
- [License](LICENSE)
