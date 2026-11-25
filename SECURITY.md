# Security

Report suspected security issues privately through the repository's GitHub
security advisory interface when available. Keep credentials, infrastructure
addresses, configuration backups, and packet captures out of public reports.

Use authenticated management access to OPNsense and restrict the native
Catalog Sync privilege to the appropriate administrators. The package uses
only fixed, local configd actions. Native ACL checks also govern per-zone
exclusions, which are stored in the appliance configuration. Protocol
processing runs as `nobody`.
A root-owned configd helper validates catalog-derived names and applies
changes using root-owned settings and fixed Unbound command arguments.

Unsigned AXFR and NOTIFY depend on network access controls. Use trusted
producer networks, restrict AXFR to the firewall addresses, and restrict
NOTIFY by firewall rules and per-catalog source allowlists. The runtime
listener limits request rates and the number of tracked sources. Monitor
native logs for repeated rejected packets and transfer failures.
See the [firewall configuration](docs/configuration.md#firewall-and-authoritative-server-access).
