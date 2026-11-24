"""OPNsense adapter for Unbound files and runtime controls."""

import glob
import ipaddress
import re
import shlex
import subprocess  # nosec B404
from pathlib import Path
from tempfile import TemporaryDirectory

import dns.name
from jinja2 import Environment, StrictUndefined
from reconcile import ReconcileError
from sync import atomic_bytes

CONTROL = "/usr/local/sbin/unbound-control"
CHECK = "/usr/local/sbin/unbound-checkconf"
OPTIONS = (
    "stub-zone", "forward-zone", "auth-zone", "rpz", "local-zone", "domain-insecure",
)


def canonical(text):
    return dns.name.from_text(text).canonicalize().to_text()


def visible_stub(stub):
    # Unbound's list_stubs exposes IP addresses but omits their port numbers.
    return tuple(sorted({address.partition("@")[0] for address in stub.addresses})), True


def names(output):
    return {canonical(line.split()[0]) for line in output.splitlines() if line.strip()}


class Unbound:
    def __init__(self, configuration, persistent, active, template, work, control=CONTROL, check=CHECK):
        self.configuration = configuration
        self.persistent = persistent
        self.active = active
        self.template = template
        self.work = work
        self.control_binary = control
        self.check_binary = check

    def command(self, binary, *args):
        try:
            result = subprocess.run(  # nosec B603
                [binary, *map(str, args)], cwd=self.configuration.parent,
                capture_output=True, text=True, timeout=15, check=False,
            )
        except (OSError, subprocess.TimeoutExpired) as error:
            raise ReconcileError(str(error)) from error
        output = result.stdout.strip()
        if result.returncode or output.lower().startswith("error"):
            raise ReconcileError((result.stderr.strip() or output or "Unbound command failed")[:500])
        return output

    def control(self, *args):
        return self.command(self.control_binary, "-c", self.configuration, *args)

    def runtime(self):
        stubs = {}
        for line in self.control("list_stubs").splitlines():
            parts = line.split()
            if len(parts) < 4 or parts[2] != "stub":
                continue
            try:
                addresses = tuple(sorted({str(ipaddress.ip_address(item)) for item in parts[4:] if not item.startswith("+")}))
            except ValueError:
                # An administrator stub may be configured with nameservers.
                addresses = ()
            stubs[canonical(parts[0])] = (addresses, parts[3] == "noprime")
        return stubs, names(self.control("list_insecure"))

    def admin_zones(self):
        zones = {}
        seen = set()
        queue = [self.configuration, *sorted(self.persistent.parent.glob("*.conf"))]
        ignored = {self.persistent, self.active}
        while queue:
            path = queue.pop()
            if path in ignored or path.resolve() in seen:
                continue
            seen.add(path.resolve())
            if len(seen) > 512:
                raise ReconcileError("Too many Unbound includes")
            section = None
            with path.open() as stream:
                for line in stream:
                    if ":" not in line or line.lstrip().startswith("#"):
                        continue
                    key, *values = shlex.split(re.sub(r"^\s*([\w-]+):", r"\1: ", line), comments=True)
                    if key in ("include:", "include-toplevel:") and values:
                        pattern = self.configuration.parent / values[0]
                        queue.extend(map(Path, sorted(glob.glob(str(pattern)))))
                    if key.rstrip(":") in OPTIONS and not values:
                        section = key.rstrip(":")
                    elif key in ("local-zone:", "domain-insecure:") and values:
                        zones[canonical(values[0])] = key.rstrip(":")
                    elif key == "name:" and section in OPTIONS and values:
                        zones[canonical(values[0])] = section
        return zones

    def conflicts(self, old, new, cleanup=False):
        stubs, insecure = self.runtime()
        admin = self.admin_zones()
        for operation in ("list_forwards", "list_local_zones", "list_auth_zones"):
            admin.update(dict.fromkeys(names(self.control(operation)), operation))
        occupied = {zone: f"Existing Unbound {admin[zone]}" for zone in new.keys() & admin.keys()}
        for zone in old.keys() | new.keys():
            if zone in stubs and (
                zone not in old or stubs[zone] != visible_stub(old[zone])
            ):
                occupied[zone] = "Existing unmanaged or modified Unbound stub"
            if zone in insecure and (zone not in old or not old[zone].insecure) and (not cleanup or zone in new):
                occupied[zone] = "Existing administrator DNSSEC exception"
        return occupied

    def render(self, zones):
        if not zones:
            return None
        environment = Environment(undefined=StrictUndefined, keep_trailing_newline=True)  # nosec B701
        return environment.from_string(self.template.read_text()).render(zones=zones).encode()

    @staticmethod
    def present(path):
        if path.is_symlink():
            raise ReconcileError(f"Unexpected symlink at {path}")
        try:
            return path.read_bytes()
        except FileNotFoundError:
            return None

    def check_owner(self, previous, candidate):
        allowed = (None, self.render(previous), self.render(candidate))
        for path in (self.persistent, self.active):
            actual = self.present(path)
            if actual not in allowed:
                raise ReconcileError(f"{path} differs from catalog-owned configuration")

    def validate(self, previous, candidate):
        self.check_owner(previous, candidate)
        content = self.render(candidate)
        with TemporaryDirectory(prefix="validate-", dir=self.work) as workspace:
            folder = Path(workspace)
            original = self.configuration.read_text()
            wildcard = str(self.active.parent / "*.conf")
            if wildcard not in original:
                raise ReconcileError("Unbound configuration has no supported OPNsense include")
            # OPNsense copies persistent includes over active includes on startup.
            # Validate the live and the post-start include sets independently.
            active = {path.name: path for path in self.active.parent.glob("*.conf") if path != self.active}
            persistent = {path.name: path for path in self.persistent.parent.glob("*.conf")
                          if path != self.persistent}
            for index, files in enumerate((active, active | persistent)):
                includes = folder / str(index)
                includes.mkdir()
                for name, path in files.items():
                    (includes / name).symlink_to(path)
                if content:
                    (includes / self.active.name).write_bytes(content)
                proposed = folder / self.configuration.name
                proposed.write_text(original.replace(wildcard, str(includes / "*.conf")))
                self.command(self.check_binary, proposed)

    def _validator(self):
        return "validator" in self.control("get_option", "module-config").strip('"').split()

    def matches(self, desired, previous):
        expected = self.render(desired)
        stubs, insecure = self.runtime()
        validator = self._validator()
        for zone in desired.keys() | previous.keys():
            stub = desired.get(zone)
            if stub is None:
                if zone in stubs or (previous[zone].insecure and zone in insecure):
                    return False
            elif stubs.get(zone) != visible_stub(stub) or (validator and (zone in insecure) != stub.insecure):
                return False
        return all(self.present(path) == expected for path in (self.persistent, self.active))

    def modify(self, *args):
        if self.control(*args) != "ok":
            raise ReconcileError(f"Unbound did not acknowledge {args[0]}")

    def transition(self, previous, desired, recovering=False):
        self.check_owner(previous, desired)
        stubs, insecure = self.runtime()
        validator = self._validator()
        touched = set()
        for zone in sorted(previous.keys() | desired.keys()):
            old, new = previous.get(zone), desired.get(zone)
            live = stubs.get(zone)
            if recovering and live is not None and not any(
                item and live == visible_stub(item) for item in (old, new)
            ):
                raise ReconcileError(f"{zone} changed outside the pending transaction")
            if recovering and zone in insecure and not any(item and item.insecure for item in (old, new)):
                raise ReconcileError(f"{zone} has a new administrator DNSSEC exception")
            if new is None:
                if live is not None:
                    self.modify("stub_remove", zone)
                    touched.add(zone)
            elif live != visible_stub(new) or (old and old.addresses != new.addresses):
                self.modify("stub_add", zone, *new.addresses)
                touched.add(zone)
            if validator:
                monitored = bool(new and new.insecure)
                if monitored != (zone in insecure) and (new or (old and old.insecure)):
                    self.modify("insecure_add" if monitored else "insecure_remove", zone)
                    touched.add(zone)
            if old is None or new is None or old.effective != new.effective:
                touched.add(zone)

        content = self.render(desired)
        for path in (self.persistent, self.active):
            if self.present(path) != content:
                if content is None:
                    path.unlink(missing_ok=True)
                else:
                    atomic_bytes(path, content, 0o644)
        self.command(self.check_binary, self.configuration)
        if not self.matches(desired, previous):
            raise ReconcileError("Runtime and persistent Unbound state did not converge")
        for zone in sorted(touched):
            if not self.control("flush_zone", zone).startswith("ok removed "):
                raise ReconcileError(f"Unbound did not acknowledge cache invalidation for {zone}")
