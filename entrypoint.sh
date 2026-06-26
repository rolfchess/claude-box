#!/usr/bin/env bash
set -euo pipefail

USERNAME=claude
SOCK=/var/run/docker.sock

# ---------------------------------------------------------------------------
# Give the unprivileged "claude" user access to the bind-mounted Docker socket
# so Testcontainers (and the docker CLI) work. The socket's group id is only
# known at runtime, so we match it here rather than baking it into the image.
# ---------------------------------------------------------------------------
if [ -S "$SOCK" ]; then
    SOCK_GID="$(stat -c '%g' "$SOCK")"
    if [ "$SOCK_GID" != "0" ]; then
        if ! getent group "$SOCK_GID" >/dev/null 2>&1; then
            groupadd -g "$SOCK_GID" dockerhost || true
        fi
        GRP="$(getent group "$SOCK_GID" | cut -d: -f1)"
        usermod -aG "$GRP" "$USERNAME" || true
    else
        # Root-owned socket (common on Docker Desktop): just open it up.
        chmod a+rw "$SOCK" || true
    fi
fi

# Named volumes (the Maven cache) are created root-owned on first use.
for d in "/home/${USERNAME}/.m2" "/home/${USERNAME}/.config"; do
    if [ -d "$d" ] && [ "$(stat -c '%u' "$d")" = "0" ]; then
        chown "${USERNAME}:${USERNAME}" "$d" || true
    fi
done

exec gosu "$USERNAME" "$@"
