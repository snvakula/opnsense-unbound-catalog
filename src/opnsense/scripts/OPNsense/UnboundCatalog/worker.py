"""OPNsense Catalog Sync process."""

import argparse
import os
import signal
import subprocess  # nosec B404
import sys
import syslog
import time
from pathlib import Path

# Isolated Python omits the root-owned script directory from imports.
sys.path.insert(0, str(Path(__file__).resolve().parent))
from notify import Listener
from sync import Poller, Settings, atomic_json

CONFIG = Path("/var/run/unboundcatalog/settings.json")

LEVELS = {
    "debug": syslog.LOG_DEBUG,
    "info": syslog.LOG_INFO,
    "warning": syslog.LOG_WARNING,
    "error": syslog.LOG_ERR,
}


def log(level, message):
    syslog.syslog(LEVELS[level], message)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--log-level", choices=LEVELS, default="info")
    parser.add_argument("--config", type=Path, default=CONFIG)
    parser.add_argument("--state", type=Path, default=Path("/var/db/unboundcatalog/catalogs/state.json"))
    args = parser.parse_args()

    stop_signals = {signal.SIGTERM, signal.SIGINT}
    signals = stop_signals | {signal.SIGUSR1}
    signal.pthread_sigmask(signal.SIG_BLOCK, signals)
    syslog.openlog("unboundcatalog", syslog.LOG_PID, syslog.LOG_DAEMON)
    syslog.setlogmask(syslog.LOG_UPTO(LEVELS[args.log_level]))
    try:
        poller = Poller(Settings.read(args.config), args.state, log)
    except (KeyError, OSError, ValueError, TypeError) as error:
        log("error", f"Cannot initialize Catalog Sync: {error}")
        return 1
    log("info", "Catalog Sync started")
    reconcile_at = 0.0
    native = args.config == CONFIG
    previous_families = None
    with Listener(poller) as listener:
        while True:
            try:
                families = listener.active_families()
                if native and families != previous_families:
                    atomic_json(args.state.with_name("listener.json"), {
                        "pid": os.getpid(), "families": families,
                    })
                    previous_families = families
                if native and reconcile_at == 0:
                    _reconcile()
                    reconcile_at = time.monotonic() + 15
                changed = poller.run_due()
                if native and (changed or time.monotonic() >= reconcile_at):
                    _reconcile()
                    reconcile_at = time.monotonic() + 15
            except (OSError, ValueError) as error:
                log("error", f"Cannot save catalog state: {error}")
            delay = max(0.05, min(poller.next_delay(), 0.25))
            received = signal.sigtimedwait(signals, delay)
            if received is not None:
                if received.si_signo in stop_signals:
                    break
                poller.synchronize()
    log("info", "Catalog Sync stopped")
    return 0


def _reconcile():
    try:
        # Configd accepts one constant action and does not receive data from DNS.
        result = subprocess.run(  # nosec B603
            ["/usr/local/sbin/configctl", "unboundcatalog", "reconcile"],
            capture_output=True, timeout=120, check=False,
        )
        if result.returncode or result.stdout.strip() != b"OK":
            log("error", "Configd rejected or failed the Unbound reconciliation request")
    except (OSError, subprocess.TimeoutExpired) as error:
        log("error", f"Cannot request Unbound reconciliation: {error}")


if __name__ == "__main__":
    raise SystemExit(main())
