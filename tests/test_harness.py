"""Tests for the LLM Harness (research profile + LLM rerank)."""

from datetime import datetime
from types import SimpleNamespace

from omegaconf import open_dict

from tests.canned_responses import make_sample_corpus, make_sample_paper
from zotero_arxiv_daily.harness import (
    LLMHarness,
    ResearchProfile,
    _corpus_hash,
    _extract_json,
    _profile_prompt,
)
from zotero_arxiv_daily.protocol import CorpusPaper

# ---------------------------------------------------------------------------
# stub OpenAI client for the harness
# ---------------------------------------------------------------------------


def make_harness_client(profile_reply=None, rerank_reply=None, fail_on=None):
    """Quack like openai.OpenAI; returns canned chat completions."""
    calls = []

    def _create(**kwargs):
        messages = kwargs.get("messages", [])
        text = str(messages)
        calls.append(kwargs)
        if fail_on and fail_on in text:
            raise RuntimeError(f"boom: {fail_on}")
        if "Score each candidate" in text:  # rerank prompt
            content = rerank_reply or '[{"index": 1, "score": 9.0, "reason": "direct hit"}, {"index": 2, "score": 3.0, "reason": "tangential"}]'
        else:  # profile prompt
            content = profile_reply or (
                '{"topics": ["LLM", "retrieval"], "keywords": ["rerank", "embedding"], '
                '"methods": ["transformer"], "summary": "Interested in LLM-based retrieval."}'
            )
        return SimpleNamespace(
            choices=[SimpleNamespace(message=SimpleNamespace(content=content), finish_reason="stop")],
            id="stub", created=0, model="gpt-5.6-luna", object="chat.completion",
        )

    client = SimpleNamespace(chat=SimpleNamespace(completions=SimpleNamespace(create=_create)))
    return client, calls


def _harness_config(config, tmp_path, **overrides):
    with open_dict(config.llm):
        config.llm.harness = {
            "enabled": True,
            "top_k": 100,
            "batch_size": 25,
            "api": {
                "key": "sk-harness",
                "base_url": "https://openrouter.ai/api/v1",
                "model": "gpt-5.6-luna",
            },
        }
    with open_dict(config.executor):
        config.executor.cache_dir = str(tmp_path)
    return config


def _make_harness(config, tmp_path, monkeypatch, **client_kwargs):
    """Build an LLMHarness with the OpenAI client stub injected."""
    import zotero_arxiv_daily.harness as harness_mod

    cfg = _harness_config(config, tmp_path)
    client, calls = make_harness_client(**client_kwargs)
    monkeypatch.setattr(harness_mod, "OpenAI", lambda **kw: client)
    harness = harness_mod.LLMHarness(cfg)
    return harness, calls


# ---------------------------------------------------------------------------
# helpers
# ---------------------------------------------------------------------------


def test_extract_json_plain():
    assert _extract_json('{"a": 1}') == {"a": 1}


def test_extract_json_with_fences():
    assert _extract_json('```json\n{"a": 1}\n```') == {"a": 1}


def test_extract_json_with_prose():
    assert _extract_json('Here you go: [{"index": 1, "score": 5}]') == [{"index": 1, "score": 5}]


def test_corpus_hash_stable_and_sensitive():
    c1 = make_sample_corpus(3)
    c2 = make_sample_corpus(3)
    assert _corpus_hash(c1) == _corpus_hash(c2)
    c3 = [*make_sample_corpus(3), CorpusPaper(
        title="extra", abstract="x", added_date=datetime(2026, 2, 1), paths=[]
    )]
    assert _corpus_hash(c1) != _corpus_hash(c3)


def test_profile_prompt_truncates_long_abstracts():
    corpus = [CorpusPaper(
        title="T", abstract="x" * 5000, added_date=datetime(2026, 1, 1), paths=["a"]
    )]
    prompt = _profile_prompt(corpus, "English")
    assert len(prompt) < 1000


# ---------------------------------------------------------------------------
# build_profile
# ---------------------------------------------------------------------------


def test_build_profile_parses_llm_reply(config, tmp_path, monkeypatch):
    harness, _ = _make_harness(config, tmp_path, monkeypatch)
    profile = harness.build_profile(make_sample_corpus(3))
    assert profile is not None
    assert "LLM" in profile.topics
    assert "rerank" in profile.keywords
    assert profile.methods == ["transformer"]


def test_build_profile_caches_by_corpus_hash(config, tmp_path, monkeypatch):
    harness, calls = _make_harness(config, tmp_path, monkeypatch)
    corpus = make_sample_corpus(3)
    harness.build_profile(corpus)
    harness.build_profile(corpus)  # second call should hit cache
    profile_calls = [c for c in calls if "research profile" in str(c)]
    assert len(profile_calls) == 1


def test_build_profile_returns_none_on_failure(config, tmp_path, monkeypatch):
    harness, _ = _make_harness(config, tmp_path, monkeypatch, fail_on="research profile")
    assert harness.build_profile(make_sample_corpus(2)) is None


def test_build_profile_requires_own_api_entry(config, tmp_path):
    """Harness has its own provider entry; missing it disables the harness."""
    cfg = _harness_config(config, tmp_path)
    with open_dict(cfg.llm.harness):
        cfg.llm.harness.api = {"key": None, "base_url": None, "model": None}
    harness = LLMHarness(cfg)
    assert harness.enabled is False


# ---------------------------------------------------------------------------
# rerank
# ---------------------------------------------------------------------------


def test_rerank_reorders_and_sets_reason(config, tmp_path, monkeypatch):
    harness, _ = _make_harness(config, tmp_path, monkeypatch)
    profile = ResearchProfile(topics=["LLM"], keywords=[], methods=[], summary="s")
    papers = [
        make_sample_paper(title="First", url="https://arxiv.org/abs/1"),
        make_sample_paper(title="Second", url="https://arxiv.org/abs/2"),
    ]
    ranked = harness.rerank(papers, profile)
    assert [p.title for p in ranked] == ["First", "Second"]
    assert ranked[0].score == 9.0
    assert ranked[0].recommend_reason == "direct hit"


def test_rerank_falls_back_to_input_order_on_error(config, tmp_path, monkeypatch):
    harness, _ = _make_harness(config, tmp_path, monkeypatch, fail_on="Candidates")
    profile = ResearchProfile(topics=["LLM"], keywords=[], methods=[], summary="s")
    papers = [make_sample_paper(title="A", url="https://arxiv.org/abs/1")]
    assert harness.rerank(papers, profile) == papers


def test_rerank_disabled_returns_input(config, tmp_path, monkeypatch):
    harness, _ = _make_harness(config, tmp_path, monkeypatch)
    with open_dict(harness.config.llm.harness):
        harness.config.llm.harness.enabled = False
    harness.enabled = False
    papers = [make_sample_paper(title="A")]
    assert harness.rerank(papers, ResearchProfile([], [], [], "s")) == papers
