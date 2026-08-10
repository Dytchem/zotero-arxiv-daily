"""Retriever-specific fixtures."""

import feedparser
import pytest


@pytest.fixture()
def mock_feedparser(monkeypatch):
    """Patch feedparser.parse to return the local RSS fixture for arXiv URLs.

    The retriever fetches feeds via requests (timeout) and hands the bytes to
    feedparser.parse, so the requests mock echoes the URL as the response
    content — decoding it later yields the original URL string, which keeps
    the feedparser.parse mocks keyed by URL working unchanged.
    """
    from types import SimpleNamespace

    import requests

    parsed = feedparser.parse("tests/retriever/arxiv_rss_example.xml")
    # The fixture XML carries static dates (2025-08-20). Rewrite each entry's
    # published/updated timestamps to *now* so the lookback_days window filter
    # keeps them (the retriever only keeps papers from the last N days).
    from datetime import UTC, datetime, timedelta

    now = datetime.now(UTC)
    for i, entry in enumerate(parsed.entries):
        stamp = (now - timedelta(hours=2 + i)).timetuple()[:6]
        entry["published_parsed"] = stamp
        entry["updated_parsed"] = stamp
        entry["published"] = datetime(*stamp, tzinfo=UTC).strftime("%Y-%m-%dT%H:%M:%SZ")
        entry["updated"] = entry["published"]
    raw_parse = feedparser.parse

    def _patched(url_or_bytes, *args, **kwargs):
        target = url_or_bytes
        if isinstance(target, bytes):
            target = target.decode("utf-8", errors="ignore")
        if isinstance(target, str) and "rss.arxiv.org" in target:
            return parsed
        return raw_parse(url_or_bytes, *args, **kwargs)

    monkeypatch.setattr(feedparser, "parse", _patched)

    original_get = requests.get

    def _patched_get(url, **kwargs):
        if isinstance(url, str) and "arxiv.org" in url:
            resp = SimpleNamespace()
            resp.content = url.encode("utf-8")
            resp.raise_for_status = lambda: None
            return resp
        return original_get(url, **kwargs)

    monkeypatch.setattr(requests, "get", _patched_get)
    return parsed


@pytest.fixture()
def mock_biorxiv_api(monkeypatch):
    """Patch requests.get to return the canned bioRxiv API response."""
    from types import SimpleNamespace

    import requests

    from tests.canned_responses import SAMPLE_BIORXIV_API_RESPONSE

    original_get = requests.get

    def _patched(url, **kwargs):
        if "api.biorxiv.org" in url:
            resp = SimpleNamespace()
            resp.status_code = 200
            resp.json = lambda: SAMPLE_BIORXIV_API_RESPONSE
            resp.raise_for_status = lambda: None
            return resp
        return original_get(url, **kwargs)

    monkeypatch.setattr(requests, "get", _patched)
