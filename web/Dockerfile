# Headscale Easy — web UI for Headscale
# https://github.com/insanerask77/headscale-easy
#
# Standard library only: the official Python image is all it needs.
FROM python:3.13-alpine

LABEL org.opencontainers.image.title="Headscale Easy" \
      org.opencontainers.image.description="The open source Tailscale alternative: Headscale with a built-in web UI" \
      org.opencontainers.image.url="https://github.com/insanerask77/headscale-easy" \
      org.opencontainers.image.source="https://github.com/insanerask77/headscale-easy" \
      org.opencontainers.image.authors="Rafa Madolell <https://github.com/insanerask77>" \
      org.opencontainers.image.licenses="MIT"

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PORT=8000

WORKDIR /app
COPY . /app

# Mount point of the volume shared with hs-helper (its Unix socket). Same
# mode as in helper/Dockerfile, whichever container creates the volume first.
RUN mkdir -p /run/hse-helper && chmod 1777 /run/hse-helper

# Runs as an unprivileged user; docker-compose.yml may override the uid/gid
# so it can write headscale-config.yaml (DNS editing).
USER 1000:1000
EXPOSE 8000

HEALTHCHECK --interval=15s --timeout=5s --start-period=10s --retries=3 \
  CMD ["python", "-c", "import urllib.request; urllib.request.urlopen('http://127.0.0.1:8000/admin/healthz', timeout=3)"]

CMD ["python", "/app/app.py"]
