"""Tests for zotero_arxiv_daily.construct_email: Digest -> safe HTML rendering."""

from tests.canned_responses import make_sample_paper
from zotero_arxiv_daily.construct_email import (
    _clean_link,
    _mathify,
    get_empty_html,
    render_email,
    render_fallback,
)
from zotero_arxiv_daily.harness import Digest, DigestPaper


def _paper(index=0, **kw):
    defaults = {
        "title": "Sample Paper Title",
        "authors": ["Author A", "Author B"],
        "abstract": "An abstract.",
        "url": "https://arxiv.org/abs/2401.00001",
        "pdf_url": "https://arxiv.org/pdf/2401.00001",
        "source": "arxiv",
        "score": 7.5,
    }
    defaults.update(kw)
    return make_sample_paper(**defaults)


def test_render_email_with_digest():
    digest = Digest(
        subject="Today's picks",
        intro="Here are my recommendations.",
        papers=[DigestPaper(index=0, reason="Direct hit", tldr="A great paper.")],
        outro="Enjoy!",
    )
    html = render_email(digest, originals=[_paper(0)])
    # subject goes into <title>; apostrophe is HTML-escaped by _safe()
    assert "Today&#x27;s picks" in html or "Today&apos;s picks" in html or "picks" in html
    assert "Here are my recommendations." in html
    assert "Sample Paper Title" in html
    assert "Direct hit" in html
    assert "Enjoy!" in html


def test_render_email_shows_relevance_score():
    """The Relevance badge shows the real embedding score, not n/a."""
    digest = Digest(subject="s", intro="", papers=[DigestPaper(index=0, reason="r")], outro="")
    html = render_email(digest, originals=[_paper(0, score=7.5)])
    assert "Relevance: 7.5" in html
    assert "Relevance: n/a" not in html


def test_render_email_relevance_none_is_defensive():
    digest = Digest(subject="s", intro="", papers=[DigestPaper(index=0, reason="r")], outro="")
    html = render_email(digest, originals=[_paper(0, score=None)])
    assert "Relevance: n/a" in html


def test_render_email_note_only_one():
    """When both reason and tldr are present, only the Why note is shown."""
    digest = Digest(
        subject="s", intro="",
        papers=[DigestPaper(index=0, reason="Direct hit", tldr="A great paper.")],
        outro="",
    )
    html = render_email(digest, originals=[_paper(0)])
    assert "Direct hit" in html
    assert "A great paper." not in html


def test_render_email_others_section_lists_unpicked_candidates():
    """Candidates the agent did not pick still appear at the bottom, compact."""
    digest = Digest(subject="s", intro="", papers=[DigestPaper(index=0, reason="r")], outro="")
    originals = [
        _paper(0, title="Picked Paper"),
        _paper(1, title="Not Picked One"),
        _paper(2, title="Not Picked Two"),
    ]
    html = render_email(digest, originals=originals)
    assert "Picked Paper" in html
    assert "Other candidates" in html
    assert "Not Picked One" in html
    assert "Not Picked Two" in html


def test_render_email_chinese_summary_and_others():
    """Chinese language config localises the summary and other-candidates heading."""
    digest = Digest(subject="s", intro="", papers=[DigestPaper(index=0, reason="r")], outro="")
    originals = [_paper(0), _paper(1)]
    html = render_email(digest, originals=originals, language="Chinese")
    assert "精选 1 篇论文" in html
    assert "其他候选" in html
    assert "Other candidates" not in html


def test_render_email_date_not_duplicated_when_subject_has_it():
    """When the agent's subject already contains a date, the summary line does
    not repeat it (avoids '2026-08-02 ... · 2026-08-02 · 精选 N 篇论文')."""
    digest = Digest(subject="Quantum digest | 2026-08-02", intro="", papers=[DigestPaper(index=0, reason="r")], outro="")
    html = render_email(digest, originals=[_paper(0)], language="Chinese")
    assert "精选 1 篇论文" in html
    # the summary line must not repeat the date the subject already carries
    assert "(Sunday) · 精选" not in html
    assert "2026-08-02 (Sunday) · 精选" not in html


def test_render_email_date_added_when_subject_lacks_it():
    digest = Digest(subject="Quantum digest", intro="", papers=[DigestPaper(index=0, reason="r")], outro="")
    html = render_email(digest, originals=[_paper(0)], language="Chinese")
    assert "2026-08-02" in html  # date appears in the summary line
    assert "精选 1 篇论文" in html


def test_render_fallback_chinese_summary():
    html = render_fallback([_paper(0)], language="Chinese")
    assert "共 1 篇论文" in html


def test_render_email_preheader_and_footer_localised():
    digest = Digest(subject="s", intro="", papers=[DigestPaper(index=0, reason="r")], outro="")
    html = render_email(digest, originals=[_paper(0)], language="Chinese")
    assert "今日精选 1 篇论文" in html
    assert "GitHub Actions" in html


def test_render_email_picks_preserve_agent_order():
    """Recommended cards render in the agent's editorial order, not score order.

    The agent is the expert: the order of its papers array is the display order,
    so a lower-scoring paper listed first must appear first in the email.
    """
    digest = Digest(
        subject="s", intro="",
        papers=[
            DigestPaper(index=0, reason="lead pick"),
            DigestPaper(index=1, reason="second pick"),
        ],
        outro="",
    )
    originals = [_paper(0, title="Low Score Paper", score=4.0), _paper(1, title="High Score Paper", score=9.0)]
    html = render_email(digest, originals=originals)
    # Agent listed index 0 (score 4.0) first — it must stay first.
    assert html.index("Low Score Paper") < html.index("High Score Paper")


def test_render_email_others_last_item_no_border():
    digest = Digest(subject="s", intro="", papers=[DigestPaper(index=0, reason="r")], outro="")
    originals = [_paper(0), _paper(1), _paper(2)]
    html = render_email(digest, originals=originals)
    # only 1 border-bottom in the others list (between the two unpicked items)
    assert html.count("border-bottom:1px solid #f3f4f6;") == 1


def test_render_email_empty_digest():
    digest = Digest(subject="x", intro="", papers=[], outro="")
    html = render_email(digest, originals=[])
    assert "No Papers Today" in html


def test_render_email_none_digest_falls_back():
    html = render_email(None, originals=[_paper(0)])
    assert "Sample Paper Title" in html


def test_render_fallback_empty():
    html = render_fallback([])
    assert "No Papers Today" in html


def test_render_fallback_with_papers():
    p = _paper(0, tldr="ok", score=8.0)
    html = render_fallback([p])
    assert "Sample Paper Title" in html
    assert "ok" in html


def test_render_email_author_truncation():
    authors = [f"Author {i}" for i in range(10)]
    digest = Digest(subject="s", intro="", papers=[DigestPaper(index=0, reason="r")], outro="")
    html = render_email(digest, originals=[_paper(0, authors=authors)])
    # Render layer just joins the first 5 authors with ", " (no ellipsis marker).
    assert "Author 0" in html
    assert "Author 1" in html
    assert "Author 2" in html
    assert "Author 3" in html
    assert "Author 4" in html
    assert "Author 5" not in html
    assert "Author 9" not in html


def test_render_email_source_badges():
    from zotero_arxiv_daily.protocol import Paper

    biorxiv = Paper(
        source="biorxiv", title="Bio Paper", authors=["A"], abstract="x",
        url="https://www.biorxiv.org/content/1v1", pdf_url="https://www.biorxiv.org/content/1v1.full.pdf",
    )
    digest = Digest(subject="s", intro="", papers=[DigestPaper(index=0, reason="r")], outro="")
    html = render_email(digest, originals=[biorxiv])
    assert ">bioRxiv</span>" in html


def test_render_email_escapes_html():
    """The render layer must never trust LLM text as markup."""
    digest = Digest(
        subject="<script>alert(1)</script>",
        intro="<b>bold</b>",
        papers=[DigestPaper(index=0, reason="<img src=x onerror=alert(1)>")],
        outro="",
    )
    html = render_email(digest, originals=[_paper(0)])
    assert "<script>alert(1)</script>" not in html
    assert "&lt;script&gt;" in html
    assert "<b>bold</b>" not in html
    assert "&lt;b&gt;bold&lt;/b&gt;" in html
    assert "&lt;img src=x" in html


def test_render_email_mathify_title():
    """Inline LaTeX is converted to readable text, not left as raw \\alpha."""
    digest = Digest(
        subject="s", intro="",
        papers=[DigestPaper(index=0, reason="r")],
        outro="",
    )
    paper = _paper(0, title="Diffusion models and $\\alpha$-stable noise")
    html = render_email(digest, originals=[paper])
    assert "α"
    assert "\\alpha" not in html


def test_render_email_safe_links():
    """Only http(s) links survive; dangerous URLs are dropped."""
    digest = Digest(subject="s", intro="", papers=[DigestPaper(index=0, reason="r")], outro="")
    paper = _paper(0, url="javascript:alert(1)", pdf_url="https://arxiv.org/pdf/1")
    html = render_email(digest, originals=[paper])
    assert "javascript:" not in html
    assert "https://arxiv.org/pdf/1" in html


def test_clean_link():
    assert _clean_link("https://arxiv.org/abs/1") == "https://arxiv.org/abs/1"
    assert _clean_link('"https://x.com/a"') == "https://x.com/a"
    assert _clean_link("javascript:alert(1)") is None
    assert _clean_link("http://x.com/a b") is None
    assert _clean_link(None) is None


def test_mathify():
    assert _mathify("$\\alpha + \\beta$") == "α + β"
    assert _mathify("plain text") == "plain text"
    assert _mathify("$\\frac{1}{2}$") == "/12"


def test_strip_markdown():
    from zotero_arxiv_daily.construct_email import _strip_markdown

    # LLM 泄漏的 markdown 语法 → 干净文本
    assert _strip_markdown("**Contextual relevance — [Quantum model reduction](https://arxiv.org/abs/1)** develops X") == (
        "Contextual relevance — Quantum model reduction develops X"
    )
    assert _strip_markdown("`code` and **bold** and _italic_") == "code and bold and italic"
    assert _strip_markdown("plain text") == "plain text"
    # 行首的 markdown 标题头去掉; 文本中间的 # 保留 (如 C#)
    assert _strip_markdown("# Heading") == "Heading"
    assert "#" in _strip_markdown("C# is great")


def test_get_empty_html():
    assert "No Papers Today" in get_empty_html()


def test_render_email_no_pdf_no_url():
    digest = Digest(subject="s", intro="", papers=[DigestPaper(index=0, reason="r")], outro="")
    paper = _paper(0, pdf_url=None, url=None)
    html = render_email(digest, originals=[paper])
    assert "PDF" not in html
    assert "Abstract" not in html
