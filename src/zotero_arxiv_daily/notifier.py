"""Notifier plugins — multiple delivery channels for the daily email digest.

A notifier turns the rendered HTML digest into an actual delivery on a
specific channel (email, Telegram, Server Chan, etc). Plugins register
themselves via ``@register_notifier`` so new channels can be added
without touching the executor.

Use case for this abstraction: the same digest can fan out to several
destinations (email + Telegram + Webhook) in a single run, and new
channels can be plugged in by writing one class.
"""

from __future__ import annotations

from abc import ABC, abstractmethod

from loguru import logger
from omegaconf import DictConfig

# ----------------------------------------------------------------------
# Registry — mirrors the retriever / reranker pattern in this project.
# ----------------------------------------------------------------------

_REGISTERED: dict[str, type[BaseNotifier]] = {}


def register_notifier(name: str):
    """Class decorator: ``@register_notifier("email")`` registers the plugin."""

    def _wrap(cls: type[BaseNotifier]) -> type[BaseNotifier]:
        if name in _REGISTERED:
            raise ValueError(f"Notifier {name!r} already registered")
        _REGISTERED[name] = cls
        cls.name = name
        return cls

    return _wrap


def get_notifier_cls(name: str) -> type[BaseNotifier]:
    if name not in _REGISTERED:
        available = ", ".join(sorted(_REGISTERED)) or "(none registered)"
        raise ValueError(f"Notifier {name!r} not found; available: {available}")
    return _REGISTERED[name]


def available_notifiers() -> list[str]:
    return sorted(_REGISTERED)


# ----------------------------------------------------------------------
# Base class
# ----------------------------------------------------------------------


class BaseNotifier(ABC):
    """Abstract base for delivery channels.

    Concrete notifiers read their own credentials / settings from
    ``self.config.<notifier name>.*`` (e.g. ``config.email.*`` for the
    built-in email notifier).
    """

    name: str = "base"

    def __init__(self, config: DictConfig):
        self.config = config

    @abstractmethod
    def send(self, html: str, subject: str | None = None) -> None:
        """Deliver the rendered HTML digest.

        Implementations should log a clear success/failure line so the
        user can see what happened even when the workflow runs in
        GitHub Actions with limited logging.
        """


# ----------------------------------------------------------------------
# Built-in implementations
# ----------------------------------------------------------------------


@register_notifier("email")
class EmailNotifier(BaseNotifier):
    """SMTP email delivery — the original behavior, lifted out of utils.py."""

    def send(self, html: str, subject: str | None = None) -> None:
        # Import here so tests patching smtplib only need to patch this module.
        from .email_sender import _collect_receivers, send_email

        send_email(self.config, html, subject=subject)
        logger.info(f"[notifier:email] sent to {_collect_receivers(self.config)}")


@register_notifier("webhook")
class WebhookNotifier(BaseNotifier):
    """Generic webhook delivery — POST the HTML body as JSON.

    Designed to plug into Telegram bots, Server酱, 钉钉 custom bots,
    Discord webhooks, Slack incoming webhooks, etc. by setting the URL
    and any custom headers in config.
    """

    def send(self, html: str, subject: str | None = None) -> None:
        import json

        import requests

        cfg = self.config.webhook
        url = cfg.url
        if not url:
            raise ValueError("webhook.url is required")
        # Most chat platforms want plain text, not HTML; default to HTML
        # but allow the user to opt into text via webhook.format.
        fmt = cfg.get("format") or "html"
        payload_field = cfg.get("payload_field") or "content"
        text = html if fmt == "html" else _strip_html(html)
        body = {payload_field: text}
        if subject:
            # Generic secondary field; some platforms (Server酱) use 'text'
            body.setdefault("title", subject)
        headers = cfg.get("headers") or {"Content-Type": "application/json"}
        try:
            resp = requests.post(url, data=json.dumps(body), headers=headers, timeout=15)
            resp.raise_for_status()
        except Exception as exc:
            logger.error(f"[notifier:webhook] POST {url} failed: {exc}")
            raise
        logger.info(f"[notifier:webhook] POST {url} → {resp.status_code}")


# ----------------------------------------------------------------------
# Helpers
# ----------------------------------------------------------------------


def _strip_html(html: str) -> str:
    """Cheap HTML-to-text for webhook backends that don't render HTML."""
    import re

    # Drop style/script blocks first
    text = re.sub(r"<(style|script)[^>]*>.*?</\1>", "", html, flags=re.DOTALL | re.IGNORECASE)
    # Replace block-level closers with newlines so paragraph breaks survive
    text = re.sub(r"</(p|div|li|h[1-6]|br|tr)[^>]*>", "\n", text, flags=re.IGNORECASE)
    text = re.sub(r"<br\s*/?>", "\n", text, flags=re.IGNORECASE)
    # Strip remaining tags
    text = re.sub(r"<[^>]+>", "", text)
    # Collapse whitespace
    text = re.sub(r"[ \t]+", " ", text)
    text = re.sub(r"\n{3,}", "\n\n", text)
    return text.strip()
