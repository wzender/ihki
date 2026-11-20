"""Mail to the school's office: a sign-in problem and a request to join a class, sent as they arrive.

The app has no mail of its own (Supabase sends the sign-in links), so this is off until a way to send is configured,
in the Space's settings, by one of:
  SMTP_HOST, SMTP_PORT (587), SMTP_USER, SMTP_PASSWORD   an SMTP server, e.g. smtp.gmail.com with an app password
  RESEND_API_KEY                                         the Resend service (https://resend.com), over HTTPS
and, for either:
  OFFICE_EMAIL   where the mail goes (default office@ahalawasahala.com; several separated by commas)
  MAIL_FROM      who it is from (default: SMTP_USER, or "ihki <onboarding@resend.dev>" with Resend, which then
                 delivers only to the Resend account's own address until a domain is verified there)
A mail that cannot be sent is logged and dropped: the note itself is already kept in `feedback`.
"""
from __future__ import annotations

import json
import logging
import os
import smtplib
import ssl
import urllib.request
from email.message import EmailMessage

log = logging.getLogger("ihki.mail")

OFFICE_EMAIL = "office@ahalawasahala.com"
RESEND_URL = "https://api.resend.com/emails"
TIMEOUT = 15


def office() -> list[str]:
    return [a.strip() for a in os.environ.get("OFFICE_EMAIL", OFFICE_EMAIL).replace(";", ",").split(",") if a.strip()]


def configured() -> str:
    """"smtp", "resend", or "" when no way to send is set."""
    if os.environ.get("SMTP_HOST", "").strip():
        return "smtp"
    if os.environ.get("RESEND_API_KEY", "").strip():
        return "resend"
    return ""


def send(subject: str, text: str, reply_to: str = "") -> bool:
    """Sends one plain-text mail to the office; True when it was handed over. Never raises."""
    how, to = configured(), office()
    if not how or not to:
        log.info("office mail not sent: no SMTP_HOST or RESEND_API_KEY")
        return False
    try:
        if how == "smtp":
            _smtp(subject, text, to, reply_to)
        else:
            _resend(subject, text, to, reply_to)
        log.info("office mail sent via %s", how)
        return True
    except Exception as e:   # the note is kept whatever happens here
        log.warning("office mail failed via %s: %s", how, type(e).__name__)
        return False


def _smtp(subject: str, text: str, to: list[str], reply_to: str) -> None:
    host, user = os.environ["SMTP_HOST"].strip(), os.environ.get("SMTP_USER", "").strip()
    port = int(os.environ.get("SMTP_PORT", "587") or 587)
    msg = EmailMessage()
    msg["Subject"], msg["From"], msg["To"] = subject, os.environ.get("MAIL_FROM", "").strip() or user, ", ".join(to)
    if reply_to:
        msg["Reply-To"] = reply_to
    msg.set_content(text)
    if port == 465:
        server = smtplib.SMTP_SSL(host, port, timeout=TIMEOUT, context=ssl.create_default_context())
    else:
        server = smtplib.SMTP(host, port, timeout=TIMEOUT)
        server.starttls(context=ssl.create_default_context())
    with server:
        if user:
            server.login(user, os.environ.get("SMTP_PASSWORD", ""))
        server.send_message(msg)


def _resend(subject: str, text: str, to: list[str], reply_to: str) -> None:
    body = {"from": os.environ.get("MAIL_FROM", "").strip() or "ihki <onboarding@resend.dev>", "to": to,
            "subject": subject, "text": text, **({"reply_to": reply_to} if reply_to else {})}
    req = urllib.request.Request(RESEND_URL, data=json.dumps(body).encode(), method="POST",
                                 headers={"Authorization": f"Bearer {os.environ['RESEND_API_KEY'].strip()}",
                                          "Content-Type": "application/json", "User-Agent": "ihki"})
    with urllib.request.urlopen(req, timeout=TIMEOUT) as res:
        if res.status >= 300:
            raise RuntimeError(f"resend answered {res.status}")


def login_problem(row: dict, panel: str = "") -> tuple[str, str]:
    """(subject, text) of a sign-in problem (feedback.login_note's row)."""
    lines = [f"מישהו לא מצליח להיכנס לאפליקציה.", "", f"כתובת: {row.get('email')}", f"מה קרה: {row.get('item')}"]
    if row.get("message"):
        lines += ["", row["message"]]
    lines += ["", "אפשר להשיב למייל הזה: התשובה תגיע לכתובת שהשאירו."]
    if panel:
        lines.append(f"בניהול ← משוב: {panel}")
    return f"🔐 בעיית כניסה: {row.get('email')}", "\n".join(lines)


def join_request(row: dict, panel: str = "") -> tuple[str, str]:
    """(subject, text) of a request to join a class (feedback.join_note's row, with its `details`)."""
    d = row.get("details") or {}
    lines = ["בקשה להצטרף לכיתה, מחשבון שנכנס ואינו באף כיתה.", "",
             f"שם: {d.get('name')}", f"כתובת החשבון: {row.get('email')}", f"טלפון: {d.get('phone') or '—'}",
             f"כיתה / מורה: {d.get('class_name') or '—'}"]
    if row.get("message"):
        lines += ["", row["message"]]
    lines += ["", "כדי לצרף: ניהול ← כיתות ← עריכת הכיתה ← הוספת הכתובת לרשימת התלמידים (או דרך Grok).",
              "אפשר להשיב למייל הזה: התשובה תגיע לתלמיד/ה."]
    if panel:
        lines.append(f"בניהול ← משוב: {panel}")
    return f"🙋 בקשת הצטרפות לכיתה: {d.get('name')} ({row.get('email')})", "\n".join(lines)
