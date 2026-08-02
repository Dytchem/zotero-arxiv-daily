"""Tests for zotero_arxiv_daily.construct_email: render_email, get_block_html."""

from tests.canned_responses import make_sample_paper
from zotero_arxiv_daily.construct_email import get_block_html, get_empty_html, render_email


def test_render_email_with_papers():
    papers = [make_sample_paper(score=7.5, tldr="A great paper.", affiliations=["MIT"])]
    html = render_email(papers)
    assert "Sample Paper Title" in html
    assert "A great paper." in html
    assert "MIT" in html


def test_render_email_empty_list():
    html = render_email([])
    assert "No Papers Today" in html


def test_render_email_author_truncation():
    authors = [f"Author {i}" for i in range(10)]
    paper = make_sample_paper(authors=authors, score=7.0, tldr="ok")
    html = render_email([paper])
    assert "Author 0" in html
    assert "Author 1" in html
    assert "Author 2" in html
    assert "..." in html
    assert "Author 8" in html
    assert "Author 9" in html
    # Middle authors should be truncated
    assert "Author 5" not in html


def test_render_email_affiliation_truncation():
    affiliations = [f"Uni {i}" for i in range(8)]
    paper = make_sample_paper(affiliations=affiliations, score=7.0, tldr="ok")
    html = render_email([paper])
    assert "Uni 0" in html
    assert "Uni 4" in html
    assert "..." in html
    assert "Uni 7" not in html


def test_render_email_no_affiliations():
    paper = make_sample_paper(affiliations=None, score=7.0, tldr="ok")
    html = render_email([paper])
    assert "Unknown Affiliation" in html


def test_get_block_html_contains_all_fields():
    html = get_block_html("Title", "Auth", "3.5", "Summary", "http://pdf.url", "MIT")
    assert "Title" in html
    assert "Auth" in html
    assert "3.5" in html
    assert "Summary" in html
    assert "http://pdf.url" in html
    assert "MIT" in html


def test_get_block_html_with_url_and_source():
    html = get_block_html(
        "Title", "Auth", "3.5", "Summary", "http://pdf.url", "MIT",
        url="http://arxiv.org/abs/1", source="arxiv",
    )
    assert 'href="http://arxiv.org/abs/1"' in html
    assert ">arXiv</span>" in html
    assert "Abstract" in html


def test_get_block_html_no_pdf_no_url():
    html = get_block_html("Title", "Auth", "3.5", "Summary", None, None)
    assert "PDF" not in html
    assert "Abstract" not in html


def test_render_email_summary_header():
    papers = [make_sample_paper(score=7.0, tldr="ok"), make_sample_paper(title="Two", score=6.0, tldr="ok2")]
    html = render_email(papers)
    assert "2 papers recommended for you" in html


def test_render_email_single_paper_singular():
    html = render_email([make_sample_paper(score=7.0, tldr="ok")])
    assert "1 paper recommended for you" in html


def test_render_email_source_badges():
    from zotero_arxiv_daily.protocol import Paper

    biorxiv = Paper(
        source="biorxiv", title="Bio Paper", authors=["A"], abstract="x",
        url="https://www.biorxiv.org/content/1v1", pdf_url="https://www.biorxiv.org/content/1v1.full.pdf",
        score=6.0, tldr="bio",
    )
    html = render_email([biorxiv])
    assert ">bioRxiv</span>" in html


def test_get_empty_html():
    html = get_empty_html()
    assert "No Papers Today" in html
