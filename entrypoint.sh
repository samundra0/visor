#!/bin/sh
# Visor container entrypoint (v1.9 security defaults).
#
# The container binds 0.0.0.0 *inside* the container (VISOR_HOST, set in the
# Dockerfile) so Docker's port mapping works; exposure on the host is decided
# by the mapping (docker-compose.yml maps 127.0.0.1:8900 — loopback only).
#
# v1.9: the server refuses a non-loopback bind without an explicit VISOR_TOKEN,
# and the per-install token is persisted 0600 at $VISOR_DATA/visor-token.
# This entrypoint closes that loop: reuse the generated token if it exists,
# otherwise create it — then export it so the server's startup check passes
# with an EXPLICIT token.
set -eu

DATA="${VISOR_DATA:-/data}"
if [ -z "${VISOR_TOKEN:-}" ]; then
    if [ -f "$DATA/visor-token" ] && [ -s "$DATA/visor-token" ]; then
        VISOR_TOKEN="$(tr -d '[:space:]' < "$DATA/visor-token")"
    else
        mkdir -p "$DATA"
        VISOR_TOKEN="$(python3 -c 'import secrets; print(secrets.token_urlsafe(24))')"
        umask 077
        printf '%s\n' "$VISOR_TOKEN" > "$DATA/visor-token"
    fi
    export VISOR_TOKEN
fi

exec python3 -u server.py
