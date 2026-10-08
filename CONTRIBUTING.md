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
compose.yaml            The simple install: one service, the pinned image
.env.example            Optional answers for the first start
advanced/               Advanced configurations: compose overlays (PostgreSQL, a proxy in front, remote backups, sign-in providers)
aio/                    The all-in-one image: Dockerfile, supervisor, config renderer, wizard, backups, `hse`
  supervisor.py         Starts and restarts Headscale, Caddy and the console; the console's socket protocol
  processes.py          One supervised child process: restart with backoff, output, helpers to run commands
  backup_control.py     The supervisor's backup side: control requests, schedule, running a backup
  render.py             Renders Headscale's config.yaml, the Caddyfile and the DERP map from the settings
  render_checks.py      Validation of the settings the renderer reads
  wizard.py             The first-run setup wizard (server, sessions, steps)
  wizard_checks.py, wizard_pages.py   Its form validation and its HTML
  backup.py, restore.py, cron.py   Built-in backups and their schedule
  hse                   Control CLI: health, reload, backup, backups, restore
templates/              Files the renderer fills in (Headscale config, Caddyfile, PostgreSQL read-only role)
web/                    The web console (runs inside the image)
  app.py                The HTTP server: Handler (sessions, dispatch), routing tables, start-up
  config.py             Settings read from the environment (Settings.from_env)
  http_base.py, signing.py   Response/request helpers and signed cookies
  handlers/             What each request does, one mixin of Handler per area
    shared.py             Settings aliases, role rules and lookups shared by the handlers
    access.py             Sign-in (OIDC, API key, local accounts), two-factor, invitations, password reset
    devices.py            Machines, pre-auth keys, device registration, live events
    users.py              Users, invitations, sign-up, API keys
    policy.py             Access controls, DNS, DERP, key expiry
    operations.py         Backups, restore, notifications
  headscale.py          Headscale REST API client, DNS config, supervisor client (contract in ports.py)
  dns_block.py          The managed DNS block of config.yaml (text only)
  local_accounts.py     Local accounts: passwords, roles, two-factor setup
  accounts_db.py, totp.py, account_tokens.py   Their database, one-time codes, invitation/reset/sign-up tokens
  sessions.py, apikey.py, audit.py, notify.py, naming.py, expiry.py, signup.py, derp.py, status.py   Logic (no HTML)
  *_pages.py            HTML of each area: machines, keys, dns, settings, auth, acl, admin, audit, backup, derp,
                        status, expiry, signup
  policy.py             HuJSON parsing/splicing and the access simulator
  ui.py                 Layout, sidebar, icons, shared helpers
  i18n.py, locales/     Translations
  static/               CSS, JS, font, favicon
backup/                 The `backup-remote` sidecar image (S3, B2, SFTP, rsync)
scripts/                aio-smoke.sh, compose-smoke.sh, advanced-smoke.sh, validate.sh, check_i18n.py, check_types.py
pyproject.toml          ruff, pytest, pyright and coverage settings
tests/                  Unit tests (Python standard library only)
docs/, mkdocs.yml       Documentation site (GitHub Pages), screenshots
```

## Principles

- **Simple to run.** One container, one command; restarting the container is safe.
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
  above 250 MB on every run and above 100 MB on a release tag (`scripts/aio-smoke.sh`;
  `HSE_SMOKE_PERF=1` runs the RAM measurements locally).

## Development

You need Docker. The loop for the all-in-one image:

```bash
docker build -f aio/Dockerfile -t hse-aio:dev .
./scripts/aio-smoke.sh hse-aio:dev          # starts it headless, checks health, a backup, the limits
docker run --rm -p 8080:80 -e HSE_PUBLIC_URL=http://localhost:8080 -e HSE_TLS=off \
  -e HSE_ADMIN_EMAIL=me@example.com -e HSE_ADMIN_PASSWORD='a long password' hse-aio:dev
```

Then open `http://localhost:8080/console`. To work on the console without a rebuild
each time, mount the sources over the image:
`-v "$PWD/web:/app/web:ro"` and restart the container. To see the first-run wizard,
leave out `HSE_PUBLIC_URL` and read the token from `docker logs`.

Before opening a pull request:

```bash
make lint        # shellcheck, ruff, Python syntax, translation coverage
make typecheck   # pyright: no more errors than the recorded ceiling (scripts/check_types.py)
make test        # unit tests
make validate    # project structure, compose files, the environment-variable reference
```

`scripts/compose-smoke.sh` runs `compose.yaml` for real (healthy, hardened, a backup,
`down && up -d` keeps the data) and then the remote backup add-on.

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
(`web/locales/<code>.d/*.json`).

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
- Keep work branches small (one task) and rebase them on their
  base branch freely while they are yours; once someone else uses one, merge
  instead.
- `CHANGELOG.md`: write under the heading of the version in progress.
- The `VERSION` file holds the version of the code on that branch (`web/version.py` and the image read it).

### Releasing

1. On a `release/x.y.z` branch, move the changelog entries to `## [x.y.z] - YYYY-MM-DD`, set
   `VERSION`, and the default tag in `compose.yaml` and `ARG HSE_VERSION` in `aio/Dockerfile`, with a PR
   into `next`. `python3 scripts/release_info.py check` (also part of `scripts/validate.sh`) fails when
   they differ.
2. Open a PR `next` → `main` and merge it with a **merge commit**, then tag the merge commit `vx.y.z`.
   On the tag, CI checks the tag against `VERSION` and the dated changelog entry, publishes
   `x.y.z`, `x.y`, `x` and `latest` of `headscale-easy` (and of `headscale-easy-backup`), pulls the
   published image back and runs `scripts/compose-smoke.sh` on it, and creates the release page with the
   changelog entry as its notes.
3. Pre-releases are tagged on `next` (`v2.0.0-rc.1`): they publish only their own tag, never `latest`.

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
