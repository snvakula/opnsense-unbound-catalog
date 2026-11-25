# OPNsense Unbound Catalog

Catalog Sync uses RFC 9432 catalog zones to manage Unbound Stub Zones on
OPNsense 26.7 amd64. It transfers catalog membership over TCP using AXFR.
Unbound obtains member-zone records directly from authoritative servers and
caches them according to ordinary DNS TTLs. Stub Zones let Unbound follow
delegations beneath a member zone. Forward Zones send recursive queries to
an upstream resolver.

After installing the native package, open **Services → Unbound DNS → Catalog
Sync**. The service starts after you enable it and apply the settings,
even with zero catalogs. It polls enabled catalogs and accepts
source-restricted DNS NOTIFY over IPv4 and IPv6. The Zones tab shows
conflicts alongside active zones and lets you exclude individual zones.
Disabling or deleting a catalog withdraws its zones on Apply.

- [Installation, upgrade, backup, and removal](docs/installation.md)
- [Configuration, producer examples, firewall rules, and troubleshooting](docs/configuration.md)
- [Architecture and recovery](docs/architecture.md)
- [Release and acceptance procedure](docs/release.md)
- [Changelog](CHANGELOG.md)
- [Contributing](CONTRIBUTING.md)
- [Security](SECURITY.md)
- [License](LICENSE)
