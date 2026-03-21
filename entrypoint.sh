#!/bin/sh
set -e

# ---------------------------------------------------------------------------
# PUID / PGID — LinuxServer.io convention
# Defaults to 1000:1000 if not set. Override in docker-compose.yml:
#   environment:
#     PUID: 99    # Unraid
#     PGID: 100
# ---------------------------------------------------------------------------
PUID="${PUID:-1000}"
PGID="${PGID:-1000}"

echo "Starting Rythmx with UID=${PUID} GID=${PGID}"

# Create group and user with the requested IDs
addgroup --gid "$PGID" rythmx 2>/dev/null || true
adduser --disabled-password --gecos "" --uid "$PUID" --ingroup rythmx --no-create-home rythmx 2>/dev/null || true

# Ensure data directories exist (survives fresh volume mounts)
mkdir -p /data/rythmx /data/soulsync

# Own the writable data directory and app directory
chown -R rythmx:rythmx /data/rythmx /rythmx

# Drop privileges and exec the CMD (uvicorn becomes PID 1)
exec gosu rythmx "$@"
