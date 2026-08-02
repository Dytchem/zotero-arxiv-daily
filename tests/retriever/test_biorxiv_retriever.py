"""Tests for BiorxivRetriever."""

import pytest
from omegaconf import open_dict

from tests.canned_responses import SAMPLE_BIORXIV_API_RESPONSE
from zotero_arxiv_daily.retriever.biorxiv_retriever import BiorxivRetriever


def test_biorxiv_retrieve(config, mock_biorxiv_api, monkeypatch):
    with open_dict(config.source):
        config.source.biorxiv = {"category": ["bioinformatics"]}
    retriever = BiorxivRetriever(config)
    papers = retriever.retrieve_papers()
    # Only latest date + matching category
    assert len(papers) == 1
    assert papers[0].title == "A biorxiv paper"


def test_biorxiv_empty_response(config, monkeypatch):
    from types import SimpleNamespace

    import requests

    empty = {"messages": [{"status": "ok"}], "collection": []}

    def _patched(url, **kw):
        resp = SimpleNamespace(status_code=200, raise_for_status=lambda: None)
        resp.json = lambda: empty
        return resp

    monkeypatch.setattr(requests, "get", _patched)

    with open_dict(config.source):
        config.source.biorxiv = {"category": ["bioinformatics"]}
    retriever = BiorxivRetriever(config)
    papers = retriever.retrieve_papers()
    assert papers == []


def test_biorxiv_convert_to_paper(config):
    with open_dict(config.source):
        config.source.biorxiv = {"category": ["bioinformatics"]}
    retriever = BiorxivRetriever(config)
    raw = SAMPLE_BIORXIV_API_RESPONSE["collection"][0]
    paper = retriever.convert_to_paper(raw)
    assert paper.title == "A biorxiv paper"
    assert paper.source == "biorxiv"
    assert "biorxiv.org" in paper.pdf_url
    assert paper.authors == ["Smith, J.", "Doe, A.", "Lee, K."]


def test_biorxiv_convert_to_paper_url_points_to_abs_page(config):
    """url should be the abstract page; pdf_url the .full.pdf download."""
    from omegaconf import open_dict

    with open_dict(config.source):
        config.source.biorxiv = {"category": ["bioinformatics"]}
    retriever = BiorxivRetriever(config)
    raw = SAMPLE_BIORXIV_API_RESPONSE["collection"][0]
    paper = retriever.convert_to_paper(raw)
    assert paper.url == "https://www.biorxiv.org/content/10.1101/2026.03.01.000001v1"
    assert paper.pdf_url == paper.url + ".full.pdf"
    assert paper.url != paper.pdf_url


def test_biorxiv_requires_category(config):
    with open_dict(config.source):
        config.source.biorxiv = {"category": None}
    with pytest.raises(ValueError, match="category must be specified"):
        BiorxivRetriever(config)


def test_fetch_full_text_uses_pdf_extraction(config, monkeypatch):
    """bioRxiv fetch_full_text delegates to the shared PDF extraction."""
    from tests.canned_responses import make_sample_paper

    called = []
    monkeypatch.setattr(
        "zotero_arxiv_daily.retriever.arxiv_retriever.extract_text_from_pdf",
        lambda paper: (called.append(paper), "pdf text")[1],
    )
    with open_dict(config.source):
        config.source.biorxiv = {"category": ["bioinformatics"]}
    retriever = BiorxivRetriever(config)
    paper = make_sample_paper(pdf_url="https://www.biorxiv.org/content/10.1101/123v1.full.pdf")
    assert retriever.fetch_full_text(paper) == "pdf text"
    assert called == [paper]
