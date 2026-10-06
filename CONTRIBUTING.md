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
aio/                    The all-in-one image: supervisor, control socket, config renderer, wizard, backups
scripts/                validate.sh, check_i18n.py, the smoke tests
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

## Branches and releases

Two long-lived branches, so the 2.0 work ([simplification plan](https://github.com/insanerask77/headscale-easy/blob/main/SIMPLIFICATION_PLAN.md))
never breaks the installations running 1.x:

| Branch | What it is | Receives | Releases | Images |
|---|---|---|---|---|
| `main` | Stable 1.x, always releasable | PRs from `fix/…`, `docs/…`, small `feat/…` for 1.x | `v1.x.y` tags | `:edge` on push, `:latest` + `:1.x.y` on tags |
| `next` | Integration branch for 2.0 | PRs from 2.0 work branches, and merges of `main` | `v2.0.0-alpha.N`, `-beta.N`, `-rc.N` tags | `:next` on push, `:2.0.0-alpha.N` on tags (never `:latest`) |
| `feat/…`, `fix/…`, `docs/…`, `chore/…`, `refactor/…`, `test/…` | Short-lived work branches | — | — | `:dev` and `:branch-<name>` on push |

### Where does my branch start?

- A fix or a feature for **1.x** (what users run today): branch from `main`, PR into `main`.
- Work from the **simplification plan**: branch from `next`, PR into `next`.
  Name it after the phase: `feat/2.0-p0-register-flow`, `feat/2.0-p1-local-accounts`.
- A bug that exists in both: fix it on `main` first; it reaches `next` with the
  next sync. Never fix it twice.

### Rules

- `main` and `next` only change through pull requests with green CI; no
  force-push, no deletion.
- Work branches into `main`/`next`: **squash merge**, with a Conventional
  Commit title (it becomes the commit message).
- **Sync `main` into `next`** with a merge commit (never rebase or squash it)
  after every 1.x release and at least once a week, in a PR titled
  `chore: sync main into next`. `next` is never merged into `main` until the
  2.0 release.
- Keep work branches small (one task of a plan phase) and rebase them on their
  base branch freely while they are yours; once someone else uses one, merge
  instead.
- Every PR into `next` keeps `make lint`, `make test` and the current
  `docker compose` stack working, until phase 5 of the plan deprecates pieces
  on purpose. New 2.0 behaviour is opt-in until then.
- `CHANGELOG.md`: `main` writes under `## [Unreleased]`; `next` writes under
  `## [2.0.0] - Unreleased`. Sync conflicts there are resolved by keeping both.
- `web/version.py`: `main` holds the last 1.x version; `next` holds
  `2.0.0-dev` until a pre-release tag.

### Releasing

1. 1.x: on a `release/1.x.y` branch from `main`, bump `web/version.py`, move
   `[Unreleased]` to `## [1.x.y] - YYYY-MM-DD` in `CHANGELOG.md`, PR into
   `main`, then tag the merge commit `v1.x.y`. CI publishes the images and the
   GitHub release.
2. 2.0 pre-releases: the same on `next`, with `v2.0.0-alpha.N` tags.
3. 2.0: PR `next` → `main` (merge commit), tag `v2.0.0`. At that moment a
   `release/1.x` branch is cut from the last `v1.*` tag for security fixes
   only, and `docker.yml` must stop moving `:latest` for `v1.*` tags.

## Pull requests

1. Fork and create a branch from `main` or `next` (see [Branches and releases](#branches-and-releases)).
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
