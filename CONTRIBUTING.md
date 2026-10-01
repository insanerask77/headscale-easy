# Contributing to Headscale Easy

Thanks for helping! Headscale Easy is maintained by
[Rafa Madolell](https://github.com/insanerask77) and every bug report,
translation and pull request makes it better.

## Ways to help

- 🐛 [Report a bug](https://github.com/insanerask77/headscale-easy/issues/new?template=bug_report.yml)
- 💡 [Suggest a feature](https://github.com/insanerask77/headscale-easy/issues/new?template=feature_request.yml)
- 🌍 [Translate the console](#translations)
- 📝 Improve the documentation
- 🔧 Send a pull request
- ⭐ Star the repository and ☕ [buy me a coffee on Ko-fi](https://ko-fi.com/rafaelmadolell)

## Project layout

```
install.sh              Interactive installer (bash, English + Spanish)
uninstall.sh            Uninstaller
docker-compose.yml      The stack: headscale, caddy, web, hs-helper, authentik (profile)
templates/              Files the installer renders with envsubst
authentik/blueprints/   Authentik configuration (OIDC clients, groups, add-user flow)
authentik/branding/     Authentik theme (CSS, logos)
web/                    The web console (ghcr.io/insanerask77/headscale-easy)
  app.py                HTTP server, routing, sessions, OIDC
  headscale.py          Headscale REST API client, DNS config, hs-helper client
  pages.py              Machines, device, DNS, keys, settings pages
  admin_pages.py        Users, access controls (raw HuJSON tab), sign-in page
  acl_pages.py          Access controls: Rules, Groups & tags, Test access tabs
  policy.py             HuJSON parsing/splicing and the access simulator
  ui.py                 Layout, sidebar, icons, shared helpers
  i18n.py, locales/     Translations
  static/               CSS, JS, font, favicon
helper/                 hs-helper: the only container with the Docker socket (helper.py)
scripts/                utils.sh (make targets), validate.sh, check_i18n.py
docs/, mkdocs.yml       Documentation site (GitHub Pages), screenshots
```

## Principles

- **Simple to run.** One command installs everything; re-running it is safe.
  Never break existing installations: keep data, migrate settings.
- **No dependencies in the console.** Python standard library only, plain
  HTML/CSS/JS, no build step. It keeps the image tiny and the attack surface
  small.
- **Feels like Tailscale.** When adding a screen, look at how Tailscale's admin
  console does it and follow the same wording and layout where Headscale
  supports the feature.
- **Secure by default.** Least privilege for containers, CSRF tokens on every
  form, escape all output (`ui.esc`), members only ever touch their own devices.

## Development

You need Docker and a running stack (`./install.sh` with `SSL_MODE=none` and a
LAN IP or a name like `vpn.127.0.0.1.nip.io` is the quickest).

Rebuild and restart the console after changing `web/`:

```bash
docker compose up -d --build web
docker compose logs -f web
```

Before opening a pull request:

```bash
make lint        # shellcheck, Python syntax, translation coverage
make test        # unit tests (standard library only, no Docker)
make validate    # project structure and Compose file
```

### End-to-end tests

`tests/e2e/` drives a real stack over HTTP with the standard library
(`urllib`): API key and Authentik (OIDC) sign-in, Headscale's API, users, an
auth key used by a real `tailscale/tailscale` container, the ACL policy, DNS
(validated with `headscale configtest` and applied by restarting Headscale
through hs-helper), session revocation, and backup + restore. They are not
part of `make test`. The `e2e` job in CI runs them on every pull request, once
with `AUTH_PROVIDER=none` and once with the built-in Authentik.

```bash
make e2e                      # AUTH_PROVIDER=none
E2E_AUTH=authentik make e2e   # with the built-in Authentik
```

`make e2e` (`tests/e2e/run.sh`) installs a throwaway stack in your checkout
with `./install.sh --non-interactive`, runs the tests and **deletes the stack
and its volumes** afterwards (`E2E_KEEP=true` keeps it). The stack uses fixed
container and volume names, so the script refuses to run where a Headscale
Easy stack already exists: use a VM or let CI run it.

To test the installer's output without deploying, answer **No** to "Deploy the
stack now?": it writes `.env`, `headscale-config.yaml`, `Caddyfile` and
`docker-compose.override.yml` and stops.

## Documentation

The docs live in `docs/` and are published to
[GitHub Pages](https://insanerask77.github.io/headscale-easy/) with
[MkDocs Material](https://squidfunk.github.io/mkdocs-material/) on every push to
`main`. English pages are `page.md`, Spanish ones `page.es.md` (a missing
translation falls back to English). Keep the `{ #anchor }` ids of translated
headings equal to the English ones: the console links to them.

Preview locally:

```bash
pip install -r docs/requirements.txt
mkdocs serve        # http://127.0.0.1:8000
```

## Translations

UI strings are written in English in the code, wrapped in `_()` (or
`ngettext()` for plurals). Each language is a JSON file in `web/locales/`
mapping the English text to its translation:

```json
{
  "Add device": "Añadir dispositivo",
  "{n} machine": "{n} máquina",
  "{n} machines": "{n} máquinas"
}
```

To add a language:

1. Copy `web/locales/es.json` to `web/locales/<code>.json` (ISO 639-1, e.g. `fr`)
   and translate the values. Keep `{placeholders}` untouched.
2. Add the code and its name to `LANGUAGES` in `web/i18n.py`.
3. Run `python3 scripts/check_i18n.py`: it lists missing and unused strings.

The installer's messages use `t "English" "Español"`; supporting a third
language there is welcome too.

## Pull requests

1. Fork and create a branch from `main` (`feat/…`, `fix/…`, `docs/…`).
2. Keep changes focused; update docs and translations in the same PR.
3. Use [Conventional Commits](https://www.conventionalcommits.org/)
   (`feat: add tailnet lock page`, `fix(installer): …`).
4. Describe what you tested. Screenshots help for UI changes.
5. Say if the change was written with an AI assistant. That is fine — most of
   this project was (see [AI usage](https://insanerask77.github.io/headscale-easy/ai-usage/))
   — but you must have read and tested it yourself, and changes to sign-in,
   sessions, permissions or the Docker socket need a test in
   `tests/test_security.py`.
6. CI must pass.

By contributing you agree your work is licensed under the [MIT License](https://github.com/insanerask77/headscale-easy/blob/main/LICENSE)
and to follow the [Code of Conduct](https://github.com/insanerask77/headscale-easy/blob/main/CODE_OF_CONDUCT.md).
