"""Gmail approval backend (fallback channel).

This is a lightweight SMTP-based notifier for approval requests. It is not
meant to be production-grade; integrate with a dedicated mail service in a
real environment.
"""
from __future__ import annotations

import smtplib
import ssl
from email.mime.text import MIMEText

from .base import Batch


def send_email(
    smtp_server: str,
    smtp_port: int,
    from_addr: str,
    to_addr: str,
    subject: str,
    body: str,
    app_password: str,
) -> bool:
    try:
        msg = MIMEText(body)
        msg["Subject"] = subject
        msg["From"] = from_addr
        msg["To"] = to_addr
        with smtplib.SMTP(smtp_server, smtp_port, timeout=30) as smtp:
            smtp.starttls(context=ssl.create_default_context())
            smtp.login(from_addr, app_password)
            smtp.sendmail(from_addr, [to_addr], msg.as_string())
        return True
    except Exception:
        return False


def notify_batch(
    batch: Batch,
    group_email: str,
    from_email: str,
    smtp_server: str,
    smtp_port: int,
    app_password: str,
) -> bool:
    body_lines = [f"Batch {batch.batch_id} approvals:"]
    for a in batch.actions:
        body_lines.append(f"- {a.id}: {a.name} -> {a.target} (status: {a.status})")
    body = "\n".join(body_lines)
    subject = f"SRE Batch {batch.batch_id} Approvals"
    return send_email(smtp_server, smtp_port, from_email, group_email, subject, body, app_password)
