# Installation and operation

## Install

Use OPNsense 26.7 amd64, which is based on FreeBSD 15.1. Native checks use
OPNsense 26.7 Nano and its bundled Unbound 1.25.1. The package requires the
target OPNsense repository's `python313`, `py313-dnspython`, and `py313-Jinja2`.
Download the `.pkg` and its matching `.pkg.sha256` from the release's
**Assets**. For a development build, use the archive
`os-unbound-catalog-FreeBSD-15-amd64` from a successful GitHub Actions run.
[Latest release](https://github.com/snvakula/opnsense-unbound-catalog/releases/latest) ·
[All releases](https://github.com/snvakula/opnsense-unbound-catalog/releases) ·
[Development builds](https://github.com/snvakula/opnsense-unbound-catalog/actions)

Copy both files to the firewall. Replace `FIREWALL` with its management
address or SSH hostname. The examples below use release 1.1.0. Substitute
the version you downloaded in both commands.

```sh
scp os-unbound-catalog-1.1.0.pkg os-unbound-catalog-1.1.0.pkg.sha256 root@FIREWALL:/tmp/
```

Run the following from a root shell on the firewall. The chain stops if the
checksum verification, dependency installation, or package installation fails.

```sh
cd /tmp
package=os-unbound-catalog-1.1.0.pkg
test "$(sha256 -q "$package")" = "$(awk 'NR == 1 { print $1 }' "$package.sha256")" &&
  pkg install -y python313 py313-dnspython py313-Jinja2 &&
  pkg add "./$package" &&
  /usr/local/opnsense/scripts/firmware/register.php install os-unbound-catalog
```

The final command registers a locally installed plugin under **System →
Firmware → Plugins**. For an already-installed package marked
`misconfigured`, run the registration command on its own and refresh the
page. A local package absent from the connected repositories may appear
as `orphaned` with `unknown-repository`. Use the local upgrade procedure
below for later versions.

Fresh installation leaves the service disabled. Open **Services → Unbound
DNS → Catalog Sync → Settings**, enable **Enable Catalog Sync**, then select
**Save** and **Apply**. The service can start with zero catalogs. Add
your catalog in **Catalogs** and apply the configuration to begin AXFR.
To delegate access, assign the **Services: Unbound DNS: Catalog Sync**
privilege to an OPNsense group. [Configuration guide](configuration.md)

## Upgrade and restore

1. Download a configuration backup from **System → Configuration → Backups**.
   For an offline recovery copy, save `/var/db/unboundcatalog` separately
   while the worker is stopped.
2. Repeat the checksum, dependencies, and registration steps in the installation
   procedure. Use `pkg add -f "./$package"` in place of `pkg add "./$package"`
   to upgrade the existing installation.
3. Confirm the saved catalogs and their applied zone status in the GUI. Check
   that Unbound remains operational and that the worker is running if enabled.

The package refreshes native templates and starts the worker when enabled.
Starting with 1.2.0 it moves the old generated include into the plugin's
private state directory during reconciliation. Manually maintained Unbound
overrides remain subject to OPNsense's warning.
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
pkg delete os-unbound-catalog &&
  /usr/local/opnsense/scripts/firmware/register.php remove os-unbound-catalog
test ! -e /var/db/unboundcatalog/resolver/catalog.conf
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
