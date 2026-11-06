# Visor — stateless image; all state lives in the mounted ./data volume.
# Runs as the host user (set via `user:` in docker-compose.yml) so the host CLI
# and the container share ownership of the data/ volume.
FROM python:3.12-alpine
WORKDIR /app
COPY server.py index.html entrypoint.sh ./
COPY plugins/ ./plugins/
# v1.9: a container must bind all interfaces INSIDE the container for
# Docker's port mapping to reach the server; exposure on the host is set by
# the mapping (docker-compose.yml maps 127.0.0.1:8900 — loopback only).
# entrypoint.sh guarantees the per-install token is exported (reused from
# /data/visor-token or created 0600) so the server's non-loopback check
# passes with an explicit VISOR_TOKEN.
ENV VISOR_DATA=/data VISOR_PORT=8900 VISOR_HOST=0.0.0.0 VISOR_MODULES=${VISOR_MODULES:-}
VOLUME /data
EXPOSE 8900
CMD ["/app/entrypoint.sh"]
