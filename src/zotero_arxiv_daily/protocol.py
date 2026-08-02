"""Data classes for the daily digest pipeline.

The old TLDR/affiliations methods are gone — that work is now the single
HarnessAgent's job. Paper keeps the legacy ``tldr``/``affiliations`` fields
as None so old serialized Paper objects still load.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime


@dataclass
class Paper:
    """One retrieved candidate paper, used everywhere downstream."""

    source: str
    title: str
    authors: list[str]
    abstract: str
    url: str
    pdf_url: str | None = None
    full_text: str | None = None
    tldr: str | None = None
    affiliations: list[str] | None = None
    score: float | None = None
    source_url: str | None = None
    recommend_reason: str | None = None


@dataclass
class CorpusPaper:
    """One paper from the user's Zotero library (used to build the research profile)."""

    title: str
    abstract: str
    added_date: datetime
    paths: list[str]


@dataclass
class RawPaperItem:
    """A raw paper item as returned by a retriever (before conversion to Paper).

    ``id`` and ``url`` are required; the retriever subclass decides what extra
    fields to populate (``abstract``, ``authors``, ``pdf_url``, ``raw_text``,
    etc.) before calling ``convert_to_paper``.
    """

    id: str
    url: str
