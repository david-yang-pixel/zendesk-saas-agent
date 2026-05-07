#!/usr/bin/env bash
# Probe each tsh-proxy-db port; restart the corresponding service if dead.
#
# Why this exists: `tsh proxy db --tunnel` occasionally drops its socket
# (cert refresh interaction, network blip, OOM). systemd reports the service
# as `active` but psql can't connect. Path-unit-on-cert-change covers the
# cert-renewal case; this timer handles the silent socket-died case.
#
# Idempotent: if all ports respond, exits 0 and does nothing.
set -euo pipefail

declare -A PORTS=(
    [tsh-proxy-db]=56061
    [tsh-proxy-db-billing]=56062
    [tsh-proxy-db-plugin]=56063
)

LOG_FILE=/var/log/proxy-health.log

log() {
    echo "[$(date -Iseconds)] $*" | tee -a "$LOG_FILE" >&2
}

probe() {
    # Returns 0 if port is reachable + responds (TCP handshake completes).
    timeout 3 bash -c "</dev/tcp/127.0.0.1/$1" 2>/dev/null
}

restart_count=0
for svc in "${!PORTS[@]}"; do
    port="${PORTS[$svc]}"
    if probe "$port"; then
        continue
    fi
    log "$svc port $port unreachable; restarting"
    if systemctl restart "$svc"; then
        log "$svc restarted OK"
        restart_count=$((restart_count + 1))
    else
        log "$svc restart FAILED (rc=$?)"
    fi
done

if [ "$restart_count" -gt 0 ]; then
    log "summary: restarted $restart_count proxy service(s)"
fi
