# Release and acceptance

## Build and publish

The build targets OPNsense 26.7 on FreeBSD 15.1 amd64. The native package
uses the official 26.7.9 plugin framework pinned to its source commit.
The FreeBSD CI guest resolves packages against the signed OPNsense
repository using the pinned OPNsense core fingerprint.

Build from a clean checkout on the target build guest with `git` and
the OPNsense Python packages installed. In a fresh FreeBSD CI guest, use
`--fetch-deps` to resolve those dependencies from the signed repository.

```sh
sh scripts/build-package.sh
ls dist/
```

The build produces an `os-unbound-catalog-VERSION.pkg` file and a
`.pkg.sha256` checksum. CI uploads both artifacts for review. Package
timestamps and repository dependency versions may vary between builds.
Record the tagged commit, build job, and verified checksum for every release.

After the milestone PRs are merged and native acceptance is recorded, tag
the reviewed commit on `main`. Push the version tag matching
`PLUGIN_VERSION` in the Makefile. The workflow checks that the commit
belongs to `main`, reruns source and DNS checks, builds the FreeBSD package,
verifies its SHA-256 checksum, and publishes both files to GitHub Releases.

## Automated tests

```sh
python -m pip install 'dnspython==2.9.2' 'jinja2==3.1.6'
python -m unittest discover -s tests -v
CATALOG_DNS_INTEGRATION=1 python -m unittest discover -s tests -p test_delegation.py -v
CATALOG_DNS_INTEGRATION=1 CATALOG_POWERDNS_INTEGRATION=1 \
  python -m unittest discover -s tests -p test_delegation.py -v
```

Run the opt-in integration test on an isolated Linux runner with BIND,
Unbound, IPv6 loopback and permission to bind port 53. CI provides an
isolated loopback network. The suite verifies an actual parent referral
and that Unbound recursively obtains correct NS, SOA, A, AAAA, MX, TXT,
CNAME, NXDOMAIN and NODATA answers from a separate child server. It checks
response codes, answer sections, authority sections and expected records.
It also covers IPv4 and IPv6 transfers, matching multi-catalog ownership,
forward conflicts, DNSSEC exceptions, duplicate NOTIFY, periodic transfer,
catalog and child-server outages, rollback, invalid, truncated and empty catalogs,
serial rollover and Unbound restart.

The PowerDNS test requires PowerDNS Authoritative with the Generic SQLite3
backend, `pdnsutil`, and `pdns_control`. It verifies a generated producer
catalog over AXFR, then triggers NOTIFY through PowerDNS. It checks that
Unbound serves a Local Data record in preference to PowerDNS, resolves
another name through the matching stub, and preserves its Local Zones,
Local Data, and host entries when the stub is removed. Run this test with
Unbound 1.26.2 to reproduce the OPNsense compatibility check. Set
`CATALOG_POWERDNS_SCHEMA` if the backend schema is installed outside
`/usr/share/doc/pdns-backend-sqlite3/schema.sqlite3.sql`. Set
`CATALOG_POWERDNS_MODULE_DIR` if testing an unpacked PowerDNS installation.

## Native acceptance

Run these checks on an isolated OPNsense 26.7 firewall. Keep an independent
authoritative parent and child available, with the parent serving an
RFC 9432 catalog. Use the test data in `tests/test_delegation.py` as a fixture.
If glue is private, allow the fixture namespace through
**Services → Unbound DNS → Advanced → Private Domains**.

1. Install the package and verify the five GUI tabs, enabled-state checks,
   configuration limits, scoped ACL, and native logs. Confirm that a fresh
   installation keeps Catalog Sync stopped. Register the locally installed
   package and verify it appears in Firmware. Enable the service with no
   catalogs and confirm that the service runs.
2. Configure the catalog, enable and apply, and verify the live stub and
   both persisted includes. Confirm final recursive DNS responses, including
   the SOA on negative responses. Run `configctl unbound check`.
3. Test catalog and member NOTIFY from allowed IPv4 and IPv6 addresses.
   Verify unknown names are refused, repeated notices are coalesced, and
   manual and scheduled transfers continue. Issue simultaneous reconcile
   requests and confirm no journal remains when the state is committed.
4. Restart and reload Unbound and retest the delegated answers. Reboot the
   firewall while the producer is unreachable and check that the applied
   stub persists. Restore the producer and confirm scheduled recovery.
5. Download and restore a standard configuration backup, then confirm the
   restored MVC settings and reconstruction from AXFR without old operational
   state.
6. Upgrade a running installation. Compare the previous Unbound PID and
   checksums of the applied state, generated include, and MVC configuration.
   Verify that the new worker is running and that delegated DNS still works.
7. Remove the package and check the removal of its worker, actions, runtime
   stubs, DNSSEC exceptions, and generated includes. Verify that unrelated
   DNS, persistent administrator settings, and native logging remain usable.
8. Verify that an administrator forward can conflict with one catalog zone
   while an unrelated zone becomes Active. Use the Zones actions to disable
   and enable the healthy zone, and confirm the changes after Apply.
9. Disable a catalog with active and conflicting zones, and verify both are
   withdrawn. Re-enable it with the producer unavailable and confirm
   that a new transfer is required. Delete the last catalog and verify
   that the service remains running with an empty Zones grid.
10. Restart with a legacy conflict record and a snapshot belonging to a
    removed catalog. Confirm that startup clears both records and leaves
    the administrator forward in Unbound.
11. Upgrade an appliance with an active catalog and the old generated
    include. Confirm that only the plugin-owned include moves to its private
    state directory. Check that Unbound's override notice clears, while a
    manually added override still triggers the notice.
12. Restart Unbound with the Catalog Sync worker stopped. Verify that the
    native restart hook restores committed stubs. Confirm that legacy
    five-second retry settings migrate to 300 seconds and scheduled AXFR
    attempts also wait 300 seconds after failure.
