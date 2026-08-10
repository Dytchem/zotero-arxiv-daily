"""Daily digest pipeline: Zotero corpus -> retrieve -> embed-rerank -> single LLM agent -> email.

The pipeline is intentionally thin. Everything editorial — what to recommend,
how to phrase each reason, how to write the email — is the job of the
single :class:`harness.HarnessAgent`. The pipeline just feeds it the cheap
stuff (corpus, candidates with embedding scores) and renders the result.

Flow:
    fetch_zotero_corpus
        -> filter_corpus
        -> retrievers.retrieve_papers (arxiv/biorxiv/medrxiv)
        -> _dedupe_papers
        -> reranker.rerank        # cheap: embedding + BM25 hybrid
        -> _filter_min_score
        -> _filter_keywords
        -> _filter_sent_history
        -> HarnessAgent.generate   # the only LLM call site
        -> construct_email.render_email
        -> notifier.send

Failure modes always degrade to embedding-order so the daily email goes out.
"""

from __future__ import annotations

import datetime as _dt
import json
import os
import re
import shutil
import subprocess
import time
from pathlib import Path

from loguru import logger
from omegaconf import DictConfig, ListConfig
from pyzotero import zotero
from tqdm import tqdm

from .construct_email import render_email
from .harness import HarnessAgent
from .protocol import CorpusPaper, Paper
from .reranker import get_reranker_cls
from .retriever import get_retriever_cls
from .utils import glob_match


def normalize_path_patterns(patterns: list[str] | ListConfig | None, config_key: str) -> list[str] | None:
    if patterns is None:
        return None
    if not isinstance(patterns, (list, ListConfig)):
        raise TypeError(
            f"config.zotero.{config_key} must be a list of glob patterns or null, "
            'for example ["2026/survey/**"]. Single strings are not supported.'
        )
    if any(not isinstance(pattern, str) for pattern in patterns):
        raise TypeError(f"config.zotero.{config_key} must contain only glob pattern strings.")
    return list(patterns)


class Executor:
    def __init__(self, config: DictConfig):
        self.config = config
        self.include_path_patterns = normalize_path_patterns(config.zotero.include_path, "include_path")
        self.ignore_path_patterns = normalize_path_patterns(config.zotero.ignore_path, "ignore_path")
        self.retrievers = {
            source: get_retriever_cls(source)(config) for source in config.executor.source
        }
        self.reranker = get_reranker_cls(config.executor.reranker)(config)
        self._rerank_failed = False
        self._validate_config()

    def _validate_config(self) -> None:
        """Fail fast with a clear message instead of silently sending no email."""

        def _missing(value) -> bool:
            return value is None or (isinstance(value, str) and (not value or value.startswith("???")))

        required: dict[str, object] = {
            "zotero.user_id": self.config.zotero.get("user_id"),
            "zotero.api_key": self.config.zotero.get("api_key"),
            "email.sender": self.config.email.get("sender"),
            "email.receiver": self.config.email.get("receiver"),
            "email.smtp_server": self.config.email.get("smtp_server"),
            "email.smtp_port": self.config.email.get("smtp_port"),
            "email.sender_password": self.config.email.get("sender_password"),
            "llm.api.key": self.config.llm.api.get("key"),
            "llm.api.base_url": self.config.llm.api.get("base_url"),
            "llm.generation_kwargs.model": self.config.llm.generation_kwargs.get("model"),
        }
        reranker = self.config.executor.get("reranker", "local")
        if reranker == "api":
            required["reranker.api.key"] = self.config.reranker.api.get("key")
            required["reranker.api.base_url"] = self.config.reranker.api.get("base_url")
            required["reranker.api.model"] = self.config.reranker.api.get("model")
        else:
            required["reranker.local.model"] = self.config.reranker.local.get("model")

        missing = [path for path, value in required.items() if _missing(value)]
        if missing:
            raise ValueError(
                "Missing required config: " + ", ".join(missing)
                + ". Check CUSTOM_CONFIG variable and repository secrets."
            )

    # ------------------------------------------------------------------
    # Zotero
    # ------------------------------------------------------------------

    def fetch_zotero_corpus(self) -> list[CorpusPaper]:
        logger.info("Fetching zotero corpus")
        zot = zotero.Zotero(self.config.zotero.user_id, 'user', self.config.zotero.api_key)
        collections = zot.everything(zot.collections())
        collections = {c['key']: c for c in collections}
        corpus = zot.everything(zot.items(itemType='conferencePaper || journalArticle || preprint'))
        # Keep every entry: many papers imported from PDFs have an empty
        # abstractNote, and dropping them shrinks the research profile to a
        # handful of papers. Fall back to the title where abstract is empty.
        corpus = [c for c in corpus if c['data']['title'] != '']

        def get_collection_path(col_key: str) -> str:
            if p := collections[col_key]['data']['parentCollection']:
                return get_collection_path(p) + '/' + collections[col_key]['data']['name']
            return collections[col_key]['data']['name']

        for c in corpus:
            paths = [get_collection_path(col) for col in c['data']['collections']]
            c['paths'] = paths
        logger.info(f"Fetched {len(corpus)} zotero papers")
        return [CorpusPaper(
            title=c['data']['title'],
            abstract=c['data']['abstractNote'] or c['data']['title'],
            added_date=_dt.datetime.strptime(c['data']['dateAdded'], '%Y-%m-%dT%H:%M:%SZ'),
            paths=c['paths'],
        ) for c in corpus]

    def filter_corpus(self, corpus: list[CorpusPaper]) -> list[CorpusPaper]:
        if self.include_path_patterns:
            logger.info(f"Selecting zotero papers matching include_path: {self.include_path_patterns}")
            corpus = [
                c for c in corpus
                if any(
                    glob_match(path, pattern)
                    for path in c.paths
                    for pattern in self.include_path_patterns
                )
            ]
        if self.ignore_path_patterns:
            logger.info(f"Excluding zotero papers matching ignore_path: {self.ignore_path_patterns}")
            corpus = [
                c for c in corpus
                if not any(
                    glob_match(path, pattern)
                    for path in c.paths
                    for pattern in self.ignore_path_patterns
                )
            ]
        if self.include_path_patterns or self.ignore_path_patterns:
            samples = corpus[:min(5, len(corpus))]
            samples = '\n'.join([c.title + ' - ' + '\n'.join(c.paths) for c in samples])
            logger.info(f"Selected {len(corpus)} zotero papers:\n{samples}\n...")
        return corpus

    # ------------------------------------------------------------------
    # Retrieval + filtering helpers
    # ------------------------------------------------------------------

    @staticmethod
    def _normalize_title(title: str) -> str:
        if not title:
            return ""
        return re.sub(r"[^a-z0-9]+", "", title.lower())

    @staticmethod
    def _dedupe_papers(papers: list[Paper]) -> list[Paper]:
        """Drop duplicate papers by URL or normalized title."""
        seen_urls: set[str] = set()
        seen_titles: set[str] = set()
        result = []
        for p in papers:
            if p.url in seen_urls:
                continue
            title_key = Executor._normalize_title(p.title)
            if title_key and title_key in seen_titles:
                continue
            seen_urls.add(p.url)
            if title_key:
                seen_titles.add(title_key)
            result.append(p)
        return result

    def _filter_min_score(self, papers: list[Paper]) -> list[Paper]:
        min_score = self.config.executor.get("min_score")
        if min_score is None:
            return papers
        if getattr(self, "_rerank_failed", False):
            # Degraded run (no scores): dropping everything would silently
            # kill the email — keep fetch order instead.
            logger.warning("Skipping min_score filter (reranker failed, papers unscored)")
            return papers
        kept = [p for p in papers if p.score is not None and p.score >= min_score]
        dropped = len(papers) - len(kept)
        if dropped:
            logger.info(f"Dropped {dropped} papers below min_score={min_score}")
        return kept

    @staticmethod
    def _matches_any(text: str, keywords: list[str] | ListConfig | None) -> bool:
        if not keywords:
            return False
        lowered = text.lower()
        return any(k.lower() in lowered for k in keywords)

    def _filter_keywords(self, papers: list[Paper]) -> list[Paper]:
        include = self.config.executor.get("keywords_include")
        exclude = self.config.executor.get("keywords_exclude")
        if not include and not exclude:
            return papers
        kept = []
        for p in papers:
            haystack = f"{p.title}\n{p.abstract}"
            if include and not self._matches_any(haystack, include):
                continue
            if exclude and self._matches_any(haystack, exclude):
                continue
            kept.append(p)
        dropped = len(papers) - len(kept)
        if dropped:
            logger.info(f"Dropped {dropped} papers by keyword filter (include={include}, exclude={exclude})")
        return kept

    # ------------------------------------------------------------------
    # Sent history
    # ------------------------------------------------------------------

    def _sent_history_path(self) -> Path:
        cache_dir = self.config.executor.get("cache_dir") or ".cache"
        return Path(cache_dir) / "sent_papers.json"

    def _load_sent_history(self) -> set[str]:
        path = self._sent_history_path()
        if not path.exists():
            return set()
        try:
            data = json.loads(path.read_text())
            return set(data.get("urls", []))
        except Exception as exc:
            logger.warning(f"Failed to load sent-history: {exc}")
            return set()

    def _save_sent_history(self, urls: set[str]) -> None:
        try:
            path = self._sent_history_path()
            path.parent.mkdir(parents=True, exist_ok=True)
            tmp = path.with_suffix(".tmp")
            tmp.write_text(json.dumps({"urls": sorted(urls)}))
            tmp.replace(path)
        except Exception as exc:
            logger.warning(f"Failed to save sent-history: {exc}")

    def _filter_sent_history(self, papers: list[Paper]) -> list[Paper]:
        if self.config.executor.get("debug", False):
            return papers
        if not self.config.executor.get("dedupe_history", True):
            return papers
        sent = self._load_sent_history()
        if not sent:
            return papers
        kept = [p for p in papers if p.url not in sent]
        dropped = len(papers) - len(kept)
        if dropped:
            logger.info(f"Dropped {dropped} papers already sent in previous runs")
        return kept

    # ------------------------------------------------------------------
    # The single LLM call site
    # ------------------------------------------------------------------

    def _agent_digest(self, candidates: list[Paper], corpus: list[CorpusPaper], pool: list[Paper] | None = None):
        """Invoke the digest agent. Returns a Digest (may be a fallback).

        Engine selection: ``llm.harness.engine`` — "pi" (default) runs the Pi
        coding agent (Node, agent/run.mjs) as the editorial agent, falling back
        to the Python HarnessAgent when the Pi engine is unavailable or fails;
        "python" skips straight to the Python harness. Either way the pipeline
        degrades to a plain embedding-order digest before giving up.

        ``pool`` (Pi engine only) is the FULL set of deduplicated papers from
        today's fetch — the agent sees every paper, not just the filtered
        candidates, so a high-value paper that the keyword/min-score filters
        dropped can still be recommended. ``candidates`` must be the same
        Paper objects (in pool order, first ``len(candidates)`` of the pool).
        """
        engine = (self.config.llm.get("harness") or {}).get("engine", "pi")
        if engine == "pi":
            digest = self._agent_digest_pi(candidates, corpus, pool=pool)
            if digest is not None:
                return digest
            logger.warning("Pi agent engine failed; falling back to Python harness")
        agent = HarnessAgent(self.config, full_text_fetcher=self._populate_full_text)
        digest = agent.generate(candidates, corpus)
        if digest is None:
            max_n = int(self.config.executor.get("max_paper_num", 100))
            language = (self.config.llm or {}).get("language", "English")
            digest = HarnessAgent.fallback_digest(candidates, max_n, language=language)
        return digest

    def _agent_digest_pi(self, candidates: list[Paper], corpus: list[CorpusPaper], pool: list[Paper] | None = None):
        """Run the Pi coding agent (agent/run.mjs) and return its Digest.

        The candidates + research profile are serialized to a JSON file, the
        Node agent inspects them with its own tools (ROLE.md governs), and its
        ``submit_digest`` tool writes the digest JSON that we read back. Any
        failure (missing node, missing key, timeout, malformed digest) returns
        None so the caller falls back to the Python harness.

        ``pool`` = ALL deduplicated papers from today's fetch. The agent gets
        the full pool (candidates first, rest after), so it can browse
        (inspect_pool), fetch full text, and even recommend papers that the
        pre-filter (keyword/min-score/max_paper_num) dropped.
        """
        node = shutil.which("node")
        run_mjs = Path(__file__).resolve().parent.parent.parent / "agent" / "run.mjs"
        if node is None or not run_mjs.exists():
            logger.warning("Pi engine unavailable (node or agent/run.mjs missing)")
            return None
        llm_cfg = self.config.llm or {}
        api_key = (llm_cfg.get("api") or {}).get("key")
        api_base = (llm_cfg.get("api") or {}).get("base_url")
        if not api_key:
            logger.warning("Pi engine unavailable (no llm.api.key)")
            return None

        # Research profile is built the same way as the Python harness (cached).
        agent = HarnessAgent(self.config, full_text_fetcher=self._populate_full_text)
        profile = agent.build_profile(corpus)
        if profile is None:
            return None

        harness_cfg = llm_cfg.get("harness") or {}
        cache_dir = Path(self.config.executor.get("cache_dir") or ".cache")
        # The agent gets the raw Zotero library — ALL papers (newest first),
        # not just the distilled profile — so it can judge the researcher's
        # interests and taste itself. Context is budgeted: every paper is in
        # the index with a truncated abstract; the agent pulls the full
        # abstract on demand via inspect_library_paper.
        recent_corpus = sorted(
            corpus, key=lambda c: c.added_date, reverse=True
        )
        # Full pool = ALL deduplicated papers from today's fetch (candidates
        # first, in rerank order; the rest after). The agent sees the whole
        # pool — not just the filtered candidates — so a high-value paper the
        # keyword/min-score/max_paper_num filters dropped can still be
        # recommended. Indexes in the digest refer to THIS pool.
        if pool is None:
            pool = candidates
        # top_k caps how many pre-filtered candidates the Pi agent must score
        # — same window semantics as the Python harness (harness.py caps
        # candidates[:top_k]). The agent can still browse the ENTIRE pool via
        # inspect_pool and rescue filtered-out papers, so only the mandatory
        # candidates-coverage list shrinks.
        top_k = int(harness_cfg.get("top_k", 100))
        if top_k > 0 and len(candidates) > top_k:
            logger.info(
                f"Capping candidates for the Pi agent at top_k={top_k} (of {len(candidates)})"
            )
            candidates = candidates[:top_k]
        candidate_urls = {p.url for p in candidates}
        full_pool = list(candidates) + [p for p in pool if p.url not in candidate_urls]
        input_payload = {
            "model": agent.model,
            "language": agent.language,
            "cache_dir": str(cache_dir),
            # run.mjs reads only what it needs: model, language, cache paths,
            # thinking_level, candidate_count, min_inspections, profile,
            # corpus, pool.
            "thinking_level": harness_cfg.get("thinking_level", "max"),
            "candidate_count": len(candidates),
            "min_inspections": int(harness_cfg.get("min_inspections", 3)),
            "full_text_cache_max": int(self.config.executor.get("full_text_cache_max", 200)),
            "profile": {
                "topics": profile.topics,
                "keywords": profile.keywords,
                "methods": profile.methods,
                "summary": profile.summary,
                "taste": profile.taste,
            },
            "corpus": [
                {
                    "title": c.title,
                    # Abstracts truncated to keep the initial context lean —
                    # the agent pulls the full abstract on demand via
                    # inspect_library_paper (same budget as the Python
                    # harness profile prompt).
                    "abstract": (c.abstract or "")[:300],
                    "added": c.added_date.date().isoformat(),
                    "paths": c.paths or [],
                }
                for c in recent_corpus
            ],
            "pool": [
                {
                    "index": i,
                    "title": p.title,
                    "authors": p.authors,
                    "abstract": p.abstract or "",
                    "url": p.url,
                    "pdf_url": p.pdf_url,
                    "source_url": p.source_url,
                    "score": p.score,
                    "source": p.source,
                    "is_candidate": i < len(candidates),
                    # The Pi agent fetches full text ITSELF (fetch_full_text
                    # tool → agent/fetch_text.py). We deliberately do NOT
                    # preload full_text here: the agent decides what to read.
                    # The disk cache (full_texts.json) is shared, so anything
                    # already fetched by the Python side is available to it.
                    "full_text": "",
                }
                for i, p in enumerate(full_pool)
            ],
        }
        cache_dir.mkdir(parents=True, exist_ok=True)
        in_path = cache_dir / "pi_input.json"
        out_path = cache_dir / "pi_digest.json"
        try:
            in_path.write_text(json.dumps(input_payload, ensure_ascii=False), "utf8")
            if out_path.exists():
                out_path.unlink()
            # The Pi agent is a REAL coding agent with bash access and is fed
            # untrusted external content (paper titles/abstracts/full texts).
            # Inheriting every workflow secret would let a prompt-injected
            # paper exfiltrate them via curl — pass a minimal env instead.
            env = {
                "PATH": os.environ.get("PATH", ""),
                "HOME": os.environ.get("HOME", ""),
                "LLM_API_KEY": api_key,
                "OPENAI_API_KEY": api_key,
                "OPENAI_API_BASE": api_base or "https://opencode.ai/zen/go/v1",
            }
            if os.environ.get("UV_CACHE_DIR"):
                env["UV_CACHE_DIR"] = os.environ["UV_CACHE_DIR"]
            if os.environ.get("ANYSEARCH_API_KEY"):
                env["ANYSEARCH_API_KEY"] = os.environ["ANYSEARCH_API_KEY"]
            timeout = int(harness_cfg.get("pi_timeout", 7200))
            proc = subprocess.run(
                [node, str(run_mjs), "--input", str(in_path), "--output", str(out_path)],
                env=env,
                capture_output=True,
                text=True,
                timeout=timeout,
            )
            if proc.returncode != 0:
                logger.warning(f"Pi agent exited {proc.returncode}: {proc.stderr[-500:]}")
                return None
            # Keep the agent's own tool log (stderr) + final summary (stdout)
            # visible in the workflow log at INFO level — the owner reviews
            # the daily run there, and DEBUG mode skips sending so it cannot
            # be used for a real send test.
            if proc.stderr.strip():
                logger.info(f"Pi agent tool log:\n{proc.stderr[-12000:]}")
            if proc.stdout.strip():
                logger.info(f"Pi agent stdout:\n{proc.stdout.strip()[-2000:]}")
            if not out_path.exists():
                logger.warning("Pi agent finished without writing a digest")
                return None
            data = json.loads(out_path.read_text("utf8"))
        except subprocess.TimeoutExpired:
            logger.warning(f"Pi agent timed out after {timeout}s")
            return None
        except Exception as exc:
            logger.warning(f"Pi agent failed: {exc}")
            return None
        digest = HarnessAgent._digest_from_args(data, len(full_pool))
        if not digest.papers and not digest.intro:
            logger.warning("Pi agent returned an empty digest")
            return None
        # Remember the pool the digest indexes refer to, so run() can render
        # against the full pool (candidates + rest) instead of only ranked.
        # The candidate count is stored too: top_k may have capped the
        # candidate window, and run() must use the SAME count for rendering
        # and sent-history or filtered-out papers get mislabelled as candidates.
        self._pi_pool = full_pool
        self._pi_candidate_count = len(candidates)
        logger.info(
            f"Pi agent digest: {len(digest.papers)} recommended, "
            f"{len(digest.others)} others scored (pool={len(full_pool)}, "
            f"candidates={len(candidates)})"
        )
        return digest

    def _populate_full_text(self, paper: Paper) -> None:
        """Fetch full text lazily — only after the agent asks to inspect it.

        Results are cached on disk (cache_dir/full_texts.json, keyed by URL) so
        repeated runs do not re-download/re-parse the same PDFs — the agent's
        on-demand reads hit the cache after the first time.
        """
        if paper.full_text is not None:
            return
        cached = self._load_full_text(paper.url)
        if cached:
            paper.full_text = cached
            return
        retriever = self.retrievers.get(paper.source)
        if retriever is None:
            return
        try:
            text = retriever.fetch_full_text(paper)
            if text:
                paper.full_text = text
                self._save_full_text(paper.url, text)
        except Exception as exc:
            logger.warning(f"Failed to fetch full text for {paper.title}: {exc}")

    # -- full-text disk cache ------------------------------------------

    def _full_text_cache_path(self) -> Path:
        cache_dir = self.config.executor.get("cache_dir") or ".cache"
        return Path(cache_dir) / "full_texts.json"

    def _load_full_text(self, url: str) -> str | None:
        try:
            path = self._full_text_cache_path()
            if not path.exists():
                return None
            data = json.loads(path.read_text())
            text = data.get(url)
            return text if isinstance(text, str) and text else None
        except Exception as exc:
            logger.warning(f"Failed to load full-text cache: {exc}")
            return None

    def _save_full_text(self, url: str, text: str) -> None:
        try:
            path = self._full_text_cache_path()
            path.parent.mkdir(parents=True, exist_ok=True)
            data = {}
            if path.exists():
                try:
                    data = json.loads(path.read_text())
                except Exception:
                    data = {}
            # Bound the cache: keep the most recent N entries (FIFO-ish) so it
            # cannot grow without limit.
            max_entries = int(self.config.executor.get("full_text_cache_max", 200))
            data[url] = text
            if len(data) > max_entries:
                # drop oldest keys (dict preserves insertion order)
                for old in list(data)[: len(data) - max_entries]:
                    data.pop(old, None)
            tmp = path.with_suffix(".tmp")
            tmp.write_text(json.dumps(data))
            tmp.replace(path)
        except Exception as exc:
            logger.warning(f"Failed to save full-text cache: {exc}")

    def _maybe_fetch_full_texts(self, candidates: list[Paper]) -> None:
        """Best-effort fetch full text for the candidates the agent might inspect.

        We do this on a budget (top-N by embedding score) to avoid blowing up
        bandwidth on papers the agent will never look at. Failures are swallowed.
        """
        budget = int((self.config.llm.get("harness") or {}).get("full_text_budget", 10))
        if budget <= 0:
            return
        # Only prefetch papers the agent is most likely to inspect.
        ordered = sorted(candidates, key=lambda p: (p.score or 0.0), reverse=True)[:budget]
        with tqdm(total=len(ordered), desc="Prefetching full text") as bar:
            for p in ordered:
                self._populate_full_text(p)
                bar.update(1)

    # ------------------------------------------------------------------
    # Delivery
    # ------------------------------------------------------------------

    def _deliver(self, html: str, subject: str | None = None) -> bool:
        """Deliver via every configured notifier; each failure is isolated.

        Returns True when at least one notifier succeeded (the digest reached
        the reader), so the caller only marks papers as sent when they were
        actually shown. A notifier failing (e.g. webhook down) never prevents
        the others from delivering.
        """
        from .notifier import get_notifier_cls
        names = self.config.executor.get("notifiers") or ["email"]
        if not subject:
            subject = self._digest_subject()
        delivered_any = False
        for name in names:
            try:
                notifier = get_notifier_cls(name)(self.config)
                logger.info(f"Delivering via notifier={name}...")
                notifier.send(html, subject=subject)
                delivered_any = True
            except Exception as exc:
                logger.error(f"[notifier:{name}] delivery failed: {exc}")
        if not delivered_any:
            raise RuntimeError(f"All notifiers failed to deliver the digest: {names}")
        return delivered_any

    def _digest_subject(self) -> str:
        """Fixed subject format: repo name + daily recommendation + date.

        The subject is deliberately NOT left to the agent's free style — the
        owner wants a stable, scannable format across days. The agent's
        creative subject is discarded; only the body content is its own.

        The date is taken in Asia/Shanghai (the owner's timezone), matching
        the in-email date line — the GitHub Actions runner runs in UTC, and
        using runner-local time would make the subject date drift a day from
        the body (e.g. 22:00 UTC is already the next day in Shanghai).
        """
        language = (self.config.llm or {}).get("language", "English")
        try:
            from zoneinfo import ZoneInfo

            now = _dt.datetime.now(ZoneInfo("Asia/Shanghai"))
        except Exception:
            now = _dt.datetime.now()
        if language.lower().startswith("chinese"):
            return f"Zotero-arXiv-Daily 每日推荐 · {now.year}年{now.month}月{now.day}日"
        return f"Zotero-arXiv-Daily Daily Digest · {now.year}-{now.month:02d}-{now.day:02d}"

    def _write_run_report(
        self,
        *,
        corpus: int,
        candidates: int,
        ranked: int,
        elapsed: float,
        failures: list[str] | None = None,
    ) -> None:
        try:
            report = {
                "ts": _dt.datetime.now(_dt.UTC).isoformat().replace("+00:00", "Z"),
                "corpus": corpus,
                "candidates": candidates,
                "ranked": ranked,
                "elapsed_s": round(elapsed, 1),
                "source": list(self.config.executor.source),
                "reranker": self.config.executor.reranker,
                "source_failures": failures or [],
            }
            path = self._sent_history_path().parent / "last_run.json"
            path.parent.mkdir(parents=True, exist_ok=True)
            tmp = path.with_suffix(".tmp")
            tmp.write_text(json.dumps(report, indent=2))
            tmp.replace(path)
        except Exception as exc:
            logger.warning(f"Failed to write run report: {exc}")

    # ------------------------------------------------------------------
    # Main pipeline
    # ------------------------------------------------------------------

    @staticmethod
    def _collect_shown_urls(digest, originals: list[Paper], candidate_count: int, ranked: list[Paper]) -> set[str]:
        """URLs of every paper actually rendered in the email (for sent-history).

        The email shows: picked cards + ALL unpicked candidates (the "other
        candidates" block) + any rescued pool paper the agent scored in
        ``others`` — mirror the render logic exactly so nothing shown to the
        reader is re-shown on a later day. ``originals`` is the index space
        the digest refers to; ``ranked`` is the fallback list when there is
        no digest.
        """
        shown: set[str] = set()
        if digest:
            # Rescued pool papers (beyond candidates) the agent scored are
            # rendered in the others block — record them in BOTH branches
            # (a digest with an empty papers list still shows the others
            # block with every unpicked candidate plus rescued papers).
            for o in digest.others or []:
                idx = int(o.get("index", -1))
                if 0 <= idx < len(originals):
                    shown.add(originals[idx].url)
            if digest.papers:
                picked = {
                    dp.index for dp in digest.papers
                    if dp.index is not None and 0 <= dp.index < len(originals)
                }
                for dp in digest.papers:
                    if dp.index is not None and 0 <= dp.index < len(originals):
                        shown.add(originals[dp.index].url)
                # Every unpicked candidate is rendered in the others block —
                # record all of them, not just the ones the agent scored.
                for i in range(min(candidate_count, len(originals))):
                    if i not in picked:
                        shown.add(originals[i].url)
            else:
                # Empty papers list: the others block still lists every
                # candidate (nothing was picked).
                for i in range(min(candidate_count, len(originals))):
                    shown.add(originals[i].url)
        else:
            shown.update(p.url for p in ranked)
        return shown

    def run(self):
        t0 = time.time()
        corpus = self.fetch_zotero_corpus()
        corpus = self.filter_corpus(corpus)
        logger.info(f"[stage:corpus] {len(corpus)} papers after path filters ({time.time() - t0:.1f}s)")
        if len(corpus) == 0:
            # Fail loudly instead of silently skipping the email: an empty corpus
            # almost always means broken Zotero credentials / filters, and the
            # workflow failure notification is the only way the owner finds out.
            logger.error(
                f"No zotero papers found. Please check your Zotero settings:\n"
                f"user_id: {self.config.zotero.get('user_id')}\n"
                f"include_path: {self.config.zotero.get('include_path')}\n"
                f"ignore_path: {self.config.zotero.get('ignore_path')}"
            )
            self._write_run_report(
                corpus=0, candidates=0, ranked=0, elapsed=time.time() - t0, failures=[]
            )
            raise RuntimeError(
                "No Zotero papers found — check ZOTERO_ID / ZOTERO_KEY and include_path filters."
            )

        all_papers: list[Paper] = []
        source_failures: list[str] = []
        for source, retriever in self.retrievers.items():
            logger.info(f"Retrieving {source} papers...")
            t_ret = time.time()
            try:
                papers = retriever.retrieve_papers()
            except Exception as exc:
                logger.warning(f"{source} retrieval failed ({exc}); continuing with other sources")
                source_failures.append(source)
                continue
            if len(papers) == 0:
                logger.info(f"No {source} papers found")
                continue
            logger.info(f"Retrieved {len(papers)} {source} papers ({time.time() - t_ret:.1f}s)")
            all_papers.extend(papers)

        n_before_dedupe = len(all_papers)
        all_papers = self._dedupe_papers(all_papers)
        n_after = len(all_papers)
        logger.info(f"[stage:retrieve] {n_before_dedupe} raw -> {n_after} after dedupe ({time.time() - t0:.1f}s)")

        ranked: list[Paper] = []
        if all_papers:
            logger.info("Reranking papers (embedding + BM25)...")
            t_rr = time.time()
            try:
                ranked = self.reranker.rerank(all_papers, corpus)
                self._rerank_failed = False
            except Exception as exc:
                # Reranker failure (provider down, rate limit, local model
                # load error) must never kill the daily email: degrade to
                # fetch order with no scores; the render layer handles
                # score=None and min_score is skipped so nothing is silently
                # dropped on a degraded run.
                logger.error(f"Reranker failed ({exc}); continuing with fetch order and no scores")
                ranked = list(all_papers)
                self._rerank_failed = True
            logger.info(f"[stage:rerank] {len(ranked)} scored ({time.time() - t_rr:.1f}s)")
            ranked = self._filter_min_score(ranked)
            ranked = self._filter_keywords(ranked)
            ranked = self._filter_sent_history(ranked)
            cap = int(self.config.executor.get("max_paper_num", 100))
            ranked = ranked[:cap]
            logger.info(f"[stage:filter] {len(ranked)} candidates survive filters (max_paper_num={cap})")
            if ranked:
                top = ranked[0]
                top_score = f"{top.score:.2f}" if top.score is not None else "?"
                logger.info(f"[stage:filter] top candidate: score={top_score} {top.title}")
            # Best-effort fetch full text for top candidates into the shared
            # disk cache (up to full_text_budget). Both engines read it: the
            # Pi agent's fetch_full_text checks the cache first, and the
            # Python harness's inspect_paper uses it for on-demand reads.
            self._maybe_fetch_full_texts(ranked)

        if not ranked and not self.config.executor.send_empty:
            logger.info("No qualifying papers found. No email will be sent.")
            self._write_run_report(
                corpus=len(corpus), candidates=len(all_papers),
                ranked=0, elapsed=time.time() - t0, failures=source_failures,
            )
            return

        logger.info("Harness agent producing digest...")
        t_agent = time.time()
        # Reset the Pi-pool marker from any previous run on this instance.
        self._pi_pool = None
        self._pi_candidate_count = None
        # The pool is ALL deduplicated papers from today's fetch — but still
        # filtered by sent-history: papers already shown in previous emails are
        # excluded so the agent cannot re-recommend yesterday's picks (the
        # pipeline-level dedupe below would not catch them since they were
        # never in ``ranked``). Debug/reset-history mode bypasses this.
        pool = self._filter_sent_history(all_papers)
        digest = self._agent_digest(ranked, corpus, pool=pool)
        logger.info(f"[stage:agent] digest produced in {time.time() - t_agent:.1f}s")

        # Digest indexes refer to the FULL pool when the Pi agent succeeded
        # (candidates first, rest after); otherwise they refer to ``ranked``
        # (Python harness / fallback). Render against whichever space the
        # digest actually uses, so rescued non-candidate papers show properly.
        originals = self._pi_pool if self._pi_pool is not None else ranked
        # Candidate count follows the digest's index space: the post-cap count
        # when the Pi agent succeeded, the ranked-list length otherwise.
        candidate_count = (
            self._pi_candidate_count if self._pi_pool is not None else len(ranked)
        )

        # Decide which papers were actually recommended (for sent-history).
        if digest and digest.papers:
            selected_indices = [
                p.index for p in digest.papers
                if p.index is not None and 0 <= p.index < len(originals)
            ]
            selected_papers = [originals[i] for i in selected_indices] or ranked
        else:
            selected_papers = ranked

        # Fixed subject format (repo + daily + date); the agent's free-style
        # subject is discarded for a stable, scannable inbox. Set it BEFORE
        # rendering so the HTML <title> and preheader match the email header.
        subject = self._digest_subject()
        if digest:
            digest.subject = subject

        logger.info("Rendering email...")
        language = (self.config.llm or {}).get("language", "English")
        html_content = render_email(
            digest, originals=originals, language=language,
            candidate_count=candidate_count, failures=source_failures,
        )

        # Archive the rendered email for debugging / review (also uploaded as
        # a workflow artifact so we can inspect what the agent produced).
        try:
            cache_dir = Path(self.config.executor.get("cache_dir") or ".cache")
            cache_dir.mkdir(parents=True, exist_ok=True)
            (cache_dir / "last_email.html").write_text(html_content, encoding="utf-8")
        except Exception as exc:
            logger.warning(f"Failed to archive rendered email: {exc}")

        logger.info("Delivering digest...")
        delivered = False
        try:
            if self.config.executor.debug:
                # Debug mode never sends: it exists for local preview / CI review
                # of the rendered HTML (archived above and uploaded as an
                # artifact). Sending from a debug run would duplicate the daily
                # email to the real inbox.
                logger.info("Debug mode: skipping delivery (rendered HTML archived at last_email.html)")
            else:
                delivered = self._deliver(html_content, subject=subject)
        finally:
            # sent-history + run report are written even when delivery fails
            # (the exception propagates after the report) so a failed run stays
            # diagnosable — but papers are only marked sent when they were
            # actually delivered, so a missed email is retried tomorrow.
            if ranked and delivered and self.config.executor.get("dedupe_history", True):
                sent = self._load_sent_history()
                shown = self._collect_shown_urls(digest, originals, candidate_count, ranked)
                sent.update(shown)
                self._save_sent_history(sent)

            logger.info(
                f"[summary] corpus={len(corpus)} candidates={len(all_papers)} "
                f"ranked={len(ranked)} selected={len(selected_papers)} elapsed={time.time() - t0:.1f}s"
            )
            self._write_run_report(
                corpus=len(corpus), candidates=len(all_papers),
                ranked=len(ranked), elapsed=time.time() - t0,
                failures=source_failures,
            )
            if delivered:
                logger.info("Email sent successfully")
