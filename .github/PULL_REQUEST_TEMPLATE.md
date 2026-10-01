## What does this change?

<!-- A short description, and the issue it closes (Closes #123). -->

## How was it tested?

<!-- Installer mode(s), browser(s), screenshots for UI changes. -->

## Checklist

- [ ] `make lint` and `make validate` pass
- [ ] New UI strings are wrapped in `_()` and translated in `web/locales/es.json`
- [ ] Documentation updated (README, docs/, CHANGELOG) if needed
- [ ] Existing installations keep working (re-running `./install.sh` is safe)
- [ ] Changes to sign-in, sessions, permissions or Docker access (hs-helper) have a test in `tests/test_security.py` or `tests/test_docker_helper.py`
- [ ] AI-assisted: yes / no (if yes, I have read and tested every line)
