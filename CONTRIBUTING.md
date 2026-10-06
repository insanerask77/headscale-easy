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
install.sh              Installer: Docker if missing, a few questions, writes a small .env and runs the compose file
uninstall.sh            Uninstaller
aio/                    The all-in-one image: Dockerfile, supervisor, config renderer, wizard, backups, `hse`
  supervisor.py         Starts and restarts Headscale, Caddy and the console; the console's socket protocol
  render.py             Renders Headscale's config.yaml, the Caddyfile and the DERP map from the settings
  wizard.py             The first-run setup wizard
  backup.py, restore.py, cron.py   Built-in backups and their schedule
  hse                   Control CLI: health, reload, backup, backups, restore
templates/              Files the renderer fills in (Headscale config, Caddyfile, PostgreSQL read-only role)
web/                    The web console (runs inside the image)
  app.py                HTTP server, routing, sessions, OIDC
  headscale.py          Headscale REST API client, DNS config, supervisor client
  local_accounts.py     Local accounts: passwords, TOTP, invitations, reset links, sign-up keys
  pages.py              Machines, device, DNS, keys, settings pages
  admin_pages.py        Users, access controls (raw HuJSON tab), sign-in page
  acl_pages.py          Access controls: Rules, Groups & tags, Test access tabs
  policy.py             HuJSON parsing/splicing and the access simulator
  ui.py                 Layout, sidebar, icons, shared helpers
  i18n.py, locales/     Translations
  static/               CSS, JS, font, favicon
backup/                 The optional `backup-remote` sidecar (S3, B2, SFTP, rsync)
deploy/                 The reference compose file and the examples (proxies, identity providers, PostgreSQL)
scripts/                aio-smoke.sh, compose-smoke.sh, validate.sh, check_i18n.py
tests/                  Unit tests (Python standard library only)
docs/, mkdocs.yml       Documentation site (GitHub Pages), screenshots
```

## Principles

- **Simple to run.** One container, one command; re-running the installer is safe.
  Never lose a user's data: keep it in the volume, and tell people in the
  changelog when something needs their attention.
- **No dependencies in the console.** Python standard library only, plain
  HTML/CSS/JS, no build step. It keeps the image tiny and the attack surface
  small.
- **Feels like Tailscale.** When adding a screen, look at how Tailscale's admin
  console does it and follow the same wording and layout where Headscale
  supports the feature.
- **Secure by default.** Least privilege (unprivileged user, no capabilities, no
  Docker socket), CSRF tokens on every form, escape all output (`ui.esc`), members
  only ever touch their own devices.
- **Small.** The image stays under 250 MB and idles under 100 MB of RAM; CI fails
  above that (`scripts/aio-smoke.sh`).

## Development

You need Docker. The loop for the all-in-one image:

```bash
docker build -f aio/Dockerfile -t hse-aio:dev .
./scripts/aio-smoke.sh hse-aio:dev          # starts it headless, checks health, a backup, the limits
docker run --rm -p 8080:80 -e HSE_PUBLIC_URL=http://localhost:8080 -e HSE_TLS=off \
  -e HSE_ADMIN_EMAIL=me@example.com -e HSE_ADMIN_PASSWORD='a long password' hse-aio:dev
```

Then open `http://localhost:8080/admin`. To work on the console without a rebuild
each time, mount the sources over the image:
`-v "$PWD/web:/app/web:ro"` and restart the container. To see the first-run wizard,
leave out `HSE_PUBLIC_URL` and read the token from `docker logs`.

Before opening a pull request:

```bash
make lint        # shellcheck, Python syntax, translation coverage
make test        # unit tests
make validate    # project structure, compose files, the environment-variable reference
```

`scripts/compose-smoke.sh` checks the reference compose file in `deploy/compose/`
with the backup sidecar.

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

Two long-lived branches:

| Branch | What it is | Receives | Releases | Images |
|---|---|---|---|---|
| `main` | The last release, always releasable | Merges of `next` at release time, and urgent `fix/…` | `v2.x.y` tags | `:edge` on push, `:latest` + `:2.x.y` on tags |
| `next` | Integration branch | PRs from work branches | `-alpha.N`, `-beta.N`, `-rc.N` tags | `:next` on push, never `:latest` |
| `feat/…`, `fix/…`, `docs/…`, `chore/…`, `refactor/…`, `test/…` | Short-lived work branches | — | — | `:dev` and `:branch-<name>` on push |

### Rules

- Branch from `next` and open the PR into `next`. A fix that cannot wait for the
  next release branches from `main` instead, goes into `main`, and is merged back
  into `next`. Never fix a bug twice.
- `main` and `next` only change through pull requests with green CI; no
  force-push, no deletion.
- Work branches into `next`: **squash merge**, with a Conventional Commit title
  (it becomes the commit message).
- Keep work branches small (one task of a plan phase) and rebase them on their
  base branch freely while they are yours; once someone else uses one, merge
  instead.
- `CHANGELOG.md`: write under `## [Unreleased]` (or the version in progress).
- `web/version.py` holds the version of the code on that branch.

### Releasing

1. Move the changelog entries to `## [x.y.z] - YYYY-MM-DD` and bump
   `web/version.py` on a `release/x.y.z` branch, with a PR into `next`.
2. Open a PR `next` → `main` and merge it with a **merge commit**, then tag the
   merge commit `vx.y.z`. CI publishes the image and the GitHub release.
3. Pre-releases are tagged on `next` (`v2.0.0-rc.1`).

## Pull requests

1. Fork and create a branch from `next` (see [Branches and releases](#branches-and-releases)).
2. Keep changes focused; update docs and translations in the same PR.
3. Use [Conventional Commits](https://www.conventionalcommits.org/)
   (`feat: add tailnet lock page`, `fix(aio): …`).
4. Describe what you tested. Screenshots help for UI changes.
5. Say if the change was written with an AI assistant. That is fine — most of
   this project was (see [AI usage](https://insanerask77.github.io/headscale-easy/ai-usage/))
   — but you must have read and tested it yourself, and changes to sign-in,
   sessions or permissions need a test in
   `tests/test_security.py`.
6. CI must pass.

By contributing you agree your work is licensed under the [MIT License](https://github.com/insanerask77/headscale-easy/blob/main/LICENSE)
and to follow the [Code of Conduct](https://github.com/insanerask77/headscale-easy/blob/main/CODE_OF_CONDUCT.md).
