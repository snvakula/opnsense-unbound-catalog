# Installation and operation

## Build and install on a lab firewall

Use an OPNsense 26.7 amd64 installation or a FreeBSD 15.1 amd64 build guest
with `git`, `pkg` and `python313`. On an OPNsense firewall, use its native
package repository for missing dependencies. The repository's CI creates a
package and a SHA-256 checksum as workflow artifacts when the package job
succeeds.

Build with the OPNsense 26.7.9 plugin framework. Run these commands from a
checkout of this project on the build machine.

```sh
build=$(mktemp -d)
git clone --depth 1 --branch 26.7.9 https://github.com/opnsense/plugins.git "$build/plugins"
plugin="$build/plugins/dns/unbound-catalog"
mkdir -p "$plugin" dist
cp -R Makefile pkg-descr src +PRE_DEINSTALL.pre +POST_DEINSTALL.post "$plugin/"
chmod -R a+rX "$plugin/src"
make -C "$plugin" PLUGIN_ABI=26.7 PLUGIN_PHP=85 package
cp "$plugin"/work/pkg/os-unbound-catalog-*.pkg dist/
rm -rf "$build"
```

Copy the package to the lab firewall and run as root.

```sh
pkg add ./os-unbound-catalog-0.1.0.pkg
configctl unboundcatalog status
```

The package installs through `pkg`. A fresh installation leaves the worker
stopped and the configured global enable switch off.

## Configure and inspect

1. Go to **Services → Unbound DNS → Catalog Sync → Catalogs** and add an enabled
   catalog. Use `catalog.example.net.` for the name, `192.0.2.10` for the
   transfer server, and `2001:db8::10` for the authoritative server in a lab
   configuration. Add each address as a separate token.
2. Open **Settings**, enable Catalog Sync, select **Save**, then **Apply**.
   Apply uses saved settings. With no enabled catalogs or a disabled global
   switch, Apply stops the worker.
3. Use the standard service widgets or `configctl unboundcatalog start`,
   `stop`, `restart`, or `status` from the shell. **Overview** shows the
   native state and worker uptime. The **Log File** tab shows lifecycle
   messages at Informational severity with the default log setting.
4. To grant access to an OPNsense group, assign the
   **Services: Unbound DNS: Catalog Sync** privilege.

Delete catalogs through the grid confirmation. Disable a catalog through its
editor and apply the saved change. Version 0.1.0 manages no zones. The Zones
grid stays empty and **Synchronize now** is unavailable. DNS-related settings
are saved for future releases. There are no AXFR or NOTIFY firewall openings
needed for 0.1.0.

If the service fails to start, verify the enable switch and that an enabled
catalog has a name and both address lists. Check
`/etc/rc.conf.d/unbound_catalog` and the native configd log. For missing log
entries, verify the severity selection and that the service started after
installation completed.

## Upgrade and removal

Save an OPNsense configuration backup before changing the package. A future
upgrade must preserve the versioned MVC model. Review and apply saved settings
after a lab upgrade. This foundation creates no persistent operational
database or Unbound include.

```sh
pkg delete os-unbound-catalog
```

Removal stops the worker and removes the generated rc settings, the runtime
lock, and package-owned registrations. The OPNsense configuration retains
saved Catalog Sync settings for backup or reinstallation. Native logs remain
subject to the appliance's log retention policy.

To discard retained settings after removal, run the following as root on the
firewall. The native configuration API records the cleanup in configuration
history. Earlier backups remain subject to the firewall's retention settings.

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

## Native acceptance

Record the appliance version and results. These checks supplement Linux
linting and the FreeBSD package build.

1. Save a configuration backup and record the Unbound status. Install the
   package. Verify the menu, all five tabs, the stopped service, and the
   continued availability of Unbound.
2. Test valid IPv4, IPv6 and mixed address lists. Submit invalid IPs, CIDRs in
   server fields, a port outside its allowed range, equivalent duplicate
   catalog names, and enabled NOTIFY with both families disabled. Verify that
   changes are rejected server-side.
3. Verify the GUI under a user with the plugin privilege, then confirm access is
   denied under a user without it. Exercise the API under the same users.
4. Save and apply enabled settings. Verify one worker running as `nobody`.
   Use Start, Stop, Restart and Apply. Confirm worker uptime changes and check
   the native log entries. Verify no listening sockets belong to the worker.
5. Reboot the enabled lab instance and confirm the worker starts once. Test
   disabling or deleting the last catalog and applying the change. Test
   restoring the settings backup through the native configuration workflow.
6. Install a subsequent lab build and verify settings persistence. Remove the
   package. Verify the worker and generated rc file are gone, the menu and
   configd action disappear, Unbound continues serving, and plugin settings
   remain in the configuration backup.

The acceptance suite for later versions must cover real catalog producers,
Unbound reconciliation and the required delegation checks with a separate
authoritative child server.

## Verified lab coverage

The package was built and installed on an isolated OPNsense 26.7 Nano guest
running FreeBSD 15.1-RELEASE-p1 amd64 and Unbound 1.25.1. The lab exercised the
five GUI tabs, catalog creation, editing and confirmed deletion, mixed IPv4
and IPv6 settings, server-side validation, Apply, service controls, worker
uptime, native logging and delegated access. A same-version package reinstall
preserved the saved MVC configuration. Reboot started one worker running as
`nobody`. Package removal stopped the worker, cleared the generated files
and configd action, and retained the MVC settings. The Unbound PID,
configuration checksum and localhost DNS answer passed the removal checks.
The native API cleanup procedure also passed.

Cross-version upgrades and the full configuration backup and restore workflow
remain native acceptance work. The mandatory resolver delegation regression
belongs to the Unbound reconciliation milestone.
