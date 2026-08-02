"""Tests for the notifier plugin system."""

import smtplib
from types import SimpleNamespace

import pytest
from omegaconf import open_dict

from tests.canned_responses import make_stub_smtp
from zotero_arxiv_daily.notifier import (
    BaseNotifier,
    EmailNotifier,
    WebhookNotifier,
    _strip_html,
    available_notifiers,
    get_notifier_cls,
    register_notifier,
)


def test_register_and_get_notifier():
    @register_notifier("test_channel")
    class _Dummy(BaseNotifier):
        def send(self, html, subject=None):
            pass

    assert get_notifier_cls("test_channel").name == "test_channel"
    assert "test_channel" in available_notifiers()


def test_get_unknown_notifier_raises():
    with pytest.raises(ValueError, match="not found"):
        get_notifier_cls("nonexistent_channel_xyz")


def test_email_notifier_sends(config, monkeypatch):
    sent = []
    monkeypatch.setattr(smtplib, "SMTP", make_stub_smtp(sent))
    notifier = EmailNotifier(config)
    notifier.send("<html>hello</html>")
    assert len(sent) == 1


def test_webhook_notifier_posts_json(config, monkeypatch):
    import requests

    calls = []

    def _fake_post(url, data=None, headers=None, timeout=None):
        calls.append((url, data, headers))
        resp = SimpleNamespace(status_code=200)
        resp.raise_for_status = lambda: None
        return resp

    monkeypatch.setattr(requests, "post", _fake_post)
    with open_dict(config):
        config.webhook = {"url": "https://example.com/hook", "format": "text"}

    notifier = WebhookNotifier(config)
    notifier.send("<p>Hello <b>world</b></p>", subject="Daily arXiv")

    assert len(calls) == 1
    url, data, _headers = calls[0]
    assert url == "https://example.com/hook"
    import json

    body = json.loads(data)
    assert "Hello" in body["content"]
    assert "<b>" not in body["content"]
    assert body["title"] == "Daily arXiv"


def test_webhook_notifier_requires_url(config):
    with open_dict(config):
        config.webhook = {"url": ""}
    notifier = WebhookNotifier(config)
    with pytest.raises(ValueError, match="webhook.url"):
        notifier.send("<html></html>")


def test_webhook_notifier_raises_on_http_error(config, monkeypatch):
    import requests

    def _fake_post(url, data=None, headers=None, timeout=None):
        resp = SimpleNamespace(status_code=500)
        resp.raise_for_status = lambda: (_ for _ in ()).throw(
            requests.HTTPError("500")
        )
        return resp

    monkeypatch.setattr(requests, "post", _fake_post)
    with open_dict(config):
        config.webhook = {"url": "https://example.com/hook"}

    notifier = WebhookNotifier(config)
    with pytest.raises(requests.HTTPError):
        notifier.send("<html></html>")


def test_strip_html():
    html = "<style>.x{}</style><p>Hello <b>world</b></p><div>Second</div><br>Third"
    text = _strip_html(html)
    assert "Hello" in text
    assert "world" in text
    assert "Second" in text
    assert "Third" in text
    assert "<" not in text
