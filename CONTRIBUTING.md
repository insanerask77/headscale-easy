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
docker-compose.yml      The stack: headscale, caddy, web, authentik (profile)
templates/              Files the installer renders with envsubst
authentik/blueprints/   Authentik configuration (OIDC clients, groups, add-user flow)
authentik/branding/     Authentik theme (CSS, logos)
web/                    The web console (ghcr.io/insanerask77/headscale-easy)
  app.py                HTTP server, routing, sessions, OIDC
  headscale.py          Headscale REST API client, DNS config, Docker socket
  pages.py              Machines, device, DNS, keys, settings pages
  admin_pages.py        Users, access controls (raw HuJSON tab), sign-in page
  acl_pages.py          Access controls: Rules, Groups & tags, Test access tabs
  policy.py             HuJSON parsing/splicing and the access simulator
  ui.py                 Layout, sidebar, icons, shared helpers
  i18n.py, locales/     Translations
  static/               CSS, JS, font, favicon
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
make validate    # project structure and Compose file
```

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

`es.d/`-style per-feature files are supported for every language
(`web/locales/<code>.d/*.json`). The installer's messages use
`t "English" "Español"`; supporting more languages there is welcome too.

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
