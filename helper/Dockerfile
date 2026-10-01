# Headscale Easy — Docker helper (hs-helper)
# https://github.com/insanerask77/headscale-easy
#
# The only container with the Docker socket. Serves three fixed operations
# (configtest, restart, status) on a Unix socket for the web UI; see
# helper.py. Standard library only.
FROM python:3.13-alpine

LABEL org.opencontainers.image.title="Headscale Easy helper" \
      org.opencontainers.image.description="Restricted Docker access for Headscale Easy: validate, restart and report on Headscale only" \
      org.opencontainers.image.url="https://github.com/insanerask77/headscale-easy" \
      org.opencontainers.image.source="https://github.com/insanerask77/headscale-easy" \
      org.opencontainers.image.authors="Rafa Madolell <https://github.com/insanerask77>" \
      org.opencontainers.image.licenses="MIT"

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    HELPER_SOCKET=/run/hse-helper/helper.sock

WORKDIR /app
COPY helper.py /app/helper.py

# The shared volume takes this directory's mode when Docker creates it. Sticky
# and world-writable like /tmp, because the uid that creates the socket
# (PORTAL_UID) is only known at run time; the socket itself is 660.
RUN mkdir -p /run/hse-helper && chmod 1777 /run/hse-helper

# docker-compose.yml sets the uid/gid (PORTAL_UID) and adds the socket's group
USER 1000:1000

HEALTHCHECK --interval=15s --timeout=5s --start-period=5s --retries=3 \
  CMD ["python", "-c", "import os, socket; s = socket.socket(socket.AF_UNIX); s.connect(os.environ['HELPER_SOCKET'])"]

CMD ["python", "/app/helper.py"]
