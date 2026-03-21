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

# Resolve or create the target group
EXISTING_GROUP=$(getent group "$PGID" | cut -d: -f1 || true)
if [ -z "$EXISTING_GROUP" ]; then
  groupadd -g "$PGID" rythmx
  TARGET_GROUP="rythmx"
else
  TARGET_GROUP="$EXISTING_GROUP"
fi

# Resolve or create the target user
EXISTING_USER=$(getent passwd "$PUID" | cut -d: -f1 || true)
if [ -z "$EXISTING_USER" ]; then
  useradd -u "$PUID" -g "$TARGET_GROUP" -M -s /bin/sh rythmx
  TARGET_USER="rythmx"
else
  TARGET_USER="$EXISTING_USER"
fi

echo "Mapped to user=${TARGET_USER} group=${TARGET_GROUP}"

# Ensure data directories exist (survives fresh volume mounts)
mkdir -p /data/rythmx /data/soulsync

# Own the writable data directory and app directory
chown -R "${PUID}:${PGID}" /data/rythmx /rythmx

# Drop privileges and exec the CMD (uvicorn becomes PID 1)
exec gosu "${TARGET_USER}" "$@"
