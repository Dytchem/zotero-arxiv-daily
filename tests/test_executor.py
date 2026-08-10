"""Tests for zotero_arxiv_daily.executor: normalize_path_patterns, filter_corpus, fetch_zotero_corpus, E2E."""

from datetime import datetime
from pathlib import Path

import pytest
from omegaconf import OmegaConf, open_dict

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


def test_fetch_zotero_corpus_keeps_empty_abstract_with_title_fallback(config, monkeypatch):
    """Papers imported from PDFs often have an empty abstractNote; they must
    stay in the corpus (title used as the embedding/profile fallback) instead
    of shrinking the research profile to almost nothing."""
    from tests.canned_responses import make_stub_zotero_client

    items = [
        {
            "data": {
                "title": "PDF Import Paper",
                "abstractNote": "",
                "dateAdded": "2026-03-01T00:00:00Z",
                "collections": ["COL1"],
            }
        },
        {
            "data": {
                "title": "Regular Paper",
                "abstractNote": "A real abstract.",
                "dateAdded": "2026-03-02T00:00:00Z",
                "collections": [],
            }
        },
    ]
    stub_zot = make_stub_zotero_client(items=items)
    monkeypatch.setattr("zotero_arxiv_daily.executor.zotero.Zotero", lambda *a, **kw: stub_zot)

    executor = Executor.__new__(Executor)
    executor.config = config
    corpus = executor.fetch_zotero_corpus()

    assert len(corpus) == 2
    # Empty abstract falls back to the title so embeddings/profile still work.
    assert corpus[0].abstract == "PDF Import Paper"
    assert corpus[1].abstract == "A real abstract."


# ---------------------------------------------------------------------------
# E2E: Executor.run()
# ---------------------------------------------------------------------------


def test_run_end_to_end(config, monkeypatch, tmp_path):
    """Full pipeline: Zotero fetch -> filter -> retrieve -> rerank -> agent -> email."""
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
        # Run state (sent-history, embeddings, emails) must never touch the
        # repo's real .cache — this test used to pollute it and could break
        # the owner's daily run.
        config.executor.cache_dir = str(tmp_path)

    # 1. Stub pyzotero
    stub_zot = make_stub_zotero_client()
    monkeypatch.setattr("zotero_arxiv_daily.executor.zotero.Zotero", lambda *a, **kw: stub_zot)

    # 2. Stub OpenAI (reranker + harness agent)
    stub_client = make_stub_openai_client()
    monkeypatch.setattr("zotero_arxiv_daily.reranker.api.OpenAI", lambda **kw: stub_client)
    monkeypatch.setattr("zotero_arxiv_daily.harness.OpenAI", lambda **kw: stub_client)
    retrieved = [
        make_sample_paper(title="E2E Paper 1", score=None, url="https://arxiv.org/abs/test-e2e-paper-1"),
        make_sample_paper(title="E2E Paper 2", score=None, url="https://arxiv.org/abs/test-e2e-paper-2"),
    ]

    # Import to register the arxiv retriever
    import zotero_arxiv_daily.retriever.arxiv_retriever  # noqa: F401
    from zotero_arxiv_daily.retriever.base import registered_retrievers

    monkeypatch.setattr(
        registered_retrievers["arxiv"],
        "retrieve_papers",
        lambda self: retrieved,
    )

    # 3. Stub full-text prefetch (avoid real network in tests)
    executor = Executor(config)
    monkeypatch.setattr(executor, "_maybe_fetch_full_texts", lambda papers: None)

    # 4. Stub SMTP
    sent = []
    monkeypatch.setattr(smtplib, "SMTP", make_stub_smtp(sent))

    # 5. Run
    # Defensive clean: reranker.api writes a corpus_embeddings.json next to
    # sent_papers.json; stale state from a previous run can otherwise leak
    # into this test and cause `Dropped N already sent` even on a fresh tmp.
    cache_dir = Path(config.executor.cache_dir)
    for stale in (cache_dir / "corpus_embeddings.json", cache_dir / "sent_papers.json"):
        if stale.exists():
            stale.unlink()
    executor.run()

    # Assertions
    assert len(sent) == 1, "Email should have been sent"
    _, _, email_body = sent[0]
    assert "text/html" in email_body


def test_run_no_papers_send_empty_false(config, monkeypatch, tmp_path):
    """When no papers are found and send_empty=false, no email is sent."""
    import smtplib

    from omegaconf import open_dict

    from tests.canned_responses import make_stub_openai_client, make_stub_smtp, make_stub_zotero_client

    with open_dict(config):
        config.executor.source = ["arxiv"]
        config.executor.reranker = "api"
        config.executor.send_empty = False
        config.executor.cache_dir = str(tmp_path)
        config.reranker.api.cache_dir = str(tmp_path)

    stub_zot = make_stub_zotero_client()
    monkeypatch.setattr("zotero_arxiv_daily.executor.zotero.Zotero", lambda *a, **kw: stub_zot)

    stub_client = make_stub_openai_client()
    monkeypatch.setattr("zotero_arxiv_daily.reranker.api.OpenAI", lambda **kw: stub_client)
    monkeypatch.setattr("zotero_arxiv_daily.harness.OpenAI", lambda **kw: stub_client)

    import zotero_arxiv_daily.retriever.arxiv_retriever  # noqa: F401
    from zotero_arxiv_daily.retriever.base import registered_retrievers

    monkeypatch.setattr(registered_retrievers["arxiv"], "retrieve_papers", lambda self: [])

    sent = []
    monkeypatch.setattr(smtplib, "SMTP", make_stub_smtp(sent))

    executor = Executor(config)
    executor.run()

    assert len(sent) == 0, "No email should be sent when no papers and send_empty=false"


def test_run_no_papers_send_empty_true(config, monkeypatch, tmp_path):
    """When no papers are found and send_empty=true, empty email is sent."""
    import smtplib

    from omegaconf import open_dict

    from tests.canned_responses import make_stub_openai_client, make_stub_smtp, make_stub_zotero_client

    with open_dict(config):
        config.executor.source = ["arxiv"]
        config.executor.reranker = "api"
        config.executor.send_empty = True
        config.executor.cache_dir = str(tmp_path)
        config.reranker.api.cache_dir = str(tmp_path)

    stub_zot = make_stub_zotero_client()
    monkeypatch.setattr("zotero_arxiv_daily.executor.zotero.Zotero", lambda *a, **kw: stub_zot)

    stub_client = make_stub_openai_client()
    monkeypatch.setattr("zotero_arxiv_daily.reranker.api.OpenAI", lambda **kw: stub_client)
    monkeypatch.setattr("zotero_arxiv_daily.harness.OpenAI", lambda **kw: stub_client)

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


def test_populate_full_text_hits_disk_cache(config, monkeypatch, tmp_path):
    """Full text fetched once is cached on disk and reused on later runs
    (no re-download/re-parse of the same PDFs)."""
    from tests.canned_responses import make_sample_paper
    from zotero_arxiv_daily.executor import Executor

    executor = Executor(config)
    with open_dict(config.executor):
        config.executor.cache_dir = str(tmp_path)
    executor = Executor(config)
    fetched: list[str] = []

    class FakeRetriever:
        def fetch_full_text(self, paper):
            fetched.append(paper.title)
            return "FULL TEXT OF " + paper.title

    executor.retrievers["arxiv"] = FakeRetriever()
    paper = make_sample_paper(title="CacheMe", url="https://arxiv.org/abs/42", full_text=None)
    executor._populate_full_text(paper)
    assert fetched == ["CacheMe"]
    assert paper.full_text == "FULL TEXT OF CacheMe"

    # Second paper with the same URL: hits cache, no fetch.
    paper2 = make_sample_paper(title="CacheMe", url="https://arxiv.org/abs/42", full_text=None)
    executor._populate_full_text(paper2)
    assert fetched == ["CacheMe"]  # no second fetch
    assert paper2.full_text == "FULL TEXT OF CacheMe"


def test_full_text_cache_bounded(config, tmp_path):
    """The disk cache does not grow without limit."""
    from zotero_arxiv_daily.executor import Executor

    with open_dict(config.executor):
        config.executor.cache_dir = str(tmp_path)
        config.executor.full_text_cache_max = 5
    executor = Executor(config)
    for i in range(10):
        executor._save_full_text(f"https://arxiv.org/abs/{i}", "x" * 100)
    import json as _json

    data = _json.loads((tmp_path / "full_texts.json").read_text())
    assert len(data) <= 5
    assert "https://arxiv.org/abs/9" in data
    assert "https://arxiv.org/abs/0" not in data  # oldest evicted


def test_generate_summaries_runs_all_papers(config, monkeypatch):
    """Legacy per-paper TLDR/affiliations path was removed in the harness refactor.

    The single HarnessAgent now owns all editorial output. This test verifies the
    executor's digest path: _agent_digest returns a Digest (fallback when the
    agent can't run), and selected papers flow to the renderer.
    """
    from tests.canned_responses import make_sample_paper
    from zotero_arxiv_daily.executor import Executor
    from zotero_arxiv_daily.harness import Digest

    executor = Executor(config)
    papers = [
        make_sample_paper(title=f"Concurrent Paper {i}", url=f"https://arxiv.org/abs/x{i}")
        for i in range(3)
    ]
    # Disable the LLM harness so _agent_digest takes the fallback path.
    from omegaconf import open_dict

    with open_dict(config.llm.harness):
        config.llm.harness.enabled = False
    digest = executor._agent_digest(papers, [])
    assert isinstance(digest, Digest)
    assert len(digest.papers) == 3


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


# ---------------------------------------------------------------------------
# keyword filter
# ---------------------------------------------------------------------------


def test_filter_keywords_include_keeps_matching():
    from tests.canned_responses import make_sample_paper
    from zotero_arxiv_daily.executor import Executor

    executor = Executor.__new__(Executor)
    executor.config = OmegaConf.create({"executor": {"keywords_include": ["diffusion", "LLM"]}})
    papers = [
        make_sample_paper(title="Diffusion Models Rock", abstract="..."),
        make_sample_paper(title="Something Else", abstract="mentions llm here"),
        make_sample_paper(title="Unrelated", abstract="nothing"),
    ]
    kept = executor._filter_keywords(papers)
    assert [p.title for p in kept] == ["Diffusion Models Rock", "Something Else"]


def test_filter_keywords_exclude_drops_matching():
    from tests.canned_responses import make_sample_paper
    from zotero_arxiv_daily.executor import Executor

    executor = Executor.__new__(Executor)
    executor.config = OmegaConf.create({"executor": {"keywords_exclude": ["survey", "tutorial"]}})
    papers = [
        make_sample_paper(title="A Survey of X", abstract="..."),
        make_sample_paper(title="Good Paper", abstract="no bad words"),
        make_sample_paper(title="Tutorial Time", abstract="..."),
    ]
    kept = executor._filter_keywords(papers)
    assert [p.title for p in kept] == ["Good Paper"]


def test_filter_keywords_empty_config_keeps_all():
    from tests.canned_responses import make_sample_paper
    from zotero_arxiv_daily.executor import Executor

    executor = Executor.__new__(Executor)
    executor.config = OmegaConf.create({"executor": {"keywords_include": None, "keywords_exclude": None}})
    papers = [make_sample_paper(title="Any", abstract="x")]
    assert executor._filter_keywords(papers) == papers


# ---------------------------------------------------------------------------
# sent-history dedupe
# ---------------------------------------------------------------------------


def test_sent_history_roundtrip(tmp_path, monkeypatch):
    from zotero_arxiv_daily.executor import Executor

    executor = Executor.__new__(Executor)
    executor.config = OmegaConf.create({"executor": {"cache_dir": str(tmp_path)}})
    executor._save_sent_history({"https://arxiv.org/abs/1", "https://arxiv.org/abs/2"})
    assert executor._load_sent_history() == {"https://arxiv.org/abs/1", "https://arxiv.org/abs/2"}


def test_sent_history_missing_file_returns_empty(tmp_path):
    from zotero_arxiv_daily.executor import Executor

    executor = Executor.__new__(Executor)
    executor.config = OmegaConf.create({"executor": {"cache_dir": str(tmp_path)}})
    assert executor._load_sent_history() == set()


def test_filter_sent_history_drops_previous_runs(tmp_path):
    from tests.canned_responses import make_sample_paper
    from zotero_arxiv_daily.executor import Executor

    executor = Executor.__new__(Executor)
    executor.config = OmegaConf.create({"executor": {"cache_dir": str(tmp_path), "dedupe_history": True}})
    executor._save_sent_history({"https://arxiv.org/abs/1"})
    papers = [
        make_sample_paper(title="Old", url="https://arxiv.org/abs/1"),
        make_sample_paper(title="New", url="https://arxiv.org/abs/2"),
    ]
    kept = executor._filter_sent_history(papers)
    assert [p.title for p in kept] == ["New"]


def test_filter_sent_history_skipped_in_debug(tmp_path):
    from tests.canned_responses import make_sample_paper
    from zotero_arxiv_daily.executor import Executor

    executor = Executor.__new__(Executor)
    executor.config = OmegaConf.create({"executor": {"cache_dir": str(tmp_path), "dedupe_history": True}})
    executor._save_sent_history({"https://arxiv.org/abs/1"})
    papers = [make_sample_paper(title="Old", url="https://arxiv.org/abs/1")]
    executor.config.executor.debug = True
    assert executor._filter_sent_history(papers) == papers


def test_filter_sent_history_skipped_when_disabled(tmp_path):
    from tests.canned_responses import make_sample_paper
    from zotero_arxiv_daily.executor import Executor

    executor = Executor.__new__(Executor)
    executor.config = OmegaConf.create({"executor": {"cache_dir": str(tmp_path), "dedupe_history": False}})
    executor._save_sent_history({"https://arxiv.org/abs/1"})
    papers = [make_sample_paper(title="Old", url="https://arxiv.org/abs/1")]
    assert executor._filter_sent_history(papers) == papers


def test_run_debug_skips_delivery(config, monkeypatch, tmp_path):
    """Debug mode renders + archives the email but never sends it (README
    promises 'debug mode skips sending') — otherwise a local debug run would
    duplicate the daily email into the real inbox."""
    import smtplib

    from omegaconf import open_dict

    from tests.canned_responses import (
        make_sample_paper,
        make_stub_openai_client,
        make_stub_smtp,
        make_stub_zotero_client,
    )

    with open_dict(config):
        config.executor.source = ["arxiv"]
        config.executor.reranker = "api"
        config.executor.send_empty = False
        config.executor.debug = True
        config.executor.cache_dir = str(tmp_path)
        config.reranker.api.cache_dir = str(tmp_path)

    stub_zot = make_stub_zotero_client()
    monkeypatch.setattr("zotero_arxiv_daily.executor.zotero.Zotero", lambda *a, **kw: stub_zot)
    stub_client = make_stub_openai_client()
    monkeypatch.setattr("zotero_arxiv_daily.reranker.api.OpenAI", lambda **kw: stub_client)
    monkeypatch.setattr("zotero_arxiv_daily.harness.OpenAI", lambda **kw: stub_client)

    import zotero_arxiv_daily.retriever.arxiv_retriever  # noqa: F401
    from zotero_arxiv_daily.retriever.base import registered_retrievers

    monkeypatch.setattr(
        registered_retrievers["arxiv"],
        "retrieve_papers",
        lambda self: [
            make_sample_paper(title="Debug Paper", url="https://arxiv.org/abs/debug-1")
        ],
    )

    sent = []
    monkeypatch.setattr(smtplib, "SMTP", make_stub_smtp(sent))

    executor = Executor(config)
    monkeypatch.setattr(executor, "_maybe_fetch_full_texts", lambda papers: None)
    executor.run()

    assert len(sent) == 0, "Debug mode must not send email"
    # But the rendered email is still archived for review.
    assert (tmp_path / "last_email.html").exists()


# ---------------------------------------------------------------------------
# run report
# ---------------------------------------------------------------------------


def test_write_run_report(tmp_path):
    import json

    from zotero_arxiv_daily.executor import Executor

    executor = Executor.__new__(Executor)
    executor.config = OmegaConf.create({"executor": {"cache_dir": str(tmp_path), "source": ["arxiv"], "reranker": "api"}})
    executor._write_run_report(corpus=3, candidates=10, ranked=4, elapsed=2.5, failures=["biorxiv"])

    report_path = tmp_path / "last_run.json"
    assert report_path.exists()
    data = json.loads(report_path.read_text())
    assert data["corpus"] == 3
    assert data["candidates"] == 10
    assert data["ranked"] == 4
    assert data["elapsed_s"] == 2.5
    assert data["source"] == ["arxiv"]
    assert data["reranker"] == "api"
    assert data["source_failures"] == ["biorxiv"]
    assert "ts" in data


def test_write_run_report_does_not_raise_on_bad_config(tmp_path):
    """Report writing must never break the pipeline (best-effort)."""
    from zotero_arxiv_daily.executor import Executor

    executor = Executor.__new__(Executor)
    executor.config = OmegaConf.create({"executor": {"cache_dir": str(tmp_path)}})
    executor._write_run_report(corpus=1, candidates=2, ranked=3, elapsed=0.1)  # no raise


def test_run_single_source_failure_degrades(config, monkeypatch, tmp_path):
    """One failing source must not kill the run; others still deliver."""
    import smtplib

    from omegaconf import open_dict

    from tests.canned_responses import (
        make_sample_paper,
        make_stub_openai_client,
        make_stub_smtp,
        make_stub_zotero_client,
    )

    with open_dict(config):
        config.executor.source = ["arxiv", "biorxiv"]
        config.executor.reranker = "api"
        config.executor.send_empty = False
        config.executor.cache_dir = str(tmp_path)
        config.reranker.api.cache_dir = str(tmp_path)
        config.source.biorxiv = {"category": ["bioinformatics"]}

    stub_zot = make_stub_zotero_client()
    monkeypatch.setattr("zotero_arxiv_daily.executor.zotero.Zotero", lambda *a, **kw: stub_zot)
    stub_client = make_stub_openai_client()
    monkeypatch.setattr("zotero_arxiv_daily.reranker.api.OpenAI", lambda **kw: stub_client)
    monkeypatch.setattr("zotero_arxiv_daily.harness.OpenAI", lambda **kw: stub_client)

    import zotero_arxiv_daily.retriever.arxiv_retriever  # noqa: F401
    from zotero_arxiv_daily.retriever.base import registered_retrievers

    def _ok(self):
        return [make_sample_paper(title="OK Paper", url="https://arxiv.org/abs/degrade-ok")]

    def _boom(self):
        raise RuntimeError("biorxiv API down")

    monkeypatch.setattr(registered_retrievers["arxiv"], "retrieve_papers", _ok)
    monkeypatch.setattr(registered_retrievers["biorxiv"], "retrieve_papers", _boom)

    sent = []
    monkeypatch.setattr(smtplib, "SMTP", make_stub_smtp(sent))

    executor = Executor(config)
    monkeypatch.setattr(executor, "_maybe_fetch_full_texts", lambda papers: None)
    executor.run()

    assert len(sent) == 1, "Email must still be sent when one source fails"


# ---------------------------------------------------------------------------
# digest subject
# ---------------------------------------------------------------------------


def test_digest_subject_fixed_english():
    from zotero_arxiv_daily.executor import Executor

    executor = Executor.__new__(Executor)
    executor.config = OmegaConf.create({"llm": {"language": "English"}, "executor": {"source": ["arxiv"]}})
    subject = executor._digest_subject()
    assert subject.startswith("Zotero-arXiv-Daily Daily Digest · ")


def test_digest_subject_fixed_chinese():
    from zotero_arxiv_daily.executor import Executor

    executor = Executor.__new__(Executor)
    executor.config = OmegaConf.create({"llm": {"language": "Chinese"}, "executor": {"source": []}})
    subject = executor._digest_subject()
    assert subject.startswith("Zotero-arXiv-Daily 每日推荐 · ")
    assert "年" in subject and "月" in subject and "日" in subject


# ---------------------------------------------------------------------------
# Pi agent engine
# ---------------------------------------------------------------------------


def test_agent_digest_pi_falls_back_when_node_missing(config, monkeypatch):
    """engine=pi but node/agent/run.mjs unavailable → Python harness fallback."""
    from tests.canned_responses import make_sample_paper
    from zotero_arxiv_daily.executor import Executor
    from zotero_arxiv_daily.harness import Digest

    # Pi engine configured, but the node runtime is missing.
    with open_dict(config.llm.harness):
        config.llm.harness.engine = "pi"
        config.llm.harness.enabled = False  # harness disabled → fallback digest
    monkeypatch.setattr("zotero_arxiv_daily.executor.shutil.which", lambda name: None)

    executor = Executor(config)
    papers = [make_sample_paper(title=f"Pi Fallback {i}") for i in range(3)]
    digest = executor._agent_digest(papers, [])
    assert isinstance(digest, Digest)
    assert len(digest.papers) == 3


def test_agent_digest_pi_returns_none_when_node_fails(config, monkeypatch):
    """engine=pi, node present, but agent/run.mjs missing → None → fallback."""
    from tests.canned_responses import make_sample_paper
    from zotero_arxiv_daily.executor import Executor
    from zotero_arxiv_daily.harness import Digest

    with open_dict(config.llm.harness):
        config.llm.harness.engine = "pi"
        config.llm.harness.enabled = False
    monkeypatch.setattr("zotero_arxiv_daily.executor.shutil.which", lambda name: "/usr/bin/node")
    monkeypatch.setattr(
        "zotero_arxiv_daily.executor.Path.exists", lambda self: False
    )

    executor = Executor(config)
    papers = [make_sample_paper(title=f"Pi None {i}") for i in range(2)]
    digest = executor._agent_digest(papers, [])
    assert isinstance(digest, Digest)  # fell back to fallback_digest path
    assert len(digest.papers) == 2


def test_collect_shown_urls_records_picked_index_zero(config):
    """Regression: index=0 is a VALID pick and must be recorded in sent-history
    (the old `(p.index or -1)` falsy trap silently dropped the top pick)."""
    from tests.canned_responses import make_sample_paper
    from zotero_arxiv_daily.executor import Executor
    from zotero_arxiv_daily.harness import Digest, DigestPaper

    executor = Executor(config)
    originals = [make_sample_paper(title=f"P{i}", url=f"https://arxiv.org/abs/{i}") for i in range(3)]
    digest = Digest(
        subject="s", intro="", outro="",
        papers=[DigestPaper(index=0, reason="top pick")],
    )
    shown = executor._collect_shown_urls(digest, originals, candidate_count=3, ranked=[])
    assert originals[0].url in shown


def test_collect_shown_urls_covers_all_unpicked_candidates(config):
    """Regression: with a PARTIAL others list, every unpicked candidate shown
    in the email's others block must still be recorded (partial coverage used
    to leak papers into tomorrow's repeat)."""
    from tests.canned_responses import make_sample_paper
    from zotero_arxiv_daily.executor import Executor
    from zotero_arxiv_daily.harness import Digest, DigestPaper

    executor = Executor(config)
    originals = [make_sample_paper(title=f"P{i}", url=f"https://arxiv.org/abs/{i}") for i in range(4)]
    digest = Digest(
        subject="s", intro="", outro="",
        papers=[DigestPaper(index=0, reason="picked")],
        # agent only scored one unpicked candidate — the other two are still rendered
        others=[{"index": 1, "work_score": 6.0}],
    )
    shown = executor._collect_shown_urls(digest, originals, candidate_count=4, ranked=[])
    assert {originals[i].url for i in range(4)} <= shown


def test_collect_shown_urls_includes_rescued_pool_papers(config):
    """A filtered-out pool paper the agent scored in others is shown → recorded."""
    from tests.canned_responses import make_sample_paper
    from zotero_arxiv_daily.executor import Executor
    from zotero_arxiv_daily.harness import Digest, DigestPaper

    executor = Executor(config)
    originals = [make_sample_paper(title=f"P{i}", url=f"https://arxiv.org/abs/{i}") for i in range(5)]
    digest = Digest(
        subject="s", intro="", outro="",
        papers=[DigestPaper(index=0, reason="picked")],
        others=[{"index": 4, "work_score": 5.0}],  # 4 >= candidate_count=2: rescued
    )
    shown = executor._collect_shown_urls(digest, originals, candidate_count=2, ranked=[])
    assert originals[4].url in shown
