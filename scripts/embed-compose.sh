#!/usr/bin/env bash
# Copies deploy/compose/docker-compose.yml into the HSE_COMPOSE heredoc of install.sh, so the
# installer needs nothing but itself (it works from `curl | bash`).
# tests/test_install.py fails when the two differ: run this after editing the compose file.
set -euo pipefail
ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
python3 - "$ROOT" <<'PY'
import re, sys
root = sys.argv[1]
compose = open(f"{root}/deploy/compose/docker-compose.yml", encoding="utf-8").read()
assert "HSE_COMPOSE" not in compose
path = f"{root}/install.sh"
text = open(path, encoding="utf-8").read()
new, n = re.subn(r"(<<'HSE_COMPOSE'\n).*?(^HSE_COMPOSE\n)",
                 lambda m: m.group(1) + compose.rstrip("\n") + "\n" + m.group(2), text, flags=re.S | re.M)
assert n == 1, "the HSE_COMPOSE heredoc was not found"
open(path, "w", encoding="utf-8").write(new)
PY
