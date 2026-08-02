"""Tests for zotero_arxiv_daily.executor: normalize_path_patterns, filter_corpus, fetch_zotero_corpus, E2E."""

from datetime import datetime

import pytest
from omegaconf import OmegaConf

from zotero_arxiv_daily.executor import Executor, normalize_path_patterns
from zotero_arxiv_daily.protocol import CorpusPaper

# ---------------------------------------------------------------------------
# normalize_path_patterns — migrated from test_include_path.py
# ---------------------------------------------------------------------------


def test_normalize_path_patterns_rejects_single_string_for_include_path():
    with pytest.raises(TypeError, match="config.zotero.include_path must be a list"):
        normalize_path_patterns("2026/survey/**", "include_path")


def test_normalize_path_patterns_accepts_list_config_for_include_path():
    include_path = OmegaConf.create(["2026/survey/**", "2026/reading-group/**"])
    assert normalize_path_patterns(include_path, "include_path") == [
        "2026/survey/**",
        "2026/reading-group/**",
    ]


def test_normalize_path_patterns_rejects_single_string_for_ignore_path():
    with pytest.raises(TypeError, match="config.zotero.ignore_path must be a list"):
        normalize_path_patterns("archive/**", "ignore_path")


def test_normalize_path_patterns_accepts_list_config_for_ignore_path():
    ignore_path = OmegaConf.create(["archive/**", "2025/**"])
    assert normalize_path_patterns(ignore_path, "ignore_path") == ["archive/**", "2025/**"]


def test_normalize_path_patterns_accepts_empty_list():
    assert normalize_path_patterns([], "ignore_path") == []


def test_normalize_path_patterns_accepts_none():
    assert normalize_path_patterns(None, "include_path") is None


# ---------------------------------------------------------------------------
# filter_corpus — migrated from test_include_path.py
# ---------------------------------------------------------------------------


def _make_executor(include_patterns=None, ignore_patterns=None):
    executor = Executor.__new__(Executor)
    executor.include_path_patterns = normalize_path_patterns(include_patterns, "include_path") if include_patterns else None
    executor.ignore_path_patterns = normalize_path_patterns(ignore_patterns, "ignore_path") if ignore_patterns else None
    return executor


def test_filter_corpus_matches_any_path_against_any_pattern():
    executor = _make_executor(include_patterns=["2026/survey/**", "2026/reading-group/**"])
    corpus = [
        CorpusPaper(title="Survey Paper", abstract="", added_date=datetime(2026, 1, 1), paths=["2026/survey/topic-a", "archive/misc"]),
        CorpusPaper(title="Reading Group Paper", abstract="", added_date=datetime(2026, 1, 2), paths=["notes/inbox", "2026/reading-group/week-1"]),
        CorpusPaper(title="Excluded Paper", abstract="", added_date=datetime(2026, 1, 3), paths=["2025/other/topic"]),
    ]
    filtered = executor.filter_corpus(corpus)
    assert [p.title for p in filtered] == ["Survey Paper", "Reading Group Paper"]


def test_filter_corpus_excludes_papers_matching_ignore_path():
    executor = _make_executor(ignore_patterns=["archive/**", "2025/**"])
    corpus = [
        CorpusPaper(title="Active Paper", abstract="", added_date=datetime(2026, 1, 1), paths=["2026/survey/topic-a"]),
        CorpusPaper(title="Archived Paper", abstract="", added_date=datetime(2026, 1, 2), paths=["archive/misc"]),
        CorpusPaper(title="Old Paper", abstract="", added_date=datetime(2026, 1, 3), paths=["2025/other/topic"]),
    ]
    filtered = executor.filter_corpus(corpus)
    assert [p.title for p in filtered] == ["Active Paper"]


def test_filter_corpus_ignore_path_takes_precedence_over_include_path():
    executor = _make_executor(include_patterns=["2026/**"], ignore_patterns=["2026/ignore/**"])
    corpus = [
        CorpusPaper(title="Included Paper", abstract="", added_date=datetime(2026, 1, 1), paths=["2026/survey/topic-a"]),
        CorpusPaper(title="Ignored Paper", abstract="", added_date=datetime(2026, 1, 2), paths=["2026/ignore/topic-b"]),
    ]
    filtered = executor.filter_corpus(corpus)
    assert [p.title for p in filtered] == ["Included Paper"]


def test_filter_corpus_no_filters_returns_all():
    executor = _make_executor()
    corpus = [
        CorpusPaper(title="Paper A", abstract="", added_date=datetime(2026, 1, 1), paths=["foo"]),
        CorpusPaper(title="Paper B", abstract="", added_date=datetime(2026, 1, 2), paths=["bar"]),
    ]
    filtered = executor.filter_corpus(corpus)
    assert filtered == corpus


# ---------------------------------------------------------------------------
# fetch_zotero_corpus
# ---------------------------------------------------------------------------


def test_fetch_zotero_corpus(config, monkeypatch):
    from tests.canned_responses import make_stub_zotero_client

    stub_zot = make_stub_zotero_client()
    monkeypatch.setattr("zotero_arxiv_daily.executor.zotero.Zotero", lambda *a, **kw: stub_zot)

    executor = Executor.__new__(Executor)
    executor.config = config
    corpus = executor.fetch_zotero_corpus()

    assert len(corpus) == 2
    assert corpus[0].title == "Stub Paper 1"
    assert "survey/topic-a" in corpus[0].paths[0]


def test_fetch_zotero_corpus_paper_with_zero_collections(config, monkeypatch):
    from tests.canned_responses import make_stub_zotero_client

    items = [
        {
            "data": {
                "title": "No Collection Paper",
                "abstractNote": "Abstract.",
                "dateAdded": "2026-03-01T00:00:00Z",
                "collections": [],
            }
        }
    ]
    stub_zot = make_stub_zotero_client(items=items)
    monkeypatch.setattr("zotero_arxiv_daily.executor.zotero.Zotero", lambda *a, **kw: stub_zot)

    executor = Executor.__new__(Executor)
    executor.config = config
    corpus = executor.fetch_zotero_corpus()

    assert len(corpus) == 1
    assert corpus[0].paths == []


# ---------------------------------------------------------------------------
# E2E: Executor.run()
# ---------------------------------------------------------------------------


def test_run_end_to_end(config, monkeypatch):
    """Full pipeline: Zotero fetch -> filter -> retrieve -> rerank -> TLDR -> email."""
    import smtplib

    from omegaconf import open_dict

    from tests.canned_responses import (
        make_sample_paper,
        make_stub_openai_client,
        make_stub_smtp,
        make_stub_zotero_client,
    )

    # Config: source=["arxiv"], reranker="api", send_empty=false
    with open_dict(config):
        config.executor.source = ["arxiv"]
        config.executor.reranker = "api"
        config.executor.send_empty = False

    # 1. Stub pyzotero
    stub_zot = make_stub_zotero_client()
    monkeypatch.setattr("zotero_arxiv_daily.executor.zotero.Zotero", lambda *a, **kw: stub_zot)

    # 2. Stub OpenAI (for reranker + TLDR/affiliations)
    stub_client = make_stub_openai_client()
    monkeypatch.setattr("zotero_arxiv_daily.executor.OpenAI", lambda **kw: stub_client)
    monkeypatch.setattr("zotero_arxiv_daily.reranker.api.OpenAI", lambda **kw: stub_client)
    retrieved = [
        make_sample_paper(title="E2E Paper 1", score=None),
        make_sample_paper(title="E2E Paper 2", score=None),
    ]

    # Import to register the arxiv retriever
    import zotero_arxiv_daily.retriever.arxiv_retriever  # noqa: F401
    from zotero_arxiv_daily.retriever.base import registered_retrievers

    monkeypatch.setattr(
        registered_retrievers["arxiv"],
        "retrieve_papers",
        lambda self: retrieved,
    )

    # 4. Stub SMTP
    sent = []
    monkeypatch.setattr(smtplib, "SMTP", make_stub_smtp(sent))

    # 5. Stub sleep (reranker/retriever)

    # 6. Run
    executor = Executor(config)
    executor.run()

    # Assertions
    assert len(sent) == 1, "Email should have been sent"
    _, _, email_body = sent[0]
    assert "text/html" in email_body


def test_run_no_papers_send_empty_false(config, monkeypatch):
    """When no papers are found and send_empty=false, no email is sent."""
    import smtplib

    from omegaconf import open_dict

    from tests.canned_responses import make_stub_openai_client, make_stub_smtp, make_stub_zotero_client

    with open_dict(config):
        config.executor.source = ["arxiv"]
        config.executor.reranker = "api"
        config.executor.send_empty = False

    stub_zot = make_stub_zotero_client()
    monkeypatch.setattr("zotero_arxiv_daily.executor.zotero.Zotero", lambda *a, **kw: stub_zot)

    stub_client = make_stub_openai_client()
    monkeypatch.setattr("zotero_arxiv_daily.executor.OpenAI", lambda **kw: stub_client)
    monkeypatch.setattr("zotero_arxiv_daily.reranker.api.OpenAI", lambda **kw: stub_client)

    import zotero_arxiv_daily.retriever.arxiv_retriever  # noqa: F401
    from zotero_arxiv_daily.retriever.base import registered_retrievers

    monkeypatch.setattr(registered_retrievers["arxiv"], "retrieve_papers", lambda self: [])

    sent = []
    monkeypatch.setattr(smtplib, "SMTP", make_stub_smtp(sent))

    executor = Executor(config)
    executor.run()

    assert len(sent) == 0, "No email should be sent when no papers and send_empty=false"


def test_run_no_papers_send_empty_true(config, monkeypatch):
    """When no papers are found and send_empty=true, empty email is sent."""
    import smtplib

    from omegaconf import open_dict

    from tests.canned_responses import make_stub_openai_client, make_stub_smtp, make_stub_zotero_client

    with open_dict(config):
        config.executor.source = ["arxiv"]
        config.executor.reranker = "api"
        config.executor.send_empty = True

    stub_zot = make_stub_zotero_client()
    monkeypatch.setattr("zotero_arxiv_daily.executor.zotero.Zotero", lambda *a, **kw: stub_zot)

    stub_client = make_stub_openai_client()
    monkeypatch.setattr("zotero_arxiv_daily.executor.OpenAI", lambda **kw: stub_client)
    monkeypatch.setattr("zotero_arxiv_daily.reranker.api.OpenAI", lambda **kw: stub_client)

    import zotero_arxiv_daily.retriever.arxiv_retriever  # noqa: F401
    from zotero_arxiv_daily.retriever.base import registered_retrievers

    monkeypatch.setattr(registered_retrievers["arxiv"], "retrieve_papers", lambda self: [])

    sent = []
    monkeypatch.setattr(smtplib, "SMTP", make_stub_smtp(sent))

    executor = Executor(config)
    executor.run()

    assert len(sent) == 1, "Email should be sent even with no papers when send_empty=true"
    _, _, body = sent[0]
    assert "text/html" in body


def test_populate_full_text_skips_when_present(config, monkeypatch):
    """_populate_full_text does not re-fetch when full_text already exists."""
    from tests.canned_responses import make_sample_paper
    from zotero_arxiv_daily.executor import Executor

    executor = Executor(config)
    fetched: list[str] = []

    class FakeRetriever:
        def fetch_full_text(self, paper):
            fetched.append(paper.title)
            return "fetched"

    executor.retrievers["arxiv"] = FakeRetriever()
    paper = make_sample_paper(full_text="already have")
    executor._populate_full_text(paper)
    assert fetched == []
    assert paper.full_text == "already have"


def test_populate_full_text_handles_failure(config, monkeypatch):
    """_populate_full_text swallows fetch errors (TLDR falls back to abstract)."""
    from tests.canned_responses import make_sample_paper
    from zotero_arxiv_daily.executor import Executor

    executor = Executor(config)

    class BoomRetriever:
        def fetch_full_text(self, paper):
            raise RuntimeError("boom")

    executor.retrievers["arxiv"] = BoomRetriever()
    paper = make_sample_paper(full_text=None)
    executor._populate_full_text(paper)  # must not raise
    assert paper.full_text is None


def test_generate_summaries_runs_all_papers(config, monkeypatch):
    """Concurrent summary generation covers every paper."""
    from tests.canned_responses import make_sample_paper, make_stub_openai_client
    from zotero_arxiv_daily.executor import Executor

    executor = Executor(config)
    executor.openai_client = make_stub_openai_client()
    monkeypatch.setattr(executor, "_populate_full_text", lambda p: None)

    papers = [make_sample_paper(title=f"Concurrent Paper {i}") for i in range(3)]
    executor._generate_summaries(papers)
    for p in papers:
        assert p.tldr is not None, p.title
        assert p.affiliations is not None, p.title


def test_validate_config_missing_zotero_user_id(config):
    import pytest
    from omegaconf import open_dict

    from zotero_arxiv_daily.executor import Executor

    with open_dict(config.zotero):
        config.zotero.user_id = None
    with pytest.raises(ValueError, match="zotero.user_id"):
        Executor(config)


def test_validate_config_missing_email_sender(config):
    import pytest
    from omegaconf import open_dict

    from zotero_arxiv_daily.executor import Executor

    with open_dict(config.email):
        config.email.sender = None
    with pytest.raises(ValueError, match="email.sender"):
        Executor(config)


def test_validate_config_missing_reranker_api_model(config):
    import pytest
    from omegaconf import open_dict

    from zotero_arxiv_daily.executor import Executor

    with open_dict(config.reranker.api):
        config.reranker.api.model = None
    with pytest.raises(ValueError, match="reranker.api.model"):
        Executor(config)


def test_dedupe_papers_by_url():
    """Duplicate papers (cross-listed categories) are dropped by URL."""
    from tests.canned_responses import make_sample_paper
    from zotero_arxiv_daily.executor import Executor

    papers = [
        make_sample_paper(title="A", url="https://arxiv.org/abs/1"),
        make_sample_paper(title="A dup", url="https://arxiv.org/abs/1"),
        make_sample_paper(title="B", url="https://arxiv.org/abs/2"),
    ]
    deduped = Executor._dedupe_papers(papers)
    assert [p.title for p in deduped] == ["A", "B"]


def test_dedupe_papers_by_normalized_title():
    """The same preprint on arXiv + bioRxiv (different URLs) is dropped by title."""
    from tests.canned_responses import make_sample_paper
    from zotero_arxiv_daily.executor import Executor

    papers = [
        make_sample_paper(title="Attention Is All You Need", source="arxiv", url="https://arxiv.org/abs/1706.03762"),
        make_sample_paper(title="Attention is all you need!", source="biorxiv", url="https://www.biorxiv.org/content/10.1101/123v1"),
        make_sample_paper(title="A Different Paper", source="arxiv", url="https://arxiv.org/abs/9999.99999"),
    ]
    deduped = Executor._dedupe_papers(papers)
    assert [p.title for p in deduped] == ["Attention Is All You Need", "A Different Paper"]


def test_normalize_title():
    from zotero_arxiv_daily.executor import Executor

    assert Executor._normalize_title("Attention Is All You Need") == "attentionisallyouneed"
    assert Executor._normalize_title("Attention is all you need!") == "attentionisallyouneed"


def test_filter_min_score_keeps_above_threshold():
    from tests.canned_responses import make_sample_paper
    from zotero_arxiv_daily.executor import Executor

    executor = Executor.__new__(Executor)
    cfg = OmegaConf.create({"executor": {"min_score": 5.0}})
    executor.config = cfg
    papers = [
        make_sample_paper(title="High", score=8.0),
        make_sample_paper(title="Mid", score=5.0),
        make_sample_paper(title="Low", score=1.5),
        make_sample_paper(title="No score"),
    ]
    kept = executor._filter_min_score(papers)
    assert [p.title for p in kept] == ["High", "Mid"]


def test_filter_min_score_none_keeps_all():
    from tests.canned_responses import make_sample_paper
    from zotero_arxiv_daily.executor import Executor

    executor = Executor.__new__(Executor)
    executor.config = OmegaConf.create({"executor": {"min_score": None}})
    papers = [make_sample_paper(title="Low", score=0.1)]
    assert executor._filter_min_score(papers) == papers
