#!/bin/sh

set -eu

rc=/usr/local/etc/rc.d/unbound_catalog

case "${1:-}" in
    status)
        exec "$rc" onestatus
        ;;
    uptime)
        "$rc" onestatus >/dev/null || exit 0
        pid=$(cat /var/run/unbound_catalog_worker.pid 2>/dev/null) || exit 0
        case "$pid" in
            ''|*[!0-9]*) exit 1 ;;
        esac
        exec /bin/ps -p "$pid" -o etime=
        ;;
    start|stop|restart|apply)
        exec /usr/bin/lockf -k -t 10 /var/run/unbound_catalog.lock "$0" "locked-$1"
        ;;
esac

stop_service()
{
    if "$rc" onestatus >/dev/null; then
        "$rc" onestop
    fi
}

start_service()
{
    if "$rc" onestatus >/dev/null; then
        return 0
    fi

    "$rc" start
    for _ in 1 2 3 4 5; do
        sleep 1
        if "$rc" onestatus >/dev/null && [ -s /var/run/unbound_catalog_worker.pid ]; then
            return 0
        fi
    done
    printf '%s\n' "Catalog Sync worker did not start." >&2
    return 1
}

case "${1:-}" in
    locked-stop)
        stop_service
        ;;
    locked-start|locked-restart|locked-apply)
        result=$(/usr/local/sbin/configctl template reload OPNsense/UnboundCatalog)
        if [ "$result" != "OK" ]; then
            printf '%s\n' "$result" >&2
            exit 1
        fi
        if [ "$1" != "locked-start" ]; then
            stop_service
        fi
        if "$rc" enabled; then
            start_service
        elif [ "$1" != "locked-apply" ]; then
            printf '%s\n' "Enable Catalog Sync and configure an enabled catalog first." >&2
            exit 1
        fi
        ;;
    *)
        exit 64
        ;;
esac
