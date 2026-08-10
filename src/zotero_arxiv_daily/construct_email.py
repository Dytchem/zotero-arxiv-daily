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

from loguru import logger
from pylatexenc.latex2text import LatexNodes2Text

from .harness import Digest, _today_str
from .protocol import Paper

_LATEX_TO_TEXT = LatexNodes2Text()

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

# Unicode subscript / superscript maps (best-effort; unknown chars fall back
# to plain text so nothing looks broken).
_SUBSCRIPTS = {
    "0": "₀", "1": "₁", "2": "₂", "3": "₃", "4": "₄",
    "5": "₅", "6": "₆", "7": "₇", "8": "₈", "9": "₉",
    "a": "ₐ", "e": "ₑ", "h": "ₕ", "i": "ᵢ", "j": "ⱼ", "k": "ₖ",
    "l": "ₗ", "m": "ₘ", "n": "ₙ", "o": "ₒ", "p": "ₚ", "r": "ᵣ",
    "s": "ₛ", "t": "ₜ", "u": "ᵤ", "v": "ᵥ", "x": "ₓ",
    "+": "₊", "-": "₋", "(": "₍", ")": "₎", "=": "₌",
}
_SUPERSCRIPTS = {
    "0": "⁰", "1": "¹", "2": "²", "3": "³", "4": "⁴",
    "5": "⁵", "6": "⁶", "7": "⁷", "8": "⁸", "9": "⁹",
    "+": "⁺", "-": "⁻", "=": "⁼", "(": "⁽", ")": "⁾",
    "n": "ⁿ", "i": "ⁱ", "a": "ᵃ", "e": "ᵉ", "x": "ˣ", "m": "ᵐ",
    "t": "ᵗ", "k": "ᵏ", "o": "ᵒ", "r": "ʳ", "s": "ˢ", "u": "ᵘ",
    "h": "ʰ", "j": "ʲ", "l": "ˡ", "p": "ᵖ", "b": "ᵇ", "c": "ᶜ",
    "d": "ᵈ", "f": "ᶠ", "g": "ᵍ", "v": "ᵛ", "w": "ʷ", "y": "ʸ",
}


def _to_subscript(s: str) -> str:
    out = "".join(_SUBSCRIPTS.get(c, c) for c in s)
    return out if any(ord(c) > 127 for c in out) else f"_{s}"


def _to_superscript(s: str) -> str:
    out = "".join(_SUPERSCRIPTS.get(c, c) for c in s)
    return out if any(ord(c) > 127 for c in out) else f"^{s}"


def _safe(text: str | None) -> str:
    """HTML-escape a text field, keeping the output safe to inline."""
    return html.escape(text or "", quote=True)


def _strip_markdown(text: str) -> str:
    """Remove stray Markdown that an LLM may have left in prose fields.

    The agent is told to write plain text, but occasionally it still emits
    ``**bold**`` or ``[title](url)`` inline. Rather than trusting the model,
    the render layer strips these to clean text: links keep their label,
    emphasis markers disappear. Applied before HTML-escaping.
    """
    if not text:
        return text
    # [label](url) -> label (never keep the raw URL in prose)
    text = re.sub(r"\[([^\]]+)\]\([^)]*\)", r"\1", text)
    # **bold** / __bold__ -> bold
    text = re.sub(r"\*{1,2}([^*]+?)\*{1,2}", r"\1", text)
    text = re.sub(r"_{1,2}([^_]+?)_{1,2}", r"\1", text)
    # backticks / hash headers / arrows leftover from markdown
    text = re.sub(r"`([^`]+?)`", r"\1", text)
    text = re.sub(r"^#{1,6}\s*", "", text, flags=re.MULTILINE)
    text = text.replace("**", "").replace("__", "")
    return text


def _mathify(text: str) -> str:
    """Convert LaTeX math in prose to readable plain text.

    Parsing is delegated to pylatexenc (mature LaTeX parser: handles \\rm/
    \\mathrm fonts, \\frac, \\times, greek letters, unknown macros). Only
    math spans ($...$ / \(...\)) go through the parser — the surrounding
    prose is left untouched (pylatexenc would otherwise eat characters like
    & and _ that are plain text here). On top of the parser output we apply a
    thin display layer so the email reads well: unicode sub/superscripts
    (CO_2 -> CO₂, 10^-3 -> 10⁻³) and primes (A' becomes the prime mark). Nothing is invented:
    unknown LaTeX degrades to its text form.
    """
    if not text:
        return text

    def _conv(match: re.Match) -> str:
        out = _LATEX_TO_TEXT.latex_to_text(match.group(1))
        # Thin display layer on the parser output.
        out = re.sub(r"_\{([^{}]*)\}", lambda m: _to_subscript(m.group(1)), out)
        out = re.sub(r"\^\{([^{}]*)\}", lambda m: _to_superscript(m.group(1)), out)
        out = re.sub(r"_([-+]?\d+)", lambda m: _to_subscript(m.group(1)), out)
        out = re.sub(r"\^([-+]?\d+)", lambda m: _to_superscript(m.group(1)), out)
        out = re.sub(r"_([0-9A-Za-z])", lambda m: _to_subscript(m.group(1)), out)
        out = re.sub(r"\^([0-9A-Za-z])", lambda m: _to_superscript(m.group(1)), out)
        out = out.replace("'", "′")
        return out

    # Chemistry subscripts first (MoS$2$ -> MoS₂, C$60$ -> C₆₀): pylatexenc
    # would swallow the $...$ and leave a plain "2", losing the subscript
    # intent, so we capture digit-only spans right after a letter ourselves.
    out = re.sub(r"(?<=[A-Za-z])\$(\d{1,3})\$", lambda m: _to_subscript(m.group(1)), text)
    # Display math ($$...$$) first so the pair-consumption below never leaves
    # a stray $ behind; render it through the same converter.
    out = re.sub(r"\$\$(.+?)\$\$", lambda m: _conv(m), out, flags=re.DOTALL)
    # Math spans only: $...$ and \( ... \)
    out = re.sub(r"\$([^$]+)\$", lambda m: _conv(m), out)
    out = re.sub(r"\\\((.+?)\\\)", lambda m: _conv(m), out)
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


def _work_html(score: float | None, language: str = "English", fallback_na: bool = False) -> str:
    """Recommendation badge (LLM judgement of the paper's own merit) shown next
    to the relevance badge. Colored by tier so watery papers are visible at a
    glance: green >= 7, amber 5-7, red < 5. Missing score renders as n/a when
    ``fallback_na`` is set (other-candidates list: every paper keeps a badge
    slot); otherwise hidden (embedding-order fallback has no LLM judgement)."""
    label = "推荐度" if language.lower().startswith("chinese") else "Recommendation"
    if score is None:
        if not fallback_na:
            # No LLM quality judgement (e.g. embedding-order fallback): hide the
            # badge rather than showing a meaningless n/a next to Relevance.
            return ""
        return f'<span style="display:inline-block;background:#f3f4f6;color:#9ca3af;font-size:12px;font-weight:600;padding:3px 10px;border-radius:999px;">{label}: n/a</span>'
    try:
        rate = round(float(score), 1)
    except (TypeError, ValueError):
        rate = "n/a"
    if isinstance(rate, float):
        if rate >= 7.0:
            bg, fg = "#ecfdf5", "#047857"
        elif rate >= 5.0:
            bg, fg = "#fffbeb", "#b45309"
        else:
            bg, fg = "#fef2f2", "#b91c1c"
    else:
        bg, fg = "#f3f4f6", "#6b7280"
    return (
        f'<span style="display:inline-block;background:{bg};color:{fg};'
        f'font-size:12px;font-weight:600;padding:3px 10px;border-radius:999px;">'
        f'{label}: {rate}</span>'
    )


def _get_block_html(title, authors, reason, tldr, url, pdf_url, source, score=None, work_score=None, language: str = "English") -> str:
    # Escape the title like every other text field — a paper title can carry
    # HTML entities / angle brackets and must not be inlined raw into markup.
    title_text = _safe(_strip_markdown(_mathify(title)))
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
            f'<div style="margin-top:10px;font-size:13px;color:#4c1d95;'
            f'background:#f5f3ff;border-left:4px solid #a855f7;padding:10px 16px;'
            f'border-radius:6px;"><strong>{why_label}:</strong> {_safe(_strip_markdown(_mathify(reason)))}</div>'
        )
    elif tldr:
        note_html = (
            f'<div style="margin-top:12px;padding:10px 14px;border-left:4px solid #2563eb;'
            f'background:#f8fafc;border-radius:6px;font-size:14px;color:#374151;line-height:1.55;">'
            f'<strong>{tldr_label}:</strong> {_safe(_strip_markdown(_mathify(tldr)))}</div>'
        )

    buttons = ""
    clean_pdf = _clean_link(pdf_url)
    clean_url = _clean_link(url)
    if clean_pdf:
        buttons += (
            f'<td style="width:50%;padding-right:4px;"><a href="{clean_pdf}" class="btn" style="display:block;width:100%;box-sizing:border-box;text-align:center;text-decoration:none;font-size:13px;'
            f'font-weight:700;color:#ffffff;background-color:#2563eb;background-image:linear-gradient(135deg,#2563eb,#4f46e5);'
            f'padding:11px 0;border-radius:8px;">PDF</a></td>'
        )
    if clean_url:
        buttons += (
            f'<td style="width:50%;padding-left:4px;"><a href="{clean_url}" class="btn" style="display:block;width:100%;box-sizing:border-box;text-align:center;text-decoration:none;font-size:13px;'
            f'font-weight:700;color:#2563eb;border:1px solid #2563eb;padding:10px 0;'
            f'border-radius:8px;">Abstract</a></td>'
        )

    if buttons:
        buttons = (
            f'<table cellpadding="0" cellspacing="0" border="0" style="border-collapse:collapse;width:100%;table-layout:fixed;">'
            f'<tr>{buttons}</tr></table>'
        )

    return f"""
    <div class="card" style="border:1px solid #e5e7eb;border-radius:10px;padding:18px 20px;margin-bottom:24px;background:#ffffff;">
      {badge_html}
      <div style="font-size:17px;font-weight:700;color:#111827;line-height:1.4;">{title_html}</div>
      <div style="font-size:13px;color:#6b7280;margin-top:8px;line-height:1.5;">{_safe(authors)}</div>
      <div style="margin-top:10px;">{_rate_html(score, language)} {_work_html(work_score, language)}</div>
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


def _others_block_html(papers: list[Paper], language: str = "English", others_summary: str = "", others_map: dict[int, dict] | None = None, indices: list[int] | None = None) -> str:
    """Compact list of candidates the agent did not pick (bottom of the email).

    Each entry shows the same Relevance + Recommendation badges as the picked cards
    (on their own line, below the title — never inline with the title), plus
    an optional per-paper note. An LLM-written overall summary is shown above
    the list when provided.

    ``indices`` (optional) carries the ORIGINAL candidate index for each
    entry in ``papers`` — ``others_map`` is keyed by original index, so this
    keeps the badges aligned after the list was re-indexed from 0.
    """
    heading = "其他候选" if language.lower().startswith("chinese") else "Other candidates"
    others_map = others_map or {}
    rows = ""
    for i, p in enumerate(papers):
        orig_index = indices[i] if indices else i
        title_text = _safe(_strip_markdown(_mathify(p.title)))
        clean_url = _clean_link(p.url)
        link = f'<a href="{clean_url}" style="color:#111827;text-decoration:none;">{title_text}</a>' if clean_url else title_text
        badge = ""
        if p.source:
            label = _SOURCE_LABELS.get(p.source, p.source)
            badge = f'<span style="background:#eef2ff;color:#4f46e5;font-size:10px;font-weight:700;padding:1px 8px;border-radius:999px;margin-left:8px;">{_safe(label)}</span>'
        # Same two chips as the picked cards, on their own line below the title.
        meta = others_map.get(orig_index, {})
        work_score = meta.get("work_score")
        chips = _rate_html(p.score, language) + " " + _work_html(work_score, language, fallback_na=True)
        note = _safe(_strip_markdown(_mathify(meta.get("note", ""))))
        note_html = f'<div style="margin-top:4px;font-size:12px;color:#6b7280;line-height:1.45;">{note}</div>' if note else ""
        border = "border-bottom:1px solid #f3f4f6;" if i < len(papers) - 1 else ""
        rows += (
            f'<div style="padding:10px 0;{border}">'
            f'<div style="font-size:13px;line-height:1.4;">{link}{badge}</div>'
            f'<div style="margin-top:6px;">{chips}</div>'
            f'{note_html}'
            f'</div>'
        )
    summary_html = ""
    if others_summary:
        summary_html = (
            f'<div style="font-size:13px;color:#374151;line-height:1.6;'
            f'background:#f8fafc;border-left:4px solid #9ca3af;padding:8px 14px;'
            f'border-radius:6px;margin-bottom:8px;">'
            f'{_safe(_strip_markdown(_mathify(others_summary)))}</div>'
        )
    return (
        f'<div style="margin-top:24px;padding-top:14px;border-top:2px solid #e5e7eb;">'
        f'<div style="font-size:13px;font-weight:700;color:#6b7280;margin-bottom:4px;">{heading}</div>'
        f'{summary_html}'
        f'{rows}</div>'
    )


def render_email(digest: Digest | None, originals: list[Paper] | None = None, language: str = "English", candidate_count: int | None = None) -> str:
    """Render a Digest (or a plain fallback list) to HTML email.

    ``digest`` is the agent's structured output; when it is None we render the
    ``originals`` list as simple embedding-ordered cards (the graceful fallback).

    ``originals`` carries the FULL paper pool the digest's indices refer to
    (candidates first, filtered-out papers after). ``candidate_count`` tells
    the renderer which indices are pre-filtered candidates (0..N-1): the
    "other candidates" block lists every unpicked candidate plus any
    filtered-out pool paper the agent explicitly scored in ``others`` — the
    rest of the pool stays hidden. When ``candidate_count`` is None (legacy
    callers), everything in ``originals`` is treated as a candidate.
    """
    if digest is None:
        return render_fallback(originals or [])
    originals = originals or []
    if candidate_count is None:
        candidate_count = len(originals)

    title = _safe(_strip_markdown(_mathify(digest.subject))) or "Daily paper digest"
    today = _today_str()
    # Avoid a duplicated date: the fixed subject format always carries the
    # date (e.g. "Zotero-arXiv-Daily 每日推荐 · 2026年8月3日" or
    # "Zotero-arXiv-Daily Daily Digest · 2026-08-03"). Any 4-digit year in
    # the title means the date is already there.
    subject_has_date = bool(re.search(r"\d{4}", title))
    if language.lower().startswith("chinese"):
        summary = (f"{today} · " if not subject_has_date else "") + f"精选 {len(digest.papers)} 篇论文"
    else:
        n_label = f"{len(digest.papers)} paper{'s' if len(digest.papers) != 1 else ''} recommended"
        summary = (f"{today} · " if not subject_has_date else "") + n_label
    intro = _safe(_strip_markdown(_mathify(digest.intro)))
    outro = _safe(_strip_markdown(_mathify(digest.outro)))

    cards = ""
    selected_indices: set[int] = set()
    if digest.papers:
        # Map candidate index -> original Paper so we can pull authors/url/pdf/source.
        originals_by_index = dict(enumerate(originals or []))
        # Render picks in the agent's own editorial order. The agent is the
        # expert: it has been instructed to rank by overall value (work
        # quality x relevance x taste) and the evaluator audits that ordering,
        # so the render layer trusts its judgement instead of re-sorting.
        for dp in digest.papers:
            # Defensive: an out-of-range / negative index from the agent must
            # never render as a broken "Paper -1" card — skip it instead.
            if dp.index is None or not (0 <= dp.index < len(originals)):
                logger.warning(f"Skipping digest paper with invalid index {dp.index!r}")
                continue
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
                work_score=dp.work_score,
                language=language,
            )
            selected_indices.add(dp.index)
    else:
        cards = get_empty_html()

    # Remaining candidates (not picked by the agent) go at the bottom as a
    # compact list — still visible, with the same Relevance + Recommendation badges as
    # the picked cards plus the agent's overall comment on them. Pool papers
    # beyond the candidate list only appear here if the agent scored them
    # (rescued from the filter) — the rest of the pool stays hidden.
    others_html = ""
    if originals:
        # Keep the ORIGINAL pool index for each remaining paper so the
        # badge map (keyed by original index) stays aligned.
        others_indices = [
            i for i in range(min(candidate_count, len(originals)))
            if i not in selected_indices
        ]
        # Filtered-out pool papers the agent explicitly scored in "others".
        for entry in digest.others or []:
            idx = int(entry.get("index", -1))
            if (
                0 <= idx < len(originals)
                and idx >= candidate_count
                and idx not in others_indices
            ):
                others_indices.append(idx)
        # Order the whole others block: papers the agent actually read and
        # annotated (has a note / analysis) come FIRST — readers see the
        # analysed entries up front — then the rest by relevance (embedding
        # score). Rescued pool papers slot in where their score belongs;
        # papers without a score go last, keeping their relative order.
        note_of: dict[int, str] = {}
        for entry in digest.others or []:
            idx = int(entry.get("index", -1))
            if idx >= 0:
                note_of[idx] = entry.get("note", "")
        others_indices.sort(
            key=lambda i: (
                0 if note_of.get(i) else 1,  # analysed/read first
                originals[i].score is None,  # unscored last
                -(originals[i].score or 0),  # then relevance desc
            )
        )
        others = [originals[i] for i in others_indices]
        if others:
            others_map: dict[int, dict] = {}
            for entry in digest.others or []:
                idx = int(entry.get("index", -1))
                if idx < 0:
                    continue
                others_map[idx] = {
                    "work_score": entry.get("work_score"),
                    "note": entry.get("note", ""),
                }
            others_html = _others_block_html(
                others,
                language,
                others_summary=digest.others_summary or "",
                others_map=others_map,
                indices=others_indices,
            )

    content = cards + others_html
    # One-pass token substitution: replaces every template token in a single
    # scan of the framework, so LLM-authored text that happens to contain a
    # token string (e.g. "__CONTENT__") is never re-processed afterwards.
    html = re.sub(
        r"__(?:TITLE|SUMMARY|PREHEADER|INTRO|OUTRO|FOOTER|CONTENT)__",
        lambda m: {
            "__TITLE__": title,
            "__SUMMARY__": summary,
            "__PREHEADER__": _preheader(digest, language),
            "__INTRO__": f'<div style="font-size:15px;color:#374151;line-height:1.6;margin-bottom:20px;">{intro}</div>' if intro else "",
            "__OUTRO__": f'<div style="font-size:14px;color:#6b7280;margin-top:20px;line-height:1.6;">{outro}</div>' if outro else "",
            "__FOOTER__": _footer_html(language),
            "__CONTENT__": content,
        }[m.group(0)],
        framework,
    )
    return html


def render_fallback(papers: list[Paper], language: str = "English") -> str:
    """Gentle fallback: embedding-ordered cards with no agent editorial."""
    if not papers:
        body = get_empty_html()
    else:
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
    return re.sub(
        r"__(?:TITLE|SUMMARY|PREHEADER|INTRO|OUTRO|FOOTER|CONTENT)__",
        lambda m: {
            "__TITLE__": "Daily paper digest",
            "__SUMMARY__": summary,
            "__PREHEADER__": "",
            "__INTRO__": "",
            "__OUTRO__": "",
            "__FOOTER__": _footer_html(language),
            "__CONTENT__": body,
        }[m.group(0)],
        framework,
    )
