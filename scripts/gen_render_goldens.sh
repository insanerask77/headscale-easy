#!/usr/bin/env bash
# Regenerates tests/fixtures/render/<case>/{headscale-config.yaml,Caddyfile} by
# running install.sh's own generators (install.sh cannot be sourced: it ends
# with an unguarded main). Each case dir may hold env.sh (install.sh variables)
# and existing-config.yaml (the config already on disk). Output is what
# tests/test_render.py compares the Python port against.
# shellcheck disable=SC2034  # the variables are read by the install.sh functions sourced below
set -euo pipefail
ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
FIX="$ROOT/tests/fixtures/render"
FUNCS="$(mktemp)"
trap 'rm -f "$FUNCS"' EXIT
start=$(grep -n '^dns_block() {' "$ROOT/install.sh" | cut -d: -f1)
end=$(grep -n '^# Headscale and the UI validate the OIDC issuer' "$ROOT/install.sh" | cut -d: -f1)
sed -n "${start},$((end - 1))p" "$ROOT/install.sh" > "$FUNCS"

for dir in "$FIX"/*/; do
    [[ -f "$dir/env.sh" ]] || continue
    work="$(mktemp -d)"
    (
        set +u
        SCRIPT_DIR="$work"
        TEMPLATES_DIR="$ROOT/templates"
        print_success() { :; }
        t() { printf '%s' "$1"; }
        # Defaults install.sh would have set
        HEADSCALE_HTTP_PORT=8080; HEADSCALE_METRICS_PORT=9090; HEADSCALE_GRPC_PORT=50443
        HEADSCALE_DERP_PORT=3478; IP_PREFIXES_V4=100.64.0.0/10; IP_PREFIXES_V6=fd7a:115c:a1e0::/48
        LOG_LEVEL=info; TAILNET_NAME=myorg; DERP_USE_PUBLIC=true; ENABLE_OIDC=false
        AUTH_PROVIDER=none; OIDC_SCOPE="openid profile email"
        # shellcheck disable=SC1090
        source "$dir/env.sh"
        [[ -f "$dir/existing-config.yaml" ]] && cp "$dir/existing-config.yaml" "$work/headscale-config.yaml"
        # shellcheck disable=SC1090
        source "$FUNCS"
        generate_headscale_config
        generate_caddyfile
    )
    cp "$work/headscale-config.yaml" "$dir/headscale-config.yaml"
    cp "$work/Caddyfile" "$dir/Caddyfile"
    rm -rf "$work"
    echo "generated $(basename "$dir")"
done
