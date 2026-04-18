#!/bin/bash
set -e

PROFILE_DIR="${UMS_PROFILE:-/profile}"

# Seed profile on first run (empty volume)
if [ ! -f "${PROFILE_DIR}/UMS.conf" ]; then
    echo "[entrypoint] Seeding profile directory..."
    mkdir -p "${PROFILE_DIR}"
    cp /ums/seed/UMS.conf "${PROFILE_DIR}/UMS.conf"
    cp /ums/seed/SHARED.conf "${PROFILE_DIR}/SHARED.conf"
fi

# Resolve UMS_HOSTNAME to a LAN IP and inject into UMS.conf.
# UMS advertises this address in SSDP/UPnP packets — it must be an IP,
# not a hostname, for DLNA clients to connect back correctly.
if [ -n "${UMS_HOSTNAME}" ]; then
    RESOLVED_IP=$(getent hosts "${UMS_HOSTNAME}" | awk '{ print $1 }' | head -1)
    if [ -n "${RESOLVED_IP}" ]; then
        echo "[entrypoint] Resolved ${UMS_HOSTNAME} -> ${RESOLVED_IP}, injecting into UMS.conf"
        sed -i "s/^hostname[[:space:]]*=.*/hostname = ${RESOLVED_IP}/" "${PROFILE_DIR}/UMS.conf"
    else
        echo "[entrypoint] WARNING: Could not resolve ${UMS_HOSTNAME} — hostname not set in UMS.conf"
    fi
fi

# Inject server friendly name if provided.
if [ -n "${UMS_SERVER_NAME}" ]; then
    echo "[entrypoint] Setting server_name to ${UMS_SERVER_NAME}"
    sed -i "s/^server_name[[:space:]]*=.*/server_name = ${UMS_SERVER_NAME}/" "${PROFILE_DIR}/UMS.conf"
fi

# Override index refresh interval if provided (minutes; 0 = disabled).
if [ -n "${UMS_INDEX_REFRESH_MINUTES}" ]; then
    echo "[entrypoint] Setting python_index_refresh_minutes to ${UMS_INDEX_REFRESH_MINUTES}"
    if grep -q "^python_index_refresh_minutes" "${PROFILE_DIR}/UMS.conf"; then
        sed -i "s/^python_index_refresh_minutes[[:space:]]*=.*/python_index_refresh_minutes = ${UMS_INDEX_REFRESH_MINUTES}/" "${PROFILE_DIR}/UMS.conf"
    else
        echo "python_index_refresh_minutes = ${UMS_INDEX_REFRESH_MINUTES}" >> "${PROFILE_DIR}/UMS.conf"
    fi
fi

exec java -jar /ums/ums.jar
