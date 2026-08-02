"""Tests for zotero_arxiv_daily.protocol: Paper / CorpusPaper dataclasses.

The legacy TLDR/affiliations generation methods were removed when the pipeline
moved to the single HarnessAgent — these tests now cover the data model and
its defaults.
"""

from datetime import datetime

from tests.canned_responses import make_sample_paper
from zotero_arxiv_daily.protocol import CorpusPaper, Paper


def test_paper_defaults():
    p = Paper(
        source="arxiv",
        title="T",
        authors=["A"],
        abstract="abs",
        url="https://arxiv.org/abs/1",
    )
    assert p.pdf_url is None
    assert p.full_text is None
    assert p.tldr is None
    assert p.affiliations is None
    assert p.score is None
    assert p.source_url is None
    assert p.recommend_reason is None


def test_paper_all_fields():
    p = Paper(
        source="arxiv",
        title="T",
        authors=["A", "B"],
        abstract="abs",
        url="https://arxiv.org/abs/1",
        pdf_url="https://arxiv.org/pdf/1",
        full_text="full",
        tldr="one-liner",
        affiliations=["MIT"],
        score=8.5,
        source_url="https://arxiv.org/list/cs.AI/recent",
        recommend_reason="direct hit",
    )
    assert p.score == 8.5
    assert p.tldr == "one-liner"
    assert p.affiliations == ["MIT"]
    assert p.recommend_reason == "direct hit"


def test_sample_paper_roundtrip():
    p = make_sample_paper()
    assert p.title == "Sample Paper Title"
    assert p.source == "arxiv"
    assert p.url.startswith("https://arxiv.org/abs/")


def test_corpus_paper_fields():
    c = CorpusPaper(
        title="T",
        abstract="abs",
        added_date=datetime(2026, 1, 1),
        paths=["2026/survey", "reading"],
    )
    assert c.paths == ["2026/survey", "reading"]
    assert c.added_date.year == 2026
