"""Tests for ArxivRetriever (pure RSS feed — no query API)."""

import time
from types import SimpleNamespace

import zotero_arxiv_daily.retriever.arxiv_retriever as arxiv_retriever
from zotero_arxiv_daily.retriever.arxiv_retriever import (
    ArxivRetriever,
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
    monkeypatch.setattr("zotero_arxiv_daily.retriever.base.sleep", lambda _: None)
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
