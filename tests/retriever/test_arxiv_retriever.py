"""Tests for ArxivRetriever."""

import time
from types import SimpleNamespace

import zotero_arxiv_daily.retriever.arxiv_retriever as arxiv_retriever
from zotero_arxiv_daily.retriever.arxiv_retriever import ArxivRetriever, _run_with_hard_timeout


def _sleep_and_return(value: str, delay_seconds: float) -> str:
    time.sleep(delay_seconds)
    return value


def _raise_runtime_error() -> None:
    raise RuntimeError("boom")


def test_arxiv_retriever(config, mock_feedparser, monkeypatch):
    monkeypatch.setattr("zotero_arxiv_daily.retriever.base.sleep", lambda _: None)

    # The RSS fixture gives us paper IDs.  After feedparser, the code calls
    # arxiv.Client().results(search) which makes real HTTP requests.  We mock
    # the arxiv Client so the test stays offline.
    new_entries = [
        e for e in mock_feedparser.entries
        if e.get("arxiv_announce_type", "new") == "new"
    ]
    # Build fake ArxivResult-like objects matching each RSS entry
    fake_results = []
    for entry in new_entries:
        pid = entry.id.removeprefix("oai:arXiv.org:")
        fake_results.append(SimpleNamespace(
            title=entry.title,
            authors=[SimpleNamespace(name="Test Author")],
            summary="Test abstract",
            pdf_url=f"https://arxiv.org/pdf/{pid}",
            entry_id=f"https://arxiv.org/abs/{pid}",
            source_url=lambda pid=pid: f"https://arxiv.org/e-print/{pid}",
        ))

    class FakeClient:
        def __init__(self, **kw):
            pass
        def results(self, search):
            return iter(fake_results)

    monkeypatch.setattr(arxiv_retriever.arxiv, "Client", FakeClient)

    # Skip file downloads in convert_to_paper
    monkeypatch.setattr(arxiv_retriever, "extract_text_from_html", lambda paper: None)
    monkeypatch.setattr(arxiv_retriever, "extract_text_from_pdf", lambda paper: None)
    monkeypatch.setattr(arxiv_retriever, "extract_text_from_tar", lambda paper: None)

    retriever = ArxivRetriever(config)
    papers = retriever.retrieve_papers()

    assert len(papers) == len(new_entries)
    assert {p.title for p in papers} == {e.title for e in new_entries}


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


def _make_http_error(code: int, msg: str):
    import arxiv
    return arxiv.HTTPError("https://export.arxiv.org/api/query", 0, code)


def _make_fake_result(pid: str):
    from types import SimpleNamespace
    return SimpleNamespace(
        title=f"Paper {pid}",
        authors=[SimpleNamespace(name="Test Author")],
        summary="Test abstract",
        pdf_url=f"https://arxiv.org/pdf/{pid}",
        entry_id=f"https://arxiv.org/abs/{pid}",
        source_url=lambda pid=pid: f"https://arxiv.org/e-print/{pid}",
    )


def test_batch_http_error_falls_back_to_per_paper(config, monkeypatch, mock_feedparser):
    """Batch HTTP error (e.g. 406) -> per-paper fallback; single failures skipped."""
    from zotero_arxiv_daily.retriever.arxiv_retriever import ArxivRetriever

    monkeypatch.setattr("zotero_arxiv_daily.retriever.base.sleep", lambda _: None)
    monkeypatch.setattr(arxiv_retriever, "sleep", lambda _: None)
    monkeypatch.setattr(arxiv_retriever, "extract_text_from_html", lambda paper: None)
    monkeypatch.setattr(arxiv_retriever, "extract_text_from_pdf", lambda paper: None)
    monkeypatch.setattr(arxiv_retriever, "extract_text_from_tar", lambda paper: None)

    new_entries = [e for e in mock_feedparser.entries if e.get("arxiv_announce_type", "new") == "new"]
    ids = [e.id.removeprefix("oai:arXiv.org:") for e in new_entries]
    broken_id = ids[0]

    def flaky_results(search):
        if len(search.id_list) > 1:
            raise _make_http_error(406, "Not Acceptable")
        pid = search.id_list[0]
        if pid == broken_id:
            raise _make_http_error(503, "Service Unavailable")
        return iter([_make_fake_result(pid)])

    class FlakyClient:
        def __init__(self, **kw):
            pass
        def results(self, search):
            return flaky_results(search)

    monkeypatch.setattr(arxiv_retriever.arxiv, "Client", FlakyClient)

    retriever = ArxivRetriever(config)
    papers = retriever.retrieve_papers()
    assert len(papers) == len(ids) - 1  # everything except the broken single paper


def test_batch_retries_on_429_then_succeeds(config, monkeypatch, mock_feedparser):
    """Batch HTTP 429 is retried with backoff, then succeeds."""
    from zotero_arxiv_daily.retriever.arxiv_retriever import ArxivRetriever

    monkeypatch.setattr("zotero_arxiv_daily.retriever.base.sleep", lambda _: None)
    monkeypatch.setattr(arxiv_retriever, "sleep", lambda _: None)
    monkeypatch.setattr(arxiv_retriever, "extract_text_from_html", lambda paper: None)
    monkeypatch.setattr(arxiv_retriever, "extract_text_from_pdf", lambda paper: None)
    monkeypatch.setattr(arxiv_retriever, "extract_text_from_tar", lambda paper: None)

    new_entries = [e for e in mock_feedparser.entries if e.get("arxiv_announce_type", "new") == "new"]
    ids = [e.id.removeprefix("oai:arXiv.org:") for e in new_entries]

    attempts = {"n": 0}

    def retry_results(search):
        attempts["n"] += 1
        if attempts["n"] < 3:
            raise _make_http_error(429, "Too Many Requests")
        return iter([_make_fake_result(pid) for pid in ids])

    class RetryClient:
        def __init__(self, **kw):
            pass
        def results(self, search):
            return retry_results(search)

    monkeypatch.setattr(arxiv_retriever.arxiv, "Client", RetryClient)

    retriever = ArxivRetriever(config)
    papers = retriever.retrieve_papers()
    assert attempts["n"] == 3
    assert len(papers) == len(ids)
