"""Sending an invitation or a password-reset link by e-mail (standard library only).

Only when SMTP_HOST is set, and only when an administrator clicks "Send by e-mail": nothing
here is automatic. The SMTP password is read from the environment when a message is sent and
never logged, never returned and never written to the audit log.

    SMTP_HOST, SMTP_PORT (587), SMTP_USERNAME, SMTP_PASSWORD, SMTP_USE_TLS (STARTTLS),
    SMTP_USE_SSL (implicit TLS), SMTP_FROM
"""
import logging
import os
import re
import smtplib
import ssl
from email.message import EmailMessage

log = logging.getLogger("hse.mailer")

EMAIL_RE = re.compile(r"[^@\s,;<>\"']{1,64}@[A-Za-z0-9.-]{1,253}\.[A-Za-z]{2,}")
TIMEOUT = 15


class MailError(Exception):
    """A message that could not be sent; the text is safe to show to the administrator."""


def _truthy(name: str) -> bool:
    return os.environ.get(name, "").strip().lower() in ("1", "true", "yes", "on")


def enabled() -> bool:
    return bool(os.environ.get("SMTP_HOST", "").strip())


def valid_address(value: str) -> bool:
    return bool(EMAIL_RE.fullmatch(value or ""))


def send(to: str, subject: str, text: str) -> None:
    """Send one plain-text message. Raises MailError (never with the credentials in it)."""
    if not enabled():
        raise MailError("E-mail is not configured.")
    if not valid_address(to):
        raise MailError("That e-mail address is not valid.")
    host = os.environ["SMTP_HOST"].strip()
    try:
        port = int(os.environ.get("SMTP_PORT") or 587)
    except ValueError:
        raise MailError("SMTP_PORT is not a number.") from None
    user = os.environ.get("SMTP_USERNAME", "")
    sender = os.environ.get("SMTP_FROM", "").strip() or user or "headscale-easy@" + host
    msg = EmailMessage()
    msg["From"] = sender
    msg["To"] = to
    msg["Subject"] = " ".join(subject.split())  # one line: no header injection through the subject
    msg.set_content(text)
    context = ssl.create_default_context()
    try:
        if _truthy("SMTP_USE_SSL"):
            server = smtplib.SMTP_SSL(host, port, timeout=TIMEOUT, context=context)
        else:
            server = smtplib.SMTP(host, port, timeout=TIMEOUT)
        with server:
            if not _truthy("SMTP_USE_SSL") and _truthy("SMTP_USE_TLS"):
                server.starttls(context=context)
            if user:
                server.login(user, os.environ.get("SMTP_PASSWORD", ""))
            server.send_message(msg)
    except (smtplib.SMTPException, OSError) as exc:
        # The class of the failure is enough to act on; the message may echo the server's reply.
        log.warning("could not send an e-mail through %s:%s (%s)", host, port, type(exc).__name__)
        raise MailError("The mail server did not accept the message (%s)." % type(exc).__name__) from None
    log.info("sent an e-mail through %s:%s", host, port)
