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

FROM python:3.13-alpine

LABEL org.opencontainers.image.title="Headscale Easy (all-in-one)" \
      org.opencontainers.image.description="Headscale + Caddy + web console in one container, set up from the browser" \
      org.opencontainers.image.url="https://github.com/insanerask77/headscale-easy" \
      org.opencontainers.image.source="https://github.com/insanerask77/headscale-easy" \
      org.opencontainers.image.authors="Rafa Madolell <https://github.com/insanerask77>" \
      org.opencontainers.image.licenses="MIT"

RUN apk add --no-cache tini ca-certificates

# Both are static binaries: no libc or shared libraries come along.
COPY --from=headscale /ko-app/headscale /usr/local/bin/headscale
COPY --from=caddy /usr/bin/caddy /usr/local/bin/caddy

WORKDIR /app
COPY web /app/web
COPY helper /app/helper
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
# its subdirectories (700) on start. /run/hse holds the helper socket.
RUN mkdir -p /data /run/hse \
 && chown 1000:1000 /data /run/hse \
 && chmod 700 /data

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
