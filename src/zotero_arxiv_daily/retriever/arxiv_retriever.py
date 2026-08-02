import multiprocessing
import os
from collections.abc import Callable
from queue import Empty
from tempfile import TemporaryDirectory
from typing import Any, TypeVar

import feedparser
import requests
from loguru import logger

from ..protocol import Paper
from ..utils import extract_markdown_from_pdf, extract_tex_code_from_tar
from .base import BaseRetriever, register_retriever

T = TypeVar("T")

DOWNLOAD_TIMEOUT = (10, 60)
PDF_EXTRACT_TIMEOUT = 180
TAR_EXTRACT_TIMEOUT = 180


def _download_file(url: str, path: str) -> None:
    with requests.get(url, stream=True, timeout=DOWNLOAD_TIMEOUT) as response:
        response.raise_for_status()
        with open(path, "wb") as file:
            for chunk in response.iter_content(chunk_size=1024 * 1024):
                if chunk:
                    file.write(chunk)


def _run_in_subprocess(
    result_queue: Any,
    func: Callable[..., T | None],
    args: tuple[Any, ...],
) -> None:
    try:
        result_queue.put(("ok", func(*args)))
    except Exception as exc:
        result_queue.put(("error", f"{type(exc).__name__}: {exc}"))


def _run_with_hard_timeout(
    func: Callable[..., T | None],
    args: tuple[Any, ...],
    *,
    timeout: float,
    operation: str,
    paper_title: str,
) -> T | None:
    start_methods = multiprocessing.get_all_start_methods()
    context = multiprocessing.get_context("fork" if "fork" in start_methods else start_methods[0])
    result_queue = context.Queue()
    process = context.Process(target=_run_in_subprocess, args=(result_queue, func, args))
    process.start()

    try:
        status, payload = result_queue.get(timeout=timeout)
    except Empty:
        if process.is_alive():
            process.kill()
        process.join(5)
        result_queue.close()
        result_queue.join_thread()
        logger.warning(f"{operation} timed out for {paper_title} after {timeout} seconds")
        return None

    process.join(5)
    result_queue.close()
    result_queue.join_thread()

    if status == "ok":
        return payload

    logger.warning(f"{operation} failed for {paper_title}: {payload}")
    return None


def _extract_text_from_pdf_worker(pdf_url: str) -> str:
    with TemporaryDirectory() as temp_dir:
        path = os.path.join(temp_dir, "paper.pdf")
        _download_file(pdf_url, path)
        return extract_markdown_from_pdf(path)


def _extract_text_from_html_worker(html_url: str) -> str | None:
    import trafilatura

    downloaded = trafilatura.fetch_url(html_url)
    if downloaded is None:
        raise ValueError(f"Failed to download HTML from {html_url}")
    text = trafilatura.extract(downloaded, include_comments=False, include_tables=False)
    if not text:
        raise ValueError(f"No text extracted from {html_url}")
    return text


def _extract_text_from_tar_worker(source_url: str, paper_id: str, paper_title: str | None = None) -> str | None:
    with TemporaryDirectory() as temp_dir:
        path = os.path.join(temp_dir, "paper.tar.gz")
        _download_file(source_url, path)
        file_contents = extract_tex_code_from_tar(path, paper_id, paper_title=paper_title)
        if not file_contents or "all" not in file_contents:
            raise ValueError("Main tex file not found.")
        return file_contents["all"]


def _parse_abstract(summary: str) -> str:
    """Extract the abstract from an arXiv Atom RSS entry summary.

    The summary looks like: 'arXiv:2508.13426v1 Announce Type: new \\nAbstract: <text>'
    """
    if "Abstract:" in summary:
        return summary.split("Abstract:", 1)[1].strip()
    return summary.strip()


def _parse_authors(entry: Any) -> list[str]:
    """arXiv Atom RSS lists all authors as one comma-joined name string."""
    names = getattr(entry, "authors", None) or []
    if not names:
        return []
    raw = names[0].get("name", "") if isinstance(names[0], dict) else str(names[0])
    return [a.strip() for a in raw.split(",") if a.strip()]


def _rss_entry_to_paper(entry: Any) -> dict[str, Any]:
    """Convert an arXiv Atom RSS entry to a Paper-ready dict.

    The RSS feed already carries title, abstract, authors and announce type,
    so we never need to hit the arXiv query API (which is rate-limited) just
    to list the day's new papers. PDF/source URLs are derived from the ID.
    """
    paper_id = entry.id.removeprefix("oai:arXiv.org:")
    return {
        "paper_id": paper_id,
        "title": entry.title,
        "abstract": _parse_abstract(entry.get("summary", "")),
        "authors": _parse_authors(entry),
        "url": entry.get("link") or f"https://arxiv.org/abs/{paper_id}",
        "pdf_url": f"https://arxiv.org/pdf/{paper_id}",
        "source_url": f"https://arxiv.org/e-print/{paper_id}",
    }


@register_retriever("arxiv")
class ArxivRetriever(BaseRetriever):
    def __init__(self, config):
        super().__init__(config)
        if self.config.source.arxiv.category is None:
            raise ValueError("category must be specified for arxiv.")

    def _retrieve_raw_papers(self) -> list[dict[str, Any]]:
        include_cross_list = self.config.source.arxiv.get("include_cross_list", False)
        # The RSS atom feed is arXiv's lightweight, rate-limit-free way to get
        # the day's new submissions (official recommendation for this use case).
        # Fetch each category as its own feed: a single `+`-joined query is
        # capped by arXiv and silently truncates when many categories are set.
        allowed_announce_types = {"new", "cross"} if include_cross_list else {"new"}
        raw_papers: list[dict[str, Any]] = []
        total_entries = 0
        for category in self.config.source.arxiv.category:
            feed = feedparser.parse(f"https://rss.arxiv.org/atom/{category}")
            if getattr(feed.feed, "title", "") and "Feed error for query" in feed.feed.title:
                raise Exception(f"Invalid ARXIV_QUERY: {category}.")
            total_entries += len(feed.entries)
            raw_papers.extend(
                _rss_entry_to_paper(entry)
                for entry in feed.entries
                if entry.get("arxiv_announce_type", "new") in allowed_announce_types
            )
        # The atom feed is capped by arXiv; warn when we are close to the limit
        # so a silently truncated candidate list can be noticed.
        if total_entries >= 1000:
            logger.warning(
                f"arXiv RSS returned {total_entries} entries across categories — the feed may be "
                f"truncated. Consider splitting categories or raising include_cross_list."
            )
        # A paper cross-listed into several of the configured categories appears
        # once per feed; dedupe by paper id before returning.
        seen_ids: set[str] = set()
        deduped: list[dict[str, Any]] = []
        for paper in raw_papers:
            if paper["paper_id"] in seen_ids:
                continue
            seen_ids.add(paper["paper_id"])
            deduped.append(paper)
        raw_papers = deduped
        if self.config.executor.debug:
            raw_papers = raw_papers[:10]
        logger.info(f"Parsed {len(raw_papers)} papers from arXiv RSS ({', '.join(self.config.source.arxiv.category)})")
        return raw_papers

    def convert_to_paper(self, raw_paper: dict[str, Any]) -> Paper:
        return Paper(
            source=self.name,
            title=raw_paper["title"],
            authors=raw_paper["authors"],
            abstract=raw_paper["abstract"],
            url=raw_paper["url"],
            pdf_url=raw_paper["pdf_url"],
            source_url=raw_paper["source_url"],
        )

    def fetch_full_text(self, paper: Paper) -> str | None:
        """Fetch full text only for papers that made it past reranking."""
        full_text = extract_text_from_tar(paper)
        if full_text is None:
            full_text = extract_text_from_html(paper)
        if full_text is None:
            full_text = extract_text_from_pdf(paper)
        return full_text


def extract_text_from_html(paper: Paper) -> str | None:
    html_url = paper.url.replace("/abs/", "/html/")
    try:
        return _extract_text_from_html_worker(html_url)
    except Exception as exc:
        logger.warning(f"HTML extraction failed for {paper.title}: {exc}")
        return None


def extract_text_from_pdf(paper: Paper) -> str | None:
    if paper.pdf_url is None:
        logger.warning(f"No PDF URL available for {paper.title}")
        return None
    return _run_with_hard_timeout(
        _extract_text_from_pdf_worker,
        (paper.pdf_url,),
        timeout=PDF_EXTRACT_TIMEOUT,
        operation="PDF extraction",
        paper_title=paper.title,
    )


def extract_text_from_tar(paper: Paper) -> str | None:
    if paper.source_url is None:
        logger.warning(f"No source URL available for {paper.title}")
        return None
    return _run_with_hard_timeout(
        _extract_text_from_tar_worker,
        (paper.source_url, paper.url, paper.title),
        timeout=TAR_EXTRACT_TIMEOUT,
        operation="Tar extraction",
        paper_title=paper.title,
    )
