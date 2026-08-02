"""Tests for the single HarnessAgent (agent loop + tools + Digest output)."""

from datetime import datetime
from types import SimpleNamespace

from omegaconf import open_dict

from tests.canned_responses import make_sample_corpus, make_sample_paper
from zotero_arxiv_daily.harness import (
    Digest,
    HarnessAgent,
    _extract_json,
    _profile_prompt,
)
from zotero_arxiv_daily.protocol import CorpusPaper

# ---------------------------------------------------------------------------
# stub OpenAI client for the harness agent loop
# ---------------------------------------------------------------------------


def make_agent_client(profile_reply=None, tool_script=None, fail_on=None, evaluator_reply=None):
    """Quack like openai.OpenAI; plays a canned tool-call script.

    ``tool_script`` is a list of steps. Each step is a dict:
        {"type": "assistant", "tool_calls": [{"id":..., "function": {...}}]}
        {"type": "assistant", "content": "text"}   # plain reply
        {"type": "tool", "content": {...}}          # digest payload

    ``evaluator_reply`` is the JSON string returned for the independent
    evaluator call (fresh context, no tools) — detect by the reviewer prompt.
    """
    calls = []

    def _create(**kwargs):
        messages = kwargs.get("messages", [])
        text = str(messages)
        calls.append(kwargs)
        if fail_on and fail_on in text:
            raise RuntimeError(f"boom: {fail_on}")

        # Evaluator call: fresh context with the reviewer prompt, no tools.
        if "strict, independent reviewer" in text:
            if isinstance(evaluator_reply, list):
                content = evaluator_reply.pop(0) if evaluator_reply else (
                    '{"score": 8.5, "issues": [], "verdict": "approve"}'
                )
            else:
                content = evaluator_reply or (
                    '{"score": 8.5, "issues": [], "verdict": "approve"}'
                )
            return SimpleNamespace(
                choices=[SimpleNamespace(message=SimpleNamespace(content=content, tool_calls=None))],
                id="stub", created=0, model="gpt-5.6-luna", object="chat.completion",
            )

        # First call = profile distillation (no tools).
        if not any(isinstance(m, dict) and m.get("role") == "assistant" for m in messages) \
           or "Distill this library" in text:
            content = profile_reply or (
                '{"topics": ["LLM", "retrieval"], "keywords": ["rerank", "embedding"], '
                '"methods": ["transformer"], "summary": "Interested in LLM-based retrieval."}'
            )
            return SimpleNamespace(
                choices=[SimpleNamespace(message=SimpleNamespace(content=content, tool_calls=None))],
                id="stub", created=0, model="gpt-5.6-luna", object="chat.completion",
            )

        # Subsequent calls: play the tool script.
        if tool_script:
            step = tool_script.pop(0)
            if step.get("type") == "tool":
                return SimpleNamespace(
                    choices=[SimpleNamespace(message=SimpleNamespace(content=None, tool_calls=None))],
                    id="stub", created=0, model="gpt-5.6-luna", object="chat.completion",
                )
            tc = [
                SimpleNamespace(
                    id=f"call_{i}",
                    type="function",
                    function=SimpleNamespace(
                        name=t["function"]["name"],
                        arguments=t["function"].get("arguments", "{}"),
                    ),
                )
                for i, t in enumerate(step.get("tool_calls", []))
            ]
            return SimpleNamespace(
                choices=[SimpleNamespace(message=SimpleNamespace(content=None, tool_calls=tc))],
                id="stub", created=0, model="gpt-5.6-luna", object="chat.completion",
            )

        # Default: no tools -> submit_digest via plain content? We can't; bail with empty reply.
        return SimpleNamespace(
            choices=[SimpleNamespace(message=SimpleNamespace(content="{}", tool_calls=None))],
            id="stub", created=0, model="gpt-5.6-luna", object="chat.completion",
        )

    client = SimpleNamespace(chat=SimpleNamespace(completions=SimpleNamespace(create=_create)))
    return client, calls


def _harness_config(config, tmp_path, **overrides):
    with open_dict(config.llm):
        config.llm.harness = {"enabled": True, "top_k": 100, "full_text_budget": 10, "max_steps": 12}
    with open_dict(config.executor):
        config.executor.cache_dir = str(tmp_path)
    return config


def _make_harness(config, tmp_path, monkeypatch, **client_kwargs):
    import zotero_arxiv_daily.harness as harness_mod

    cfg = _harness_config(config, tmp_path)
    client, calls = make_agent_client(**client_kwargs)
    monkeypatch.setattr(harness_mod, "OpenAI", lambda **kw: client)
    harness = harness_mod.HarnessAgent(cfg)
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


def test_profile_prompt_truncates_long_abstracts():
    corpus = [CorpusPaper(
        title="T", abstract="x" * 5000, added_date=datetime(2026, 1, 1), paths=["a"]
    )]
    prompt = _profile_prompt(corpus, "English")
    assert len(prompt) < 1000


# ---------------------------------------------------------------------------
# profile
# ---------------------------------------------------------------------------


def test_build_profile_parses_llm_reply(config, tmp_path, monkeypatch):
    harness, _ = _make_harness(config, tmp_path, monkeypatch)
    profile = harness.build_profile(make_sample_corpus(3))
    assert profile is not None
    assert "LLM" in profile.topics
    assert "rerank" in profile.keywords


def test_build_profile_caches_by_corpus_hash(config, tmp_path, monkeypatch):
    harness, calls = _make_harness(config, tmp_path, monkeypatch)
    corpus = make_sample_corpus(3)
    harness.build_profile(corpus)
    harness.build_profile(corpus)  # second call hits cache
    profile_calls = [c for c in calls if "Distill this library" in str(c)]
    assert len(profile_calls) == 1


def test_build_profile_returns_none_on_failure(config, tmp_path, monkeypatch):
    harness, _ = _make_harness(config, tmp_path, monkeypatch, fail_on="Distill this library")
    assert harness.build_profile(make_sample_corpus(2)) is None


def test_disabled_harness_has_no_client(config, tmp_path):
    cfg = _harness_config(config, tmp_path)
    with open_dict(cfg.llm.harness):
        cfg.llm.harness.enabled = False
    harness = HarnessAgent(cfg)
    assert harness.client is None
    assert harness.generate([], []) is None


# ---------------------------------------------------------------------------
# agent loop / generate
# ---------------------------------------------------------------------------


def test_generate_runs_tools_and_submits_digest(config, tmp_path, monkeypatch):
    """Agent inspects candidates, deep-dives papers, then submits a digest."""
    tool_script = [
        # step 1: survey
        {
            "type": "assistant",
            "tool_calls": [
                {
                    "function": {
                        "name": "inspect_candidates",
                        "arguments": '{"start": 0, "count": 5}',
                    }
                }
            ],
        },
        # step 2: deep-dive paper 0
        {
            "type": "assistant",
            "tool_calls": [
                {
                    "function": {
                        "name": "inspect_paper",
                        "arguments": '{"index": 0}',
                    }
                }
            ],
        },
        # step 3: deep-dive paper 1
        {
            "type": "assistant",
            "tool_calls": [
                {
                    "function": {
                        "name": "inspect_paper",
                        "arguments": '{"index": 1}',
                    }
                }
            ],
        },
        # step 4: compare 0 vs 1 (extra tool)
        {
            "type": "assistant",
            "tool_calls": [
                {
                    "function": {
                        "name": "compare_papers",
                        "arguments": '{"index_a": 0, "index_b": 1}',
                    }
                }
            ],
        },
        # step 5: submit_digest
        {
            "type": "assistant",
            "tool_calls": [
                {
                    "function": {
                        "name": "submit_digest",
                        "arguments": json_dumps({
                            "subject": "Today's picks",
                            "intro": "Here are my recommendations.",
                            "papers": [
                                {"index": 1, "reason": "Directly extends your work on rerank."},
                                {"index": 0, "reason": "Great survey of LLM retrieval.", "tldr": "A broad overview."},
                            ],
                            "outro": "Enjoy!",
                        }),
                    }
                }
            ],
        },
    ]
    harness, _calls = _make_harness(config, tmp_path, monkeypatch, tool_script=tool_script)
    papers = [
        make_sample_paper(title="A", url="https://arxiv.org/abs/1"),
        make_sample_paper(title="B", url="https://arxiv.org/abs/2"),
    ]
    digest = harness.generate(papers, make_sample_corpus(2))
    assert digest is not None
    assert digest.subject == "Today's picks"
    assert len(digest.papers) == 2
    assert digest.papers[0].index == 1
    assert digest.papers[0].reason == "Directly extends your work on rerank."
    assert digest.papers[1].tldr == "A broad overview."
    assert digest.outro == "Enjoy!"


def test_generate_rejects_early_submit_until_enough_inspections(config, tmp_path, monkeypatch):
    """The agent cannot submit before inspecting enough papers — it is asked to
    keep working and the loop continues until it complies."""
    tool_script = [
        # step 1: try to submit immediately (no inspections yet) -> rejected
        {
            "type": "assistant",
            "tool_calls": [
                {
                    "function": {
                        "name": "submit_digest",
                        "arguments": json_dumps({
                            "subject": "Too early", "intro": "",
                            "papers": [{"index": 0, "reason": "r"}], "outro": "",
                        }),
                    }
                }
            ],
        },
        # step 2: inspect paper 0
        {
            "type": "assistant",
            "tool_calls": [
                {"function": {"name": "inspect_paper", "arguments": '{"index": 0}'}}
            ],
        },
        # step 3: inspect paper 1
        {
            "type": "assistant",
            "tool_calls": [
                {"function": {"name": "inspect_paper", "arguments": '{"index": 1}'}}
            ],
        },
        # step 4: inspect paper 2 (third inspection -> gate opens)
        {
            "type": "assistant",
            "tool_calls": [
                {"function": {"name": "inspect_paper", "arguments": '{"index": 2}'}}
            ],
        },
        # step 5: submit now succeeds
        {
            "type": "assistant",
            "tool_calls": [
                {
                    "function": {
                        "name": "submit_digest",
                        "arguments": json_dumps({
                            "subject": "Ready", "intro": "",
                            "papers": [{"index": 0, "reason": "r"}], "outro": "",
                        }),
                    }
                }
            ],
        },
    ]
    harness, _calls = _make_harness(config, tmp_path, monkeypatch, tool_script=tool_script)
    papers = [make_sample_paper(title=f"P{i}", url=f"https://arxiv.org/abs/{i}") for i in range(3)]
    digest = harness.generate(papers, make_sample_corpus(2))
    assert digest is not None
    # The final subject is "Ready", not "Too early" — proof the premature
    # submit was rejected and the loop continued until the gate opened.
    assert digest.subject == "Ready"


def test_search_candidates_filters_by_keyword(config, tmp_path, monkeypatch):
    tool_script = [
        {"type": "assistant", "tool_calls": [{"function": {"name": "search_candidates", "arguments": '{"query": "quantum"}'}}]},
        {"type": "assistant", "tool_calls": [{"function": {"name": "inspect_paper", "arguments": '{"index": 0}'}}]},
        {"type": "assistant", "tool_calls": [{"function": {"name": "inspect_paper", "arguments": '{"index": 1}'}}]},
        {
            "type": "assistant",
            "tool_calls": [
                {"function": {"name": "submit_digest", "arguments": json_dumps({
                    "subject": "s", "intro": "",
                    "papers": [{"index": 0, "reason": "r"}], "outro": "",
                })}},
            ],
        },
    ]
    harness, _calls = _make_harness(config, tmp_path, monkeypatch, tool_script=tool_script)
    papers = [
        make_sample_paper(title="Quantum dynamics", url="https://arxiv.org/abs/1"),
        make_sample_paper(title="Classical mechanics", url="https://arxiv.org/abs/2"),
    ]
    digest = harness.generate(papers, make_sample_corpus(1))
    assert digest is not None
    assert digest.subject == "s"


def test_evaluator_approves_draft(config, tmp_path, monkeypatch):
    """An approving evaluator returns the draft unchanged (no revision round)."""
    tool_script = _submit_script()
    harness, _calls = _make_harness(
        config, tmp_path, monkeypatch,
        tool_script=tool_script,
        evaluator_reply='{"score": 9.0, "issues": [], "verdict": "approve"}',
    )
    papers = [make_sample_paper(title=f"P{i}") for i in range(3)]
    digest = harness.generate(papers, make_sample_corpus(1))
    assert digest is not None
    assert digest.subject == "s"


def test_evaluator_revise_triggers_revision_round(config, tmp_path, monkeypatch):
    """A 'revise' verdict feeds issues back and re-runs the generator; the
    revised draft wins."""
    tool_script = _submit_script() + _submit_script(subject="revised")
    harness, _calls = _make_harness(
        config, tmp_path, monkeypatch,
        tool_script=tool_script,
        evaluator_reply=[
            '{"score": 4.0, "issues": [{"severity": "high", '
            '"problem": "Picks do not match profile", "suggestion": "Pick quantum papers"}], '
            '"verdict": "revise"}',
            '{"score": 9.0, "issues": [], "verdict": "approve"}',
        ],
    )
    papers = [make_sample_paper(title=f"P{i}") for i in range(3)]
    digest = harness.generate(papers, make_sample_corpus(1))
    assert digest is not None
    assert digest.subject == "revised"


def test_evaluator_failure_keeps_draft(config, tmp_path, monkeypatch):
    """Evaluator exceptions degrade gracefully: keep the generator's draft."""
    tool_script = _submit_script()
    harness, _calls = _make_harness(
        config, tmp_path, monkeypatch,
        tool_script=tool_script,
        fail_on="strict, independent reviewer",
    )
    papers = [make_sample_paper(title=f"P{i}") for i in range(3)]
    digest = harness.generate(papers, make_sample_corpus(1))
    assert digest is not None
    assert digest.subject == "s"


def test_evaluator_disabled_skips_review(config, tmp_path, monkeypatch):
    tool_script = _submit_script()
    harness, _calls = _make_harness(config, tmp_path, monkeypatch, tool_script=tool_script)
    with open_dict(harness.config.llm.harness):
        harness.config.llm.harness.evaluator_enabled = False
    papers = [make_sample_paper(title=f"P{i}") for i in range(3)]
    digest = harness.generate(papers, make_sample_corpus(1))
    assert digest is not None
    assert digest.subject == "s"


def _submit_script(subject="s"):
    """A minimal generator script that inspects 3 papers then submits."""
    return [
        {"type": "assistant", "tool_calls": [{"function": {"name": "inspect_paper", "arguments": '{"index": 0}'}}]},
        {"type": "assistant", "tool_calls": [{"function": {"name": "inspect_paper", "arguments": '{"index": 1}'}}]},
        {"type": "assistant", "tool_calls": [{"function": {"name": "inspect_paper", "arguments": '{"index": 2}'}}]},
        {
            "type": "assistant",
            "tool_calls": [
                {"function": {"name": "submit_digest", "arguments": json_dumps({
                    "subject": subject, "intro": "",
                    "papers": [{"index": 0, "reason": "r"}], "outro": "",
                })}},
            ],
        },
    ]


def test_generate_returns_none_when_disabled(config, tmp_path, monkeypatch):
    harness, _ = _make_harness(config, tmp_path, monkeypatch)
    with open_dict(harness.config.llm.harness):
        harness.config.llm.harness.enabled = False
    harness.client = None
    assert harness.generate([make_sample_paper()], make_sample_corpus(1)) is None


def test_generate_returns_none_when_profile_fails(config, tmp_path, monkeypatch):
    harness, _ = _make_harness(config, tmp_path, monkeypatch, fail_on="Distill this library")
    assert harness.generate([make_sample_paper()], make_sample_corpus(1)) is None


def test_describe_candidates_out_of_range():
    agent = object.__new__(HarnessAgent)
    out = HarnessAgent._describe_candidates(agent, [], 0, 5)
    assert out == "No candidates."


def test_describe_paper_out_of_range():
    agent = object.__new__(HarnessAgent)
    out = HarnessAgent._describe_paper(agent, [], 7)
    assert "Index out of range" in out


def test_digest_from_args_ignores_bad_indices():
    object.__new__(HarnessAgent)
    d = HarnessAgent._digest_from_args(
        {"subject": "s", "intro": "i", "papers": [{"index": "x", "reason": "r"}], "outro": "o"},
        5,
    )
    assert d.papers[0].index == -1
    assert d.papers[0].reason == "r"


def test_fallback_digest_uses_embedding_order(config, tmp_path):
    cfg = _harness_config(config, tmp_path)
    agent = HarnessAgent(cfg)
    papers = [
        make_sample_paper(title="First", url="https://arxiv.org/abs/1", score=9.0),
        make_sample_paper(title="Second", url="https://arxiv.org/abs/2", score=3.0),
    ]
    d = agent.fallback_digest(papers, max_papers=10)
    assert isinstance(d, Digest)
    assert len(d.papers) == 2
    assert d.papers[0].index == 0
    assert d.papers[1].index == 1


def json_dumps(obj):
    import json
    return json.dumps(obj)
