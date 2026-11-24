"""Lifecycle entry point for the Catalog Sync foundation."""

import argparse
import signal
import syslog

LEVELS = {
    "debug": syslog.LOG_DEBUG,
    "info": syslog.LOG_INFO,
    "warning": syslog.LOG_WARNING,
    "error": syslog.LOG_ERR,
}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--log-level", choices=LEVELS, default="info")
    args = parser.parse_args()

    stop_signals = {signal.SIGTERM, signal.SIGINT}
    signal.pthread_sigmask(signal.SIG_BLOCK, stop_signals)

    syslog.openlog("unboundcatalog", syslog.LOG_PID, syslog.LOG_DAEMON)
    syslog.setlogmask(syslog.LOG_UPTO(LEVELS[args.log_level]))
    syslog.syslog(syslog.LOG_INFO, "Catalog Sync foundation started")
    signal.sigwait(stop_signals)
    syslog.syslog(syslog.LOG_INFO, "Catalog Sync foundation stopped")


if __name__ == "__main__":
    main()
