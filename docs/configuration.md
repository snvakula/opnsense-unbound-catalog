# Configuration

## Catalogs and settings

Each enabled catalog needs its RFC 9432 zone name, one or more transfer
addresses, and one or more authoritative addresses. Use numeric IPv4 or
IPv6 addresses and enter each as a separate token in the native form. The
two ports default to 53. List the source IP addresses or CIDRs permitted to
send NOTIFY for this catalog. An empty list allows periodic transfers
while dropping incoming NOTIFY for the catalog.

Enable Catalog Sync under **Settings**, save, and apply. The service can run
with an empty catalog list. The synchronization
interval defaults to 300 seconds. Each AXFR attempt has a 10-second lifetime.
Successful and failed attempts both schedule their next AXFR after 300
seconds by default. Set a longer interval or retry delay in Settings if
needed. Upgrades raise shorter saved intervals to 300 seconds. An initial
start, authorized catalog NOTIFY, and **Synchronize now** can initiate
transfers outside the periodic schedule. The default
transfer limits are 10,000 records and 4 MiB on the wire. DNS NOTIFY defaults
to UDP 1053, a two-second debounce, and both address families. Each catalog
supports up to 16 transfer addresses, 16 authoritative addresses and 64
NOTIFY sources. The service supports up to 64 catalogs.

Per-catalog DNSSEC exceptions are off by default. Turn one on only for a
trusted unsigned namespace when needed. Existing global and administrator
DNSSEC settings govern the rest of the resolver. If a delegated nameserver's
glue uses RFC 1918 or other private addresses, allow its namespace with
Unbound's native **Private Domains** setting. Keep its DNS rebinding
protection enabled for other names.

Disabling or deleting a catalog and applying the change withdraws its
zones, including conflicts. A zone shared with another catalog keeps that
catalog's ownership. Re-enabling a catalog requires a new successful AXFR.
Turn off **Enable Catalog Sync** and select **Apply** to withdraw all
plugin-managed zones and their DNSSEC exceptions. Re-enabling the module
requires a fresh AXFR. The service widget's **Stop** action pauses
synchronization and retains its applied zones. On startup, Catalog
Sync discards saved snapshots for disabled or deleted catalogs and clears
conflict records that no longer belong to any catalog.

To exclude a zone across all catalogs, clear its checkbox in the **Enabled**
column on the **Zones** tab and select **Apply**. Check it and apply again to
restore it. Existing administrator DNS entries remain in place. Conflicted
zones stay visible with their error details while other zones are applied.

## Firewall and authoritative server access

| Direction | Protocol | Destination | Purpose |
| --- | --- | --- | --- |
| Firewall to producer | TCP | Configured transfer port, typically 53 | Catalog AXFR |
| Producer to firewall | UDP | Configured NOTIFY port, typically 1053 | Catalog update hint |
| Firewall to authoritative servers | UDP and TCP | Configured member authority port | Initial member queries |
| Firewall to delegated servers | UDP and TCP | Port 53 | Recursion beneath member stubs |

Limit unsigned AXFR to trusted networks and the firewall's transfer source
addresses. Restrict incoming NOTIFY in both the firewall rules and the plugin
source allowlist. The plugin creates no firewall rules. Multiple transfer
servers are tried in the order entered until a complete catalog validates.

A known catalog NOTIFY schedules AXFR after debouncing. A known member
NOTIFY is acknowledged and its ordinary TTL behavior continues. An
authorized NOTIFY for an unknown name gets REFUSED. Unauthorized, malformed,
rate-limited, and unsupported packets are dropped. Polling continues
independently of NOTIFY.

In **Log File**, AXFR entries marked `push` show the DNS NOTIFY sender that
prompted the transfer. Entries marked `pull` show what prompted Catalog Sync
to initiate it (startup, periodic poll, retry, or **Synchronize now**).
Both use an outbound AXFR request. Successful entries also show the transfer
server, which may differ from the NOTIFY sender.

## PowerDNS Authoritative

For PowerDNS 5.x, configure a catalog producer and add a member zone already
present on the authoritative server. The producer's apex needs an SOA and
NS RRset. The backend must support catalog generation.

```sh
pdnsutil zone create catalog.example.net ns1.example.net
pdnsutil zone set-kind catalog.example.net producer
pdnsutil zone set-kind example.net primary
pdnsutil catalog set example.net catalog.example.net
```

Allow transfer of the catalog to the firewall and configure
`ALSO-NOTIFY` on the producer when using NOTIFY. PowerDNS versions earlier
than 5.0 use the legacy `pdnsutil set-kind` and `pdnsutil set-catalog`
commands. Confirm the final command syntax and supported backend for your
version. [PowerDNS Catalog documentation](https://doc.powerdns.com/authoritative/catalog.html)

## BIND and Knot DNS

BIND can serve an ordinary primary zone containing the RFC 9432 records.
This covers deployments that generate the zone file with a provisioning
system. Add a source-restricted `allow-transfer` to the catalog zone, and
an `also-notify` destination that specifies the plugin UDP port if desired.

```dns
$ORIGIN catalog.example.net.
$TTL 60
@       IN SOA ns1.example.net. hostmaster.example.net. 1 60 60 600 60
@       IN NS  ns1.example.net.
version IN TXT "2"
m1.zones IN PTR example.net.
```

With Knot DNS, use `catalog-role: generate` for the producer zone and
`catalog-role: member` with `catalog-zone` pointing to the producer on
the member zones. Restrict transfers with Knot ACLs and configure a NOTIFY
remote to the firewall when required. PowerDNS and Knot examples describe
producer setup. The integration suite exercises BIND. [Knot DNS catalog configuration](https://www.knot-dns.cz/docs/latest/html/configuration.html)

Change-of-ownership records are rejected, and unrecognized group values are
ignored. The plugin applies the catalog's configured endpoint and DNSSEC
policy to its members. TSIG is outside the current release scope.

## Troubleshooting

- **Worker stopped.** Verify the global enable switch. Check the native Log File tab and
  `configctl unboundcatalog status`.
- **Stale catalog.** Check TCP access, producer AXFR ACLs, and the configured
  catalog name. From an authorized host, run
  `dig @192.0.2.10 -p 53 catalog.example.net. AXFR` and check the SOA
  boundaries, version record and member PTRs.
- **Conflict.** Find the exact name in Zones and check Query Forwarding,
  Domain Overrides, custom Unbound includes, and other catalogs. Align the
  catalogs' authorities and DNSSEC policies or resolve the administrator
  entry before asking Catalog Sync to retry.
- **Runtime pending or error.** Run `configctl unbound check`, confirm the
  resolver is running, and compare `/var/db/unboundcatalog/resolver/catalog.conf`
  with `/var/unbound/etc/catalog.conf` without editing them. Use **Synchronize now** after resolving the error.
- **SERVFAIL under a delegation.** Make sure the child servers are reachable
  over UDP and TCP 53 and that their glue survives DNS rebinding filtering.
  Check the DNSSEC chain, then test both the parent referral and the answer
  from each child server.
