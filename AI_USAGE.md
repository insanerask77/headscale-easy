# AI usage

Headscale Easy was built with extensive help from
[Claude Code](https://claude.com/claude-code), Anthropic's AI coding assistant.
This page says how, plainly, so you can weigh it when you decide whether to
run the project and how carefully to review it.

## What the AI did

- **Generated and modified a significant part of the code**: the web UI
  (Python), the installer (`install.sh`), the Authentik blueprint, the Docker
  Compose files, the tests and the CI workflows. Several features of 1.1.0
  were developed by AI agents working in parallel, one per feature, and then
  integrated (see the [changelog](https://insanerask77.github.io/headscale-easy/changelog/)).
- **Helped with debugging, refactoring, translations and documentation**,
  including this page.
- Commits where it took part carry a `Co-Authored-By: Claude` trailer, so
  `git log` shows which ones.

## What the maintainer did

- Decided what the project is, its architecture and every feature, and which
  of the AI's proposals to keep or discard.
- Reviewed, ran and tested the changes on a real installation before merging
  them, and integrated them.
- **Is responsible for the code and the final decisions**, whoever or whatever
  typed them. Bugs are the maintainer's bugs; report them like any other.

## What this means for you

- Review and testing by one person is not an independent audit. The code has
  **not been audited by a third party** — see [SECURITY.md](https://insanerask77.github.io/headscale-easy/security/)
  for what has been reviewed, what the tests cover and the known limitations.
- Treat it like code from a new contributor: read the parts that matter to you
  (sign-in, sessions, the Docker socket) before exposing it to the Internet,
  and follow the [hardening guide](https://insanerask77.github.io/headscale-easy/hardening/).
- Independent reviews, issues and pull requests are very welcome, whether or
  not you use AI tools yourself. Contributions are judged on what they do, and
  the [contributing guide](https://insanerask77.github.io/headscale-easy/contributing/)
  asks you to say if a pull request was AI-assisted.
