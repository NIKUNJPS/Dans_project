"""Email service.

Delivery order:
  1. Resend transactional API (if RESEND_API_KEY is set) — preferred on Render.
  2. SMTP (if SMTP_HOST/USER/PASS configured) — fallback.
  3. Console log — dev only, so codes remain recoverable when nothing is set up.

send_email returns a real boolean: True only when a provider actually accepted
the message. Callers can surface a hard failure to the user.
"""
from __future__ import annotations

import logging
import smtplib
from email.mime.multipart import MIMEMultipart
from email.mime.text import MIMEText

import requests

from config import settings

logger = logging.getLogger(__name__)

RESEND_ENDPOINT = "https://api.resend.com/emails"

BRAND_NAVY = "#0d2240"
BRAND_GOLD = "#f5a800"


def _html_wrap(title: str, body_html: str) -> str:
    return f"""<!doctype html>
<html>
<head><meta charset="utf-8"><title>{title}</title></head>
<body style="margin:0;padding:0;background:#f7f9fc;font-family:'IBM Plex Sans',Arial,sans-serif;color:#1a2d44">
  <table role="presentation" width="100%" cellpadding="0" cellspacing="0" style="background:#f7f9fc;padding:32px 16px">
    <tr><td align="center">
      <table role="presentation" width="560" cellpadding="0" cellspacing="0" style="background:#ffffff;border:1px solid #e2eaf2">
        <tr>
          <td style="background:{BRAND_NAVY};padding:28px 32px">
            <div style="font-family:'Barlow Condensed',Arial,sans-serif;font-weight:700;font-size:22px;letter-spacing:2px;color:#ffffff">
              4<span style="color:{BRAND_GOLD}">X</span>STRUCT · STRUCTMIND
            </div>
          </td>
        </tr>
        <tr><td style="padding:32px">{body_html}</td></tr>
        <tr><td style="padding:24px 32px;background:#f7f9fc;border-top:1px solid #e2eaf2;font-size:12px;color:#6b8299">
          4XStruct Inc. · Structural Steel Detailing & Estimation<br/>
          If you did not request this email, you can safely ignore it.
        </td></tr>
      </table>
    </td></tr>
  </table>
</body>
</html>"""


def render_otp_email(first_name: str, otp: str, purpose: str) -> tuple[str, str, str]:
    subject_map = {
        "signup": "Verify your STRUCTMIND account",
        "login": "Your STRUCTMIND login code",
        "reset": "Reset your STRUCTMIND password",
        "change": "Confirm your password change",
    }
    subject = subject_map.get(purpose, "Your STRUCTMIND verification code")
    title_map = {
        "signup": "Verify your email",
        "login": "Confirm your sign-in",
        "reset": "Reset your password",
        "change": "Confirm password change",
    }
    title = title_map.get(purpose, "Your verification code")
    body = f"""
<h1 style="font-family:'Barlow Condensed',Arial,sans-serif;font-size:28px;letter-spacing:-0.5px;margin:0 0 8px;color:{BRAND_NAVY};text-transform:uppercase">{title}</h1>
<p style="font-size:15px;line-height:1.6;margin:0 0 20px">Hi {first_name or 'there'},</p>
<p style="font-size:15px;line-height:1.6;margin:0 0 20px">Use the 6-digit code below to continue. This code expires in {settings.otp_expiry_seconds // 60} minutes.</p>
<div style="margin:24px 0;padding:24px;background:{BRAND_NAVY};text-align:center">
  <div style="font-family:'JetBrains Mono',Consolas,monospace;font-size:40px;font-weight:700;letter-spacing:14px;color:{BRAND_GOLD}">{otp}</div>
</div>
<p style="font-size:13px;color:#6b8299;margin:0">For your security, never share this code. 4XStruct will never ask for it.</p>
"""
    text = f"Your STRUCTMIND {purpose} code is: {otp}\nThis code expires in {settings.otp_expiry_seconds // 60} minutes."
    return subject, _html_wrap(subject, body), text


def render_password_changed_email(first_name: str) -> tuple[str, str, str]:
    subject = "Your STRUCTMIND password has been changed"
    body = f"""
<h1 style="font-family:'Barlow Condensed',Arial,sans-serif;font-size:28px;letter-spacing:-0.5px;margin:0 0 8px;color:{BRAND_NAVY};text-transform:uppercase">Password updated</h1>
<p style="font-size:15px;line-height:1.6;margin:0 0 16px">Hi {first_name or 'there'},</p>
<p style="font-size:15px;line-height:1.6;margin:0 0 16px">Your STRUCTMIND password was just changed. All your previous sessions have been signed out.</p>
<p style="font-size:13px;color:#6b8299">If this wasn't you, please contact support immediately.</p>
"""
    return subject, _html_wrap(subject, body), "Your STRUCTMIND password has been changed."


def render_analysis_complete_email(first_name: str, mode_label: str, project_name: str, action_url: str) -> tuple[str, str, str]:
    subject = f"Analysis complete · {mode_label}"
    body = f"""
<h1 style="font-family:'Barlow Condensed',Arial,sans-serif;font-size:28px;letter-spacing:-0.5px;margin:0 0 8px;color:{BRAND_NAVY};text-transform:uppercase">Analysis complete</h1>
<p style="font-size:15px;line-height:1.6;margin:0 0 20px">Hi {first_name or 'there'},</p>
<p style="font-size:15px;line-height:1.6;margin:0 0 20px"><strong>{mode_label}</strong> for project <strong>{project_name}</strong> is ready to review.</p>
<a href="{action_url}" style="display:inline-block;background:{BRAND_GOLD};color:{BRAND_NAVY};padding:14px 28px;text-decoration:none;font-family:'Barlow Condensed',Arial,sans-serif;font-weight:700;letter-spacing:1px;text-transform:uppercase">Open report →</a>
"""
    return subject, _html_wrap(subject, body), f"Analysis complete: {mode_label} for {project_name}. Open: {action_url}"


def _from_header() -> str:
    """Sender header used by both Resend and SMTP."""
    addr = settings.smtp_from or settings.smtp_user or "onboarding@resend.dev"
    return f"{settings.smtp_from_name} <{addr}>"


def _send_via_resend(to: str, subject: str, html: str, text: str) -> bool:
    """Send through the Resend transactional API. Returns True on a 2xx."""
    try:
        resp = requests.post(
            RESEND_ENDPOINT,
            headers={
                "Authorization": f"Bearer {settings.resend_api_key}",
                "Content-Type": "application/json",
            },
            json={
                "from": _from_header(),
                "to": [to],
                "subject": subject,
                "html": html,
                "text": text,
            },
            timeout=15,
        )
        if 200 <= resp.status_code < 300:
            logger.info("email_sent provider=resend to=%s subject=%s", to, subject)
            return True
        logger.error(
            "resend_send_failed to=%s status=%s body=%s",
            to, resp.status_code, resp.text[:500],
        )
        return False
    except Exception as e:  # noqa: BLE001
        logger.error("resend_send_error to=%s error=%s", to, e)
        return False


def _send_via_smtp(to: str, subject: str, html: str, text: str) -> bool:
    """Send through SMTP. Returns True only when the server accepts the message."""
    try:
        msg = MIMEMultipart("alternative")
        msg["Subject"] = subject
        msg["From"] = _from_header()
        msg["To"] = to
        msg.attach(MIMEText(text, "plain"))
        msg.attach(MIMEText(html, "html"))

        with smtplib.SMTP(settings.smtp_host, settings.smtp_port, timeout=15) as s:
            s.starttls()
            s.login(settings.smtp_user, settings.smtp_pass)
            s.sendmail(settings.smtp_from or settings.smtp_user, [to], msg.as_string())
        logger.info("email_sent provider=smtp to=%s subject=%s", to, subject)
        return True
    except Exception as e:  # noqa: BLE001
        logger.error("smtp_send_failed to=%s error=%s", to, e)
        return False


def send_email(to: str, subject: str, html: str, text: str) -> bool:
    """Deliver an email via Resend, then SMTP, then console (dev).

    Returns True only when a real provider accepted the message. When no
    provider is configured we log the message to the console (dev fallback)
    and still return True so local development is not blocked.
    """
    if settings.resend_api_key:
        if _send_via_resend(to, subject, html, text):
            return True
        # If a real provider was configured but failed, do NOT pretend success.
        # Fall through to SMTP only when SMTP is also configured.
        if not (settings.smtp_host and settings.smtp_user and settings.smtp_pass):
            return False

    if settings.smtp_host and settings.smtp_user and settings.smtp_pass:
        return _send_via_smtp(to, subject, html, text)

    # No provider configured — dev fallback so codes stay recoverable.
    logger.info(
        "\n[DEV EMAIL — no provider configured] ------\n"
        "To: %s\nSubject: %s\nText:\n%s\n---------------------------------------\n",
        to, subject, text,
    )
    return True
