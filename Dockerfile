# Visor — stateless image; all state lives in the mounted ./data volume.
# Runs as the host user (set via `user:` in docker-compose.yml) so the host CLI
# and the container share ownership of the data/ volume.
FROM python:3.12-alpine
WORKDIR /app
COPY server.py index.html ./
ENV VISOR_DATA=/data VISOR_PORT=8900
VOLUME /data
EXPOSE 8900
CMD ["python3", "-u", "server.py"]
