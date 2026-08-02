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
    msg['From'] = _format_addr(f'Github Action <{sender}>')
    msg['To'] = _format_addr(f'You <{receivers[0]}>')
    if len(receivers) > 1:
        msg['Cc'] = ', '.join(_format_addr(f'CC <{r}>') for r in receivers[1:])
    if subject is None:
        subject = f'Daily arXiv {datetime.datetime.now().strftime("%Y/%m/%d")}'
    msg['Subject'] = Header(subject, 'utf-8').encode()

    server = None
    try:
        server = smtplib.SMTP(smtp_server, smtp_port)
        server.starttls()
    except Exception as e:
        logger.debug(f"Failed to use TLS. {e}\nTry to use SSL.")
        try:
            server = smtplib.SMTP_SSL(smtp_server, smtp_port)
        except Exception as e:
            logger.debug(f"Failed to use SSL. {e}\nTry to use plain text.")
            server = smtplib.SMTP(smtp_server, smtp_port)

    try:
        server.login(sender, password)
        server.sendmail(sender, receivers, msg.as_string())
    finally:
        with suppress(Exception):
            server.quit()
