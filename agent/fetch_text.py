#!/usr/bin/env python
"""Fetch full text for one candidate paper, printed to stdout.

Used by the Pi agent (agent/run.mjs) when it decides to read a paper in
depth: the agent calls `uv run python agent/fetch_text.py <url> [pdf_url]
[source_url]` and gets the paper's full text on stdout. The agent — not the
pipeline — decides which papers to fetch and read.

Exit 0 + text on stdout = success; exit 1 = nothing extractable.
"""

from __future__ import annotations

import sys

from zotero_arxiv_daily.protocol import Paper
from zotero_arxiv_daily.retriever.arxiv_retriever import (
    extract_text_from_html,
    extract_text_from_pdf,
    extract_text_from_tar,
)


def main() -> int:
    if len(sys.argv) < 2:
        print("usage: fetch_text.py <url> [pdf_url] [source_url]", file=sys.stderr)
        return 2
    url = sys.argv[1]
    pdf_url = sys.argv[2] if len(sys.argv) > 2 else None
    source_url = sys.argv[3] if len(sys.argv) > 3 else None
    paper = Paper(
        source="arxiv",
        title="",
        authors=[],
        abstract="",
        url=url,
        pdf_url=pdf_url,
        source_url=source_url,
    )
    text = (
        extract_text_from_tar(paper)
        or extract_text_from_html(paper)
        or extract_text_from_pdf(paper)
    )
    if text:
        sys.stdout.write(text)
        return 0
    print(f"fetch_text: no extractable full text for {url}", file=sys.stderr)
    return 1


if __name__ == "__main__":
    sys.exit(main())
