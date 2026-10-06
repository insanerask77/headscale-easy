# Headscale Easy — all-in-one image (Headscale + Caddy + console)
# https://github.com/insanerask77/headscale-easy
#
# Build from the repository root:  docker build -f aio/Dockerfile -t hse-aio .
#
# Three processes under a small Python supervisor (aio/supervisor.py), run by
# tini. Ports 80/443 (TCP), 443/UDP and 3478/UDP; state lives in /data.
ARG HEADSCALE_VERSION=0.29.4
ARG CADDY_VERSION=2.11.4

FROM headscale/headscale:${HEADSCALE_VERSION} AS headscale
FROM caddy:${CADDY_VERSION}-alpine AS caddy
# The official image gives the binary the file capability cap_net_bind_service. This image binds 80/443
# through net.ipv4.ip_unprivileged_port_start instead, and a binary that carries a capability the
# container does not have cannot be executed at all (docker run --cap-drop ALL: "Operation not permitted").
RUN apk add --no-cache libcap-utils && setcap -r /usr/bin/caddy && ! getcap /usr/bin/caddy | grep -q .

FROM python:3.13-alpine

# The default is the VERSION file (tests/test_version.py keeps them equal); a release build passes it explicitly.
ARG HSE_VERSION=2.0.1

LABEL org.opencontainers.image.title="Headscale Easy (all-in-one)" \
      org.opencontainers.image.version="${HSE_VERSION}" \
      org.opencontainers.image.description="Headscale + Caddy + web console in one container, set up from the browser" \
      org.opencontainers.image.url="https://github.com/insanerask77/headscale-easy" \
      org.opencontainers.image.source="https://github.com/insanerask77/headscale-easy" \
      org.opencontainers.image.authors="Rafa Madolell <https://github.com/insanerask77>" \
      org.opencontainers.image.licenses="MIT"

# postgresql18-client (pg_dump, psql): backups and restores with an external PostgreSQL, and the read-only role
# for the console. +13 MB. A pg_dump dumps servers up to its own major version (13 to 18 here).
RUN apk add --no-cache tini ca-certificates postgresql18-client

# Both are static binaries: no libc or shared libraries come along.
COPY --from=headscale /ko-app/headscale /usr/local/bin/headscale
COPY --from=caddy /usr/bin/caddy /usr/local/bin/caddy

WORKDIR /app
COPY VERSION /app/VERSION
COPY web /app/web
COPY templates /app/templates
COPY aio /app/aio
RUN ln -s /app/aio/hse /usr/local/bin/hse \
 && chmod 755 /app/aio/hse \
 && rm -f /app/aio/Dockerfile

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PORT=8000 \
    HSE_DATA_DIR=/data \
    HSE_RUN_DIR=/run/hse \
    STATUS_DISKS=data:/data

# /data holds everything (see the layout in the docs); the supervisor creates
# its subdirectories (700) on start. /run/hse holds the control socket.
# /data/backups exists in the image, owned by the unprivileged user, so a named volume mounted there
# starts with that owner (an empty volume would be root's and the container could not use it).
RUN mkdir -p /data/backups /run/hse \
 && chown 1000:1000 /data /data/backups /run/hse \
 && chmod 700 /data /data/backups

# Unprivileged. Ports 80/443 bind without capabilities on Docker >= 20.10
# (net.ipv4.ip_unprivileged_port_start=0); on other runtimes pass
# --sysctl net.ipv4.ip_unprivileged_port_start=0.
USER 1000:1000
VOLUME /data
EXPOSE 80 443 443/udp 3478/udp

HEALTHCHECK --interval=15s --timeout=5s --start-period=30s --retries=3 \
  CMD ["hse", "health"]

ENTRYPOINT ["/sbin/tini", "--"]
CMD ["python", "/app/aio/supervisor.py"]
