# Installation and operation

## Install

Use OPNsense 26.7 amd64, which is based on FreeBSD 15.1. Native checks use
OPNsense 26.7 Nano and its bundled Unbound 1.25.1. The package requires the
target OPNsense repository's `python313`, `py313-dnspython`, and `py313-Jinja2`.
The package and SHA-256 checksum are available as CI artifacts and as
versioned assets when the release workflow runs.

Verify the download on the firewall and install it from a root shell.

```sh
package=os-unbound-catalog-1.1.0.pkg
test "$(sha256 -q "$package")" = "$(awk 'NR == 1 { print $1 }' "$package.sha256")"
pkg install -y python313 py313-dnspython py313-Jinja2
pkg add ./os-unbound-catalog-1.1.0.pkg
configctl unboundcatalog status
```

Fresh installation leaves the worker stopped. Open **Services → Unbound DNS →
Catalog Sync**, add a catalog, enable the service in **Settings**, then select
**Save** and **Apply**. To delegate access, assign the **Services: Unbound DNS:
Catalog Sync** privilege to an OPNsense group. See the [configuration guide](configuration.md).

## Upgrade and restore

1. Download a configuration backup from **System → Configuration → Backups**.
   For an offline recovery copy, save `/var/db/unboundcatalog` separately
   while the worker is stopped.
2. Verify the new package checksum. Update an existing local installation with
   `pkg add -f ./os-unbound-catalog-1.1.0.pkg`.
3. Confirm the saved catalogs and their applied zone status in the GUI. Check
   that Unbound remains operational and that the worker is running if enabled.

The package refreshes native templates and starts the worker when enabled.
Upgrading from 0.4.0 or later requires a build that recognizes FreeBSD's
`PKG_UPGRADE` package hook environment. If upgrading an early prerelease
snapshot without the upgrade guard, use a maintenance window and verify
that reconciliation has restored the stubs.

The native configuration backup contains the versioned MVC settings.
Restore it from **System → Configuration → Backups**. Apply the restored
settings or reboot if the restore workflow calls for it. When moving to a
new firewall, install the plugin, allow the new source IP in the catalog
producer's AXFR ACL, and apply the restored settings. The worker rebuilds
operational state from the next valid transfer. The standard configuration
backup excludes operational snapshots, applied state, and transaction files.

Use persistent storage for `/var/db/unboundcatalog`. If the firewall uses a
memory filesystem for `/var`, ensure this directory is backed by persistent
storage before depending on outage recovery across reboots.

## Remove

Keep Unbound available during normal removal. The uninstall hook removes
owned runtime stubs and DNSSEC exceptions, then deletes the owned include
from the persistent and live directories. Verify normal queries after removal.

```sh
pkg delete os-unbound-catalog
test ! -e /usr/local/etc/unbound.opnsense.d/catalog.conf
test ! -e /var/unbound/etc/catalog.conf
unbound-control -c /var/unbound/unbound.conf status
```

An ownership conflict is reported to the native log. Resolve it before
uninstallation. From a root shell, you can run the same cleanup before
removing the package.

```sh
configctl unboundcatalog stop
/usr/local/bin/python3.13 -I \
  /usr/local/opnsense/scripts/OPNsense/UnboundCatalog/resolver_service.py remove
```

The MVC settings and catalog snapshots are retained for reinstall or
investigation. To fully remove them, first archive any data you want to keep.
After the package and its generated include are removed, remove
`/var/db/unboundcatalog` and clear the settings using the native configuration
API. Historical backups and native logs follow the firewall's retention
settings.

```sh
/usr/local/bin/php <<'PHP'
<?php
require '/usr/local/opnsense/mvc/script/load_phalcon.php';
$config = OPNsense\Core\Config::getInstance()->lock();
$root = $config->object();
if (isset($root->OPNsense->UnboundCatalog)) {
    unset($root->OPNsense->UnboundCatalog);
    $config->save();
}
PHP
```

- [Native acceptance](release.md#native-acceptance)
