"""Tests for ArxivRetriever (pure RSS feed — no query API)."""

import time
from types import SimpleNamespace

import zotero_arxiv_daily.retriever.arxiv_retriever as arxiv_retriever
from zotero_arxiv_daily.retriever.arxiv_retriever import (
    ArxivRetriever,
    _extract_arxiv_id,
    _parse_abstract,
    _parse_authors,
    _rss_entry_to_paper,
    _run_with_hard_timeout,
)


def _sleep_and_return(value: str, delay_seconds: float) -> str:
    time.sleep(delay_seconds)
    return value


def _raise_runtime_error() -> None:
    raise RuntimeError("boom")


def test_arxiv_retriever_rss_driven(config, mock_feedparser, monkeypatch):
    """Papers are built straight from the RSS feed; no arxiv API involved."""
    monkeypatch.setattr(arxiv_retriever, "extract_text_from_html", lambda paper: None)
    monkeypatch.setattr(arxiv_retriever, "extract_text_from_pdf", lambda paper: None)
    monkeypatch.setattr(arxiv_retriever, "extract_text_from_tar", lambda paper: None)

    new_entries = [
        e for e in mock_feedparser.entries
        if e.get("arxiv_announce_type", "new") == "new"
    ]

    retriever = ArxivRetriever(config)
    papers = retriever.retrieve_papers()

    assert len(papers) == len(new_entries)
    assert {p.title for p in papers} == {e.title for e in new_entries}
    # Abstract is parsed out of the RSS summary, not the placeholder
    assert all(p.abstract and "Announce Type" not in p.abstract for p in papers)
    # URLs are derived from the (versioned) paper id in the feed
    paper = papers[0]
    assert paper.url.startswith("https://arxiv.org/abs/")
    assert paper.pdf_url.startswith("https://arxiv.org/pdf/")
    assert paper.source_url.startswith("https://arxiv.org/e-print/")
    assert paper.pdf_url == paper.source_url.replace("/e-print/", "/pdf/")
    assert paper.url.rsplit("/", 1)[-1] in paper.pdf_url


def test_parse_abstract_strips_rss_prefix():
    summary = "arXiv:2508.13426v1 Announce Type: new \nAbstract: A novel method for X."
    assert _parse_abstract(summary) == "A novel method for X."


def test_arxiv_retriever_fetches_each_category_feed(config, mock_feedparser, monkeypatch):
    """Each configured category is fetched as its own feed (avoids 1000-entry cap)."""
    import feedparser
    from omegaconf import open_dict

    fetched_urls: list[str] = []
    raw_parse = feedparser.parse

    def _recording_parse(url_or_bytes, *args, **kwargs):
        target = url_or_bytes.decode("utf-8", errors="ignore") if isinstance(url_or_bytes, bytes) else url_or_bytes
        if isinstance(target, str) and "rss.arxiv.org" in target:
            fetched_urls.append(target)
        return raw_parse(url_or_bytes, *args, **kwargs)

    monkeypatch.setattr(feedparser, "parse", _recording_parse)
    monkeypatch.setattr(arxiv_retriever, "extract_text_from_html", lambda paper: None)
    monkeypatch.setattr(arxiv_retriever, "extract_text_from_pdf", lambda paper: None)
    monkeypatch.setattr(arxiv_retriever, "extract_text_from_tar", lambda paper: None)

    with open_dict(config.source):
        config.source.arxiv = {"category": ["cs.AI", "cs.CV", "cs.LG"]}
    retriever = ArxivRetriever(config)
    retriever.retrieve_papers()

    assert fetched_urls == [
        "https://rss.arxiv.org/atom/cs.AI",
        "https://rss.arxiv.org/atom/cs.CV",
        "https://rss.arxiv.org/atom/cs.LG",
    ]


def test_arxiv_retriever_dedupes_cross_category(config, mock_feedparser, monkeypatch):
    """A paper appearing in two category feeds is returned only once."""
    import feedparser
    from omegaconf import open_dict

    new_entries = [
        e for e in mock_feedparser.entries
        if e.get("arxiv_announce_type", "new") == "new"
    ]
    # Build two different feeds that share one paper (cross-listed)
    shared = new_entries[0]
    other = new_entries[1] if len(new_entries) > 1 else new_entries[0]
    feed_a = SimpleNamespace(entries=[shared, other], feed=SimpleNamespace(title="feed a"))
    feed_b = SimpleNamespace(entries=[shared], feed=SimpleNamespace(title="feed b"))
    by_url = {
        "https://rss.arxiv.org/atom/cs.AI": feed_a,
        "https://rss.arxiv.org/atom/cs.CV": feed_b,
    }

    def _multi_parse(url_or_bytes, *args, **kwargs):
        target = url_or_bytes.decode("utf-8", errors="ignore") if isinstance(url_or_bytes, bytes) else url_or_bytes
        return by_url.get(target, SimpleNamespace(entries=[], feed=SimpleNamespace(title="")))

    monkeypatch.setattr(feedparser, "parse", _multi_parse)

    with open_dict(config.source):
        config.source.arxiv = {"category": ["cs.AI", "cs.CV"]}
    retriever = ArxivRetriever(config)
    raw = retriever._retrieve_raw_papers()

    # shared paper appears in both feeds but is deduped to one entry
    ids = [p["paper_id"] for p in raw]
    assert len(ids) == len(set(ids))
    assert _rss_entry_to_paper(shared)["paper_id"] in ids
    assert len(raw) == 2


def test_parse_abstract_without_prefix():
    assert _parse_abstract("Just an abstract.") == "Just an abstract."


def test_parse_authors_splits_comma_joined_names():
    entry = SimpleNamespace(authors=[{"name": "Alice A, Bob B, Carol C"}])
    assert _parse_authors(entry) == ["Alice A", "Bob B", "Carol C"]


def test_parse_authors_missing():
    assert _parse_authors(SimpleNamespace(authors=[])) == []


def test_rss_entry_to_paper_derives_urls(mock_feedparser):
    entry = next(e for e in mock_feedparser.entries if e.get("arxiv_announce_type", "new") == "new")
    paper = _rss_entry_to_paper(entry)
    pid = entry.id.removeprefix("oai:arXiv.org:")
    assert paper["paper_id"] == pid
    assert paper["url"] == entry.link
    assert paper["pdf_url"] == f"https://arxiv.org/pdf/{pid}"
    assert paper["source_url"] == f"https://arxiv.org/e-print/{pid}"


def test_extract_arxiv_id_formats():
    # canonical OAI prefix
    assert _extract_arxiv_id("oai:arXiv.org:2607.26142") == "2607.26142"
    # full abs / pdf URLs (previously produced broken /pdf/<url> links)
    assert _extract_arxiv_id("https://arxiv.org/abs/2607.26142v1") == "2607.26142v1"
    assert _extract_arxiv_id("https://arxiv.org/pdf/2607.26142v2") == "2607.26142v2"
    # old-style ids stay intact
    assert _extract_arxiv_id("oai:arXiv.org:cs/0501001") == "cs/0501001"
    # empty / junk
    assert _extract_arxiv_id("") == ""
    assert _extract_arxiv_id(None) == ""


def test_run_with_hard_timeout_returns_value():
    result = _run_with_hard_timeout(
        _sleep_and_return, ("done", 0.01), timeout=1, operation="test op", paper_title="paper"
    )
    assert result == "done"


def test_run_with_hard_timeout_returns_none_on_timeout(monkeypatch):
    warnings: list[str] = []
    monkeypatch.setattr(arxiv_retriever, "logger", SimpleNamespace(warning=warnings.append))
    result = _run_with_hard_timeout(
        _sleep_and_return, ("done", 1.0), timeout=0.01, operation="test op", paper_title="paper"
    )
    assert result is None
    assert "timed out" in warnings[0]


def test_run_with_hard_timeout_returns_none_on_failure(monkeypatch):
    warnings: list[str] = []
    monkeypatch.setattr(arxiv_retriever, "logger", SimpleNamespace(warning=warnings.append))
    result = _run_with_hard_timeout(
        _raise_runtime_error, (), timeout=1, operation="test op", paper_title="paper"
    )
    assert result is None
    assert "boom" in warnings[0]


def test_fetch_full_text_tries_tar_then_html_then_pdf(config, monkeypatch):
    """fetch_full_text falls back tar -> html -> pdf and returns first hit."""
    from tests.canned_responses import make_sample_paper

    calls: list[str] = []

    def fake_tar(paper):
        calls.append("tar")
        return None

    def fake_html(paper):
        calls.append("html")
        return None

    def fake_pdf(paper):
        calls.append("pdf")
        return "pdf text"

    monkeypatch.setattr(arxiv_retriever, "extract_text_from_tar", fake_tar)
    monkeypatch.setattr(arxiv_retriever, "extract_text_from_html", fake_html)
    monkeypatch.setattr(arxiv_retriever, "extract_text_from_pdf", fake_pdf)

    retriever = ArxivRetriever(config)
    result = retriever.fetch_full_text(make_sample_paper())
    assert result == "pdf text"
    assert calls == ["tar", "html", "pdf"]


def test_fetch_full_text_short_circuits_on_tar_hit(config, monkeypatch):
    """fetch_full_text stops at the first successful extraction."""
    from tests.canned_responses import make_sample_paper

    monkeypatch.setattr(arxiv_retriever, "extract_text_from_tar", lambda paper: "tar text")
    monkeypatch.setattr(
        arxiv_retriever, "extract_text_from_html",
        lambda paper: (_ for _ in ()).throw(AssertionError("html should not be called")),
    )
    retriever = ArxivRetriever(config)
    assert retriever.fetch_full_text(make_sample_paper()) == "tar text"


def test_arxiv_retriever_api_fallback_on_empty_rss(config, mock_feedparser, monkeypatch):
    """Empty RSS (weekend) + fallback_days set → pulls from the export API."""
    import feedparser
    from omegaconf import open_dict

    empty = SimpleNamespace(entries=[], feed=SimpleNamespace(title=""))
    api_feed = SimpleNamespace(
        entries=mock_feedparser.entries[:3],
        feed=SimpleNamespace(title="api"),
    )

    def _routing_parse(url_or_bytes, *args, **kwargs):
        target = url_or_bytes.decode("utf-8", errors="ignore") if isinstance(url_or_bytes, bytes) else url_or_bytes
        if "rss.arxiv.org" in target:
            return empty
        if "export.arxiv.org" in target:
            return api_feed
        return SimpleNamespace(entries=[], feed=SimpleNamespace(title=""))

    monkeypatch.setattr(feedparser, "parse", _routing_parse)
    monkeypatch.setattr(arxiv_retriever, "extract_text_from_html", lambda paper: None)
    monkeypatch.setattr(arxiv_retriever, "extract_text_from_pdf", lambda paper: None)
    monkeypatch.setattr(arxiv_retriever, "extract_text_from_tar", lambda paper: None)

    with open_dict(config.source):
        config.source.arxiv = {"category": ["cs.AI"], "fallback_days": 7}
    retriever = ArxivRetriever(config)
    papers = retriever.retrieve_papers()
    assert len(papers) == 3


def test_arxiv_retriever_no_fallback_when_rss_has_papers(config, mock_feedparser, monkeypatch):
    """fallback_days set but RSS non-empty → RSS wins, API never called."""
    import feedparser
    from omegaconf import open_dict

    called_api = []
    raw_parse = feedparser.parse

    def _recording_parse(url_or_bytes, *args, **kwargs):
        target = url_or_bytes.decode("utf-8", errors="ignore") if isinstance(url_or_bytes, bytes) else url_or_bytes
        if "export.arxiv.org" in target:
            called_api.append(target)
        return raw_parse(url_or_bytes, *args, **kwargs)

    monkeypatch.setattr(feedparser, "parse", _recording_parse)
    monkeypatch.setattr(arxiv_retriever, "extract_text_from_html", lambda paper: None)
    monkeypatch.setattr(arxiv_retriever, "extract_text_from_pdf", lambda paper: None)
    monkeypatch.setattr(arxiv_retriever, "extract_text_from_tar", lambda paper: None)

    with open_dict(config.source):
        config.source.arxiv = {"category": ["cs.AI"], "fallback_days": 7}
    retriever = ArxivRetriever(config)
    retriever.retrieve_papers()
    assert called_api == []


def test_arxiv_retriever_lookback_filters_old_papers(config, mock_feedparser, monkeypatch):
    """Papers older than lookback_days are dropped; recent ones are kept."""
    from datetime import UTC, datetime, timedelta

    from zotero_arxiv_daily.retriever.arxiv_retriever import ArxivRetriever

    # Only announce_type=new entries pass the announce filter; in the fixture
    # those are the last two entries. Push one of them into the past.
    entries = [e for e in mock_feedparser.entries if e.get("arxiv_announce_type", "new") == "new"]
    assert len(entries) == 2
    old = datetime.now(UTC) - timedelta(days=10)
    entries[0]["published_parsed"] = old.timetuple()[:6]
    entries[0]["updated_parsed"] = old.timetuple()[:6]
    entries[1]["published_parsed"] = datetime.now(UTC).timetuple()[:6]
    entries[1]["updated_parsed"] = datetime.now(UTC).timetuple()[:6]

    retriever = ArxivRetriever(config)
    papers = retriever.retrieve_papers()
    assert len(papers) == 1
    assert papers[0].title == entries[1]["title"]


def test_fetch_feed_with_retry_raises_after_retries(config, monkeypatch):
    """A persistently dead feed must surface as RuntimeError (round-3 fix) so
    run()'s per-source isolation + workflow failure notification kick in —
    not a silent 'no papers today'."""
    import requests as _req

    from zotero_arxiv_daily.retriever.arxiv_retriever import ArxivRetriever

    monkeypatch.setattr("zotero_arxiv_daily.retriever.arxiv_retriever.sleep", lambda s: None)
    monkeypatch.setattr(_req, "get", lambda url, **kw: (_ for _ in ()).throw(ConnectionError("down")))

    import pytest
    with pytest.raises(RuntimeError, match="after 4 attempts"):
        ArxivRetriever._fetch_feed_with_retry("https://rss.arxiv.org/atom/cs.AI", "cs.AI")
