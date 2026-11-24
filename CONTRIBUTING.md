# Contributing

Use the roadmap in [architecture.md](docs/architecture.md) to keep changes
reviewable. Changes to DNS or Unbound behavior need tests for failure paths and
must preserve the last known good state. Use example domains and documentation
address ranges in fixtures.

Run the checks in [.github/workflows/ci.yml](.github/workflows/ci.yml). Report the
OPNsense, FreeBSD and Unbound versions when testing on a firewall. See the
[native acceptance checklist](docs/installation.md#native-acceptance).
