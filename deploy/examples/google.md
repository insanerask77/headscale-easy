# Headscale Easy with Google

People sign in with their Google account. Read **Who can sign in** first: with this provider it decides
whether the setup is safe.

## Who can sign in

Headscale registers **whoever the provider lets in**. Restrict it with `HSE_OIDC_ALLOWED_DOMAINS` (the domain of the e-mail), `HSE_OIDC_ALLOWED_USERS` (addresses) or `HSE_OIDC_ALLOWED_GROUPS` (Headscale's `oidc.allowed_*`; comma-separated, empty = no restriction). Google
lets in **any** Google account unless the OAuth app is limited on Google's side or you set one of these:

| Your Google | Safe to use directly? |
|---|---|
| **Google Workspace**, OAuth consent screen user type **Internal** | Yes: only people of your organisation can sign in |
| Personal Gmail accounts, consent screen **External** | **No.** Anyone with a Google account could sign in and join your tailnet |

If you do not have Workspace, do not point the image at Google directly. Put Google behind a provider
that decides who gets in: [`authentik/`](authentik/) ("Sign in with Google" is part of its blueprint, and
Authentik only lets in the people you have enabled), [`pocket-id/`](pocket-id/) or [`keycloak.md`](keycloak.md).

## 1. In Google Cloud

**APIs & Services → OAuth consent screen**: user type **Internal** (Workspace), app name, support e-mail.
Scopes: `openid`, `email`, `profile`.

**APIs & Services → Credentials → Create credentials → OAuth client ID**

| Field | Value |
|---|---|
| Application type | Web application |
| Authorized redirect URIs | `https://vpn.example.com/admin/callback` and `https://vpn.example.com/oidc/callback` |

The first is the console's (`REDIRECT_URI` in `web/app.py`), the second Headscale's; one client serves
both. Copy the client id and the secret.

## 2. The image's side

```bash
# .env of deploy/compose
OIDC_ISSUER=https://accounts.google.com
OIDC_CLIENT_ID=<client id>.apps.googleusercontent.com
OIDC_CLIENT_SECRET=<the secret>
PORTAL_ADMIN_EMAILS=you@your-company.com     # who administers the console
```

`docker compose up -d`, then sign in at `https://vpn.example.com/admin/` with *Sign in with SSO*; devices
with `tailscale up --login-server https://vpn.example.com`.

## What to expect

- Google normally reports addresses as verified, so `PORTAL_ADMIN_EMAILS` should count (not checked here).
- Google sends no **groups**: roles come from `PORTAL_ADMIN_EMAILS` only; everybody else is a member.

## What was run

Nothing against Google: it needs a Google Cloud project, a consent screen and a public domain. The
redirect URIs and the variables come from the code (`web/app.py`, the same ones the Keycloak and Pocket ID
examples were run with); the Google console labels are from Google's documentation and may have moved.
Treat this page as a checklist to confirm, not as a tested recipe.
