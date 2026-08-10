"""SMTP email sending (used by the built-in EmailNotifier).

Kept in its own module so the notifier plugin can import it without
triggering circular imports.
"""

from __future__ import annotations

import datetime
import smtplib
from contextlib import suppress
from email.header import Header
from email.mime.text import MIMEText
from email.utils import formataddr, parseaddr

from loguru import logger
from omegaconf import DictConfig


def _connect_tls(host: str, port: int):
    """SMTP with STARTTLS (failures propagate to the caller's fallback chain)."""
    server = smtplib.SMTP(host, port, timeout=30)
    try:
        server.starttls()
    except Exception:
        with suppress(Exception):
            server.quit()
        raise
    return server


def _collect_receivers(config: DictConfig) -> list[str]:
    """Primary recipient + optional extras (deduped, order kept)."""
    primary = config.email.receiver
    extra = config.email.get("receivers") or []
    if isinstance(extra, str):
        extra = [e.strip() for e in extra.split(",") if e.strip()]
    receivers = [primary] + [r for r in extra if r and r != primary]
    return list(dict.fromkeys(receivers))


def _format_addr(s: str) -> str:
    name, addr = parseaddr(s)
    return formataddr((Header(name, 'utf-8').encode(), addr))


def send_email(config: DictConfig, html: str, subject: str | None = None) -> None:
    sender = config.email.sender
    receivers = _collect_receivers(config)
    password = config.email.sender_password
    smtp_server = config.email.smtp_server
    smtp_port = config.email.smtp_port

    msg = MIMEText(html, 'html', 'utf-8')
    msg['From'] = _format_addr(f'Zotero-arXiv-Daily <{sender}>')
    msg['To'] = _format_addr(f'You <{receivers[0]}>')
    if len(receivers) > 1:
        msg['Cc'] = ', '.join(_format_addr(f'CC <{r}>') for r in receivers[1:])
    if subject is None:
        subject = f'Daily arXiv {datetime.datetime.now().strftime("%Y/%m/%d")}'
    msg['Subject'] = Header(subject, 'utf-8').encode()

    server = None
    attempts = [
        ("TLS", lambda: _connect_tls(smtp_server, smtp_port)),
        ("SSL", lambda: smtplib.SMTP_SSL(smtp_server, smtp_port, timeout=30)),
        ("plain", lambda: smtplib.SMTP(smtp_server, smtp_port, timeout=30)),
    ]
    for label, make in attempts:
        try:
            server = make()
            break
        except Exception as e:
            logger.debug(f"Failed to use {label}: {e}")
            # Release any connection made before the failure so a retry on
            # the same port never leaks the earlier socket.
            with suppress(Exception):
                if server is not None:
                    server.quit()
                    server = None

    if server is None:
        # All connection modes failed — surface the real problem instead of
        # a misleading AttributeError on None.login.
        raise ConnectionError(f"SMTP connection failed for {smtp_server}:{smtp_port} (TLS/SSL/plain all failed)")

    try:
        server.login(sender, password)
        server.sendmail(sender, receivers, msg.as_string())
    finally:
        with suppress(Exception):
            server.quit()
