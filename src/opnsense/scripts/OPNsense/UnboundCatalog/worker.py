"""OPNsense Catalog Sync process."""

import argparse
import signal
import sys
import syslog
from pathlib import Path

# Isolated Python omits the root-owned script directory from imports.
sys.path.insert(0, str(Path(__file__).resolve().parent))
from sync import Poller, Settings

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
    parser.add_argument("--config", type=Path, default=Path("/var/run/unboundcatalog/settings.json"))
    parser.add_argument("--state", type=Path, default=Path("/var/db/unboundcatalog/catalogs/state.json"))
    args = parser.parse_args()

    stop_signals = {signal.SIGTERM, signal.SIGINT}
    signal.pthread_sigmask(signal.SIG_BLOCK, stop_signals)
    syslog.openlog("unboundcatalog", syslog.LOG_PID, syslog.LOG_DAEMON)
    syslog.setlogmask(syslog.LOG_UPTO(LEVELS[args.log_level]))
    try:
        poller = Poller(Settings.read(args.config), args.state, log)
    except (KeyError, OSError, ValueError, TypeError) as error:
        log("error", f"Cannot initialize Catalog Sync: {error}")
        return 1
    log("info", "Catalog Sync started")
    while True:
        try:
            poller.run_due()
        except (OSError, ValueError) as error:
            log("error", f"Cannot save catalog snapshot: {error}")
        delay = max(0.1, min(poller.next_delay(), 60))
        if signal.sigtimedwait(stop_signals, delay) is not None:
            break
    log("info", "Catalog Sync stopped")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
