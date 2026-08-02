"""Safe HTML rendering of the agent-produced Digest.

This module is a *pure rendering layer*. It takes the agent's structured
``Digest`` (subject / intro / per-paper cards / outro) and turns it into
polished HTML email. It deliberately does NOT do any editorial work and does
NOT trust raw markup from the LLM:

- every text field is HTML-escaped (& < > " '),
- LaTeX math is converted to a readable plain-text form,
- links are validated (only http/https, stripped of dangerous chars),
- ``score``/``Relevance`` is always rendered defensively (handles None).

The agent never writes HTML directly — it writes JSON; this layer owns markup.
"""

from __future__ import annotations

import html
import re

from .harness import Digest, _today_str
from .protocol import Paper

framework = """
<!DOCTYPE HTML>
<html>
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1.0">
<style>
  @media (max-width:480px){
    .card{padding:14px 14px !important;}
    .body-pad{padding:16px 12px !important;}
    .btn{display:block !important;margin:6px 0 0 0 !important;text-align:center;}
    .hdr{padding:16px 16px !important;}
  }
</style>
</head>
<body style="margin:0;padding:0;background-color:#f4f5f7;font-family:-apple-system,BlinkMacSystemFont,'Segoe UI',Roboto,Arial,'PingFang SC','Hiragino Sans GB','Microsoft YaHei','Noto Sans CJK SC',sans-serif;">
<div style="display:none;max-height:0;overflow:hidden;mso-hide:all;font-size:1px;color:#f4f5f7;line-height:1px;">__PREHEADER__</div>
<div style="max-width:680px;margin:0 auto;padding:24px 16px;">
  <div style="background:#ffffff;border-radius:14px;box-shadow:0 2px 12px rgba(0,0,0,0.06);overflow:hidden;">
    <div class="hdr" style="background-color:#2563eb;background-image:linear-gradient(135deg,#2563eb,#7c3aed);padding:22px 28px;">
      <div style="color:#ffffff;font-size:22px;font-weight:700;">__TITLE__</div>
      <div style="color:rgba(255,255,255,0.9);font-size:13px;margin-top:4px;">__SUMMARY__</div>
    </div>
    <div class="body-pad" style="padding:24px 28px;">
      __INTRO__
      __CONTENT__
      __OUTRO__
    </div>
  </div>
  <div style="text-align:center;color:#6b7280;font-size:12px;padding:16px 8px 8px;">
    __FOOTER__
  </div>
</div>
</body>
</html>
"""

_SOURCE_LABELS = {
    "arxiv": "arXiv",
    "biorxiv": "bioRxiv",
    "medrxiv": "medRxiv",
}

# Simple LaTeX command -> unicode substitutions for common math.
_LATEX_SUBS = {
    r"\alpha": "α", r"\beta": "β", r"\gamma": "γ", r"\delta": "δ",
    r"\epsilon": "ε", r"\theta": "θ", r"\lambda": "λ", r"\mu": "μ",
    r"\sigma": "σ", r"\phi": "φ", r"\omega": "ω", r"\pi": "π",
    r"\infty": "∞", r"\times": "×", r"\cdot": "·", r"\leq": "≤",
    r"\geq": "≥", r"\neq": "≠", r"\approx": "≈", r"\pm": "±",
    r"\sum": "∑", r"\int": "∫", r"\sqrt": "√", r"\frac": "/",
}


def _safe(text: str | None) -> str:
    """HTML-escape a text field, keeping the output safe to inline."""
    return html.escape(text or "", quote=True)


def _mathify(text: str) -> str:
    """Convert common inline LaTeX to readable unicode, keeping the rest plain.

    Only replaces $...$ spans and known commands; unknown commands are kept
    as-is (escaped) so nothing breaks the HTML and no raw backslashes leak
    into the rendered subject/title text in a confusing way. The goal is
    *readability*, not full LaTeX rendering.
    """
    if not text:
        return text

    def _sub(match: re.Match) -> str:
        inner = match.group(1) or match.group(2) or ""
        out = inner
        for cmd, uni in _LATEX_SUBS.items():
            out = out.replace(cmd, uni)
        # collapse stray braces
        out = out.replace("{", "").replace("}", "")
        out = out.replace("\\", "")
        return out

    # $...$ or \( ... \)
    out = re.sub(r"\$(.+?)\$", lambda m: _sub(m), text)
    out = re.sub(r"\\\((.+?)\\\)", lambda m: _sub(m), out)
    return out


def _clean_link(url: str | None) -> str | None:
    """Return a safe http(s) URL; None otherwise (also strips quotes/brackets)."""
    if not url:
        return None
    url = url.strip().strip('"').strip("'").strip("<>")
    if not re.match(r"^https?://", url, flags=re.IGNORECASE):
        return None
    if re.search(r"[\s\"'<>]", url):
        return None
    return url


def get_empty_html() -> str:
    return """
    <div style="text-align:center;padding:40px 20px;">
      <div style="font-size:20px;font-weight:700;color:#111827;">No Papers Today. Take a Rest!</div>
      <div style="font-size:14px;color:#6b7280;margin-top:8px;">Your Zotero library had no new matching papers today.</div>
    </div>
    """


def _rate_html(score: float | None, language: str = "English") -> str:
    label = "相关度" if language.lower().startswith("chinese") else "Relevance"
    if score is None:
        return f'<span style="display:inline-block;background:#eef2ff;color:#4f46e5;font-size:12px;font-weight:600;padding:3px 10px;border-radius:999px;">{label}: n/a</span>'
    try:
        rate = round(float(score), 1)
    except (TypeError, ValueError):
        rate = "n/a"
    return (
        f'<span style="display:inline-block;background:#eef2ff;color:#4f46e5;'
        f'font-size:12px;font-weight:600;padding:3px 10px;border-radius:999px;">'
        f'{label}: {rate}</span>'
    )


def _get_block_html(title, authors, reason, tldr, url, pdf_url, source, score=None, language: str = "English") -> str:
    title_text = _mathify(title)
    title_html = title_text
    clean_url = _clean_link(url)
    if clean_url:
        title_html = f'<a href="{clean_url}" style="color:#111827;text-decoration:none;">{title_text}</a>'

    badge_html = ""
    if source:
        label = _SOURCE_LABELS.get(source, source)
        badge_html = (
            f'<span style="display:inline-block;background:#eef2ff;color:#4f46e5;'
            f'font-size:11px;font-weight:700;padding:2px 10px;border-radius:999px;'
            f'margin-bottom:6px;">{_safe(label)}</span>'
        )

    # One note per card: prefer the agent's Why (recommendation reason);
    # fall back to the TLDR when no reason is given. Never show both.
    why_label = "推荐理由" if language.lower().startswith("chinese") else "Why"
    tldr_label = "一句话" if language.lower().startswith("chinese") else "TLDR"
    note_html = ""
    if reason:
        note_html = (
            f'<div style="margin-top:10px;font-size:13px;color:#6d28d9;'
            f'background:#faf5ff;border-left:4px solid #a855f7;padding:8px 12px;'
            f'border-radius:6px;"><strong>{why_label}:</strong> {_safe(reason)}</div>'
        )
    elif tldr:
        note_html = (
            f'<div style="margin-top:12px;padding:10px 14px;border-left:4px solid #2563eb;'
            f'background:#f8fafc;border-radius:6px;font-size:14px;color:#374151;line-height:1.55;">'
            f'<strong>{tldr_label}:</strong> {_safe(tldr)}</div>'
        )

    buttons = ""
    clean_pdf = _clean_link(pdf_url)
    clean_url = _clean_link(url)
    if clean_pdf:
        buttons += (
            f'<td style="width:50%;padding-right:4px;"><a href="{clean_pdf}" class="btn" style="display:block;width:100%;box-sizing:border-box;text-align:center;text-decoration:none;font-size:13px;'
            f'font-weight:700;color:#ffffff;background-color:#2563eb;background-image:linear-gradient(135deg,#2563eb,#4f46e5);'
            f'padding:9px 0;border-radius:8px;">PDF</a></td>'
        )
    if clean_url:
        buttons += (
            f'<td style="width:50%;padding-left:4px;"><a href="{clean_url}" class="btn" style="display:block;width:100%;box-sizing:border-box;text-align:center;text-decoration:none;font-size:13px;'
            f'font-weight:700;color:#2563eb;border:1px solid #2563eb;padding:8px 0;'
            f'border-radius:8px;">Abstract</a></td>'
        )

    if buttons:
        buttons = (
            f'<table cellpadding="0" cellspacing="0" border="0" style="border-collapse:collapse;width:100%;table-layout:fixed;">'
            f'<tr>{buttons}</tr></table>'
        )

    return f"""
    <div class="card" style="border:1px solid #e5e7eb;border-radius:10px;padding:18px 20px;margin-bottom:16px;background:#ffffff;">
      {badge_html}
      <div style="font-size:17px;font-weight:700;color:#111827;line-height:1.4;">{title_html}</div>
      <div style="font-size:13px;color:#6b7280;margin-top:8px;line-height:1.5;">{_safe(authors)}</div>
      <div style="margin-top:10px;">{_rate_html(score, language)}</div>
      {note_html}
      <div style="margin-top:14px;">{buttons}</div>
    </div>
    """


def _preheader(digest: Digest, language: str) -> str:
    """Inbox-preview text: a short, skimmable teaser of the digest."""
    n = len(digest.papers)
    if language.lower().startswith("chinese"):
        head = f"今日精选 {n} 篇论文"
    else:
        head = f"{n} paper{'s' if n != 1 else ''} recommended today"
    # Use the intro's first sentence (or the subject) as the teaser — never
    # stitch together per-paper fragments.
    intro = (digest.intro or "").strip()
    first_sentence = re.split(r"[。！？!?]\s*", intro)[0] if intro else ""
    tail = first_sentence if first_sentence else (digest.subject or "")
    return _safe((head + " · " + tail)[:150])


def _footer_html(language: str) -> str:
    if language.lower().startswith("chinese"):
        return (
            "不再接收？在 GitHub Actions 设置的 Secrets 中移除你的邮箱即可退订。"
            "<br>本邮件由 Zotero-arXiv-Daily 自动生成。"
        )
    return "To unsubscribe, remove your email in your GitHub Actions settings."


def _others_block_html(papers: list[Paper], language: str = "English") -> str:
    """Compact list of candidates the agent did not pick (bottom of the email)."""
    heading = "其他候选" if language.lower().startswith("chinese") else "Other candidates"
    rows = ""
    for i, p in enumerate(papers):
        title_text = _safe(_mathify(p.title))
        clean_url = _clean_link(p.url)
        link = f'<a href="{clean_url}" style="color:#111827;text-decoration:none;">{title_text}</a>' if clean_url else title_text
        badge = ""
        if p.source:
            label = _SOURCE_LABELS.get(p.source, p.source)
            badge = f'<span style="background:#eef2ff;color:#4f46e5;font-size:10px;font-weight:700;padding:1px 8px;border-radius:999px;margin-left:8px;">{_safe(label)}</span>'
        border = "border-bottom:1px solid #f3f4f6;" if i < len(papers) - 1 else ""
        rows += (
            f'<div style="padding:8px 0;{border}font-size:13px;line-height:1.4;">'
            f'{link}{badge}</div>'
        )
    return (
        f'<div style="margin-top:24px;padding-top:14px;border-top:2px solid #e5e7eb;">'
        f'<div style="font-size:13px;font-weight:700;color:#6b7280;margin-bottom:4px;">{heading}</div>'
        f'{rows}</div>'
    )


def render_email(digest: Digest | None, originals: list[Paper] | None = None, language: str = "English") -> str:
    """Render a Digest (or a plain fallback list) to HTML email.

    ``digest`` is the agent's structured output; when it is None we render the
    ``originals`` list as simple embedding-ordered cards (the graceful fallback).
    """
    if digest is None:
        return render_fallback(originals or [])

    title = _safe(_mathify(digest.subject)) or "Daily paper digest"
    today = _today_str()
    # Avoid a duplicated date: the agent's subject often already carries one
    # (e.g. "... | 2026-08-02"). Only add the date to the summary line when
    # the subject does not contain it.
    subject_has_date = bool(re.search(r"\d{4}[-/]\d{1,2}[-/]\d{1,2}|\d{1,2}[-/]\d{1,2}", title))
    if language.lower().startswith("chinese"):
        summary = (f"{today} · " if not subject_has_date else "") + f"精选 {len(digest.papers)} 篇论文"
    else:
        n_label = f"{len(digest.papers)} paper{'s' if len(digest.papers) != 1 else ''} recommended"
        summary = (f"{today} · " if not subject_has_date else "") + n_label
    intro = _safe(_mathify(digest.intro))
    outro = _safe(_mathify(digest.outro))

    cards = ""
    selected_indices: set[int] = set()
    if digest.papers:
        # Map candidate index -> original Paper so we can pull authors/url/pdf/source.
        originals_by_index = dict(enumerate(originals or []))
        # Render picks in descending relevance order (agent order is editorial,
        # but readers expect the strongest match first).
        ordered = sorted(
            digest.papers,
            key=lambda dp: ((originals_by_index.get(dp.index).score if originals_by_index.get(dp.index) else None) or 0.0),
            reverse=True,
        )
        for dp in ordered:
            paper = originals_by_index.get(dp.index)
            title_text = paper.title if paper else f"Paper {dp.index}"
            authors = ", ".join((paper.authors or [])[:5]) if paper else ""
            reason = dp.reason or (paper.recommend_reason if paper else "")
            cards += _get_block_html(
                title=title_text,
                authors=authors,
                reason=reason,
                tldr=dp.tldr,
                url=(paper.url if paper else None),
                pdf_url=(paper.pdf_url if paper else None),
                source=(paper.source if paper else None),
                score=(paper.score if paper else None),
                language=language,
            )
            selected_indices.add(dp.index)
    else:
        cards = get_empty_html()

    # Remaining candidates (not picked by the agent) go at the bottom as a
    # compact, no-frills list — still visible, but not editorialised.
    others_html = ""
    if originals:
        others = [p for i, p in enumerate(originals) if i not in selected_indices]
        if others:
            others_html = _others_block_html(others, language)

    content = cards + others_html
    html = framework.replace("__TITLE__", title)
    html = html.replace("__SUMMARY__", summary)
    html = html.replace("__PREHEADER__", _preheader(digest, language))
    html = html.replace("__INTRO__", f'<div style="font-size:15px;color:#374151;line-height:1.6;margin-bottom:20px;">{intro}</div>' if intro else "")
    html = html.replace("__OUTRO__", f'<div style="font-size:14px;color:#6b7280;margin-top:20px;line-height:1.6;">{outro}</div>' if outro else "")
    html = html.replace("__FOOTER__", _footer_html(language))
    return html.replace("__CONTENT__", content)


def render_fallback(papers: list[Paper], language: str = "English") -> str:
    """Gentle fallback: embedding-ordered cards with no agent editorial."""
    if not papers:
        return framework.replace("__TITLE__", "Daily paper digest").replace(
            "__SUMMARY__", "No new papers today"
        ).replace("__PREHEADER__", "").replace("__FOOTER__", _footer_html(language)).replace(
            "__INTRO__", "").replace("__OUTRO__", "").replace("__CONTENT__", get_empty_html())

    body = ""
    for _i, p in enumerate(papers):
        body += _get_block_html(
            title=p.title,
            authors=", ".join((p.authors or [])[:5]),
            reason=p.recommend_reason,
            tldr=p.tldr,
            url=p.url,
            pdf_url=p.pdf_url,
            source=p.source,
            score=p.score,
            language=language,
        )
    if language.lower().startswith("chinese"):
        summary = f"共 {len(papers)} 篇论文"
    else:
        summary = f"{len(papers)} paper{'s' if len(papers) != 1 else ''} recommended"
    return framework.replace("__TITLE__", "Daily paper digest").replace(
        "__SUMMARY__", summary
    ).replace("__PREHEADER__", "").replace("__FOOTER__", _footer_html(language)).replace(
        "__INTRO__", "").replace("__OUTRO__", "").replace("__CONTENT__", body)
