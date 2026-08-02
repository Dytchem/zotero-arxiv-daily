"""LLM Harness — the smart reranking stage of the daily digest.

Two-stage recommendation pipeline:

  Stage 1 (cheap, existing): embedding similarity + BM25 hybrid rerank
      narrows the day's candidates to a manageable ``top_k``.
  Stage 2 (this module): an LLM reads a *research profile* distilled from
      the user's Zotero library, then scores each candidate (0-10) with a
      one-line rationale. Scores reorder the final digest and the rationale
      is shown in the email card.

The profile is cached (keyed by a hash of the corpus) so the LLM only
re-distills it when the library actually changes — the common daily case
is a pure rerank of fresh candidates.

Every LLM call here is best-effort: on any failure the harness logs and
returns the input unchanged, so the pipeline degrades gracefully to the
Stage-1 ranking instead of breaking the daily email.
"""

from __future__ import annotations

import hashlib
import json
import re
from dataclasses import dataclass, field
from pathlib import Path

from loguru import logger
from omegaconf import DictConfig
from openai import OpenAI

from .protocol import CorpusPaper, Paper

# ----------------------------------------------------------------------
# Data
# ----------------------------------------------------------------------


@dataclass
class ResearchProfile:
    """LLM-distilled description of the user's research interests."""

    topics: list[str] = field(default_factory=list)
    keywords: list[str] = field(default_factory=list)
    methods: list[str] = field(default_factory=list)
    summary: str = ""


@dataclass
class HarnessScore:
    """LLM judgement for one candidate paper."""

    index: int          # position in the candidate list passed to the LLM
    score: float        # 0-10 relevance to the user's profile
    reason: str         # one-line why-this-paper rationale (shown in email)


# ----------------------------------------------------------------------
# Prompt helpers
# ----------------------------------------------------------------------


def _corpus_hash(corpus: list[CorpusPaper]) -> str:
    """Content hash of the corpus — used as the profile cache key."""
    h = hashlib.sha256()
    for c in corpus:
        h.update(c.title.encode("utf-8", errors="ignore"))
        h.update(c.abstract.encode("utf-8", errors="ignore"))
        h.update(c.added_date.isoformat().encode("utf-8", errors="ignore"))
    return h.hexdigest()


def _truncate(text: str, limit: int) -> str:
    if len(text) <= limit:
        return text
    return text[:limit] + "…"


def _profile_prompt(corpus: list[CorpusPaper], language: str) -> str:
    """Ask the LLM to distill the Zotero library into a research profile.

    Only the most recent papers are included (newest additions best
    reflect current interests); each abstract is truncated to keep the
    prompt inside the model's context window.
    """
    recent = sorted(corpus, key=lambda c: c.added_date, reverse=True)[:40]
    entries = []
    for i, c in enumerate(recent, 1):
        entries.append(
            f"{i}. [{c.added_date.date()}] {c.title}\n"
            f"   Paths: {', '.join(c.paths) if c.paths else '-'}\n"
            f"   Abstract: {_truncate(c.abstract, 300)}"
        )
    return (
        f"You are a research librarian building a profile of a scientist's interests. "
        f"Below are the {len(recent)} most recent papers from their Zotero library "
        f"(title, collection paths, abstract).\n\n"
        + "\n".join(entries)
        + "\n\n"
        f"Distill this library into a research profile. Respond with STRICT JSON only, "
        f"no markdown, in this exact shape:\n"
        f'{{"topics": ["...", "..."], "keywords": ["...", "..."], '
        f'"methods": ["...", "..."], "summary": "one paragraph describing research interests in {language}"}}\n'
        f"Rules: 4-8 topics, 8-15 keywords, 3-8 methods, summary max 120 words in {language}."
    )


def _rerank_prompt(profile: ResearchProfile, candidates: list[Paper], language: str) -> str:
    """Ask the LLM to score each candidate against the research profile."""
    entries = []
    for i, c in enumerate(candidates, 1):
        authors = ", ".join(c.authors[:3]) + (" et al." if len(c.authors) > 3 else "")
        entries.append(
            f"{i}. {c.title} ({authors})\n"
            f"   Abstract: {_truncate(c.abstract, 250)}"
        )
    profile_text = (
        f"Topics: {', '.join(profile.topics)}\n"
        f"Keywords: {', '.join(profile.keywords)}\n"
        f"Methods: {', '.join(profile.methods)}\n"
        f"Summary: {profile.summary}"
    )
    return (
        f"You are a research recommender. The user's research profile:\n{profile_text}\n\n"
        f"Score each candidate paper below by relevance to this profile. "
        f"Respond with STRICT JSON only: an array of objects, one per paper, in the same order:\n"
        f'[{{"index": 1, "score": 7.5, "reason": "one-line rationale in {language}"}}, ...]\n'
        f"Rules: score 0-10 (10 = must read, 0 = irrelevant); reason concise, in {language}; "
        f"no markdown, no trailing commas.\n\nCandidates:\n" + "\n".join(entries)
    )


# ----------------------------------------------------------------------
# Harness
# ----------------------------------------------------------------------


class LLMHarness:
    """Two-stage LLM reranking on top of the cheap embedding rerank.

    Uses its own API provider (OpenRouter by default — same provider as the
    ``reranker.api``) with a strong reasoning model (e.g. ``gpt-5.6-luna``),
    independent from the TLDR generation provider (``llm.api``, e.g. Ollama).
    """

    def __init__(self, config: DictConfig):
        self.config = config
        harness_cfg = config.llm.get("harness") or {}
        self.enabled = bool(harness_cfg.get("enabled", False))
        self.top_k = int(harness_cfg.get("top_k", 100))
        self.batch_size = int(harness_cfg.get("batch_size", 25))
        self.language = config.llm.get("language", "English")
        self.cache_dir = Path(config.executor.get("cache_dir") or ".cache")

        # Fully independent provider entry (key/base_url/model), NOT shared
        # with reranker.api or llm.api. Keeps each stage free to point at
        # its own provider (e.g. OpenRouter for rerank+harness, Ollama for
        # TLDR). Missing credentials degrade to disabled (embedding order).
        api_cfg = harness_cfg.get("api") or {}
        self.api_key = api_cfg.get("key")
        self.api_base = api_cfg.get("base_url")
        self.model = api_cfg.get("model")
        if not (self.api_key and self.api_base and self.model):
            logger.warning(
                "llm.harness.api is incomplete (key/base_url/model); "
                "LLM Harness will stay disabled and embedding order is kept"
            )
            self.enabled = False
            self.client = None
            self.generation_kwargs = {}
            return
        self.client = OpenAI(api_key=self.api_key, base_url=self.api_base)
        self.generation_kwargs = dict(config.llm.get("generation_kwargs") or {})
        self.generation_kwargs["model"] = self.model

    # -- profile -------------------------------------------------------

    def _profile_cache_path(self) -> Path:
        return self.cache_dir / "research_profile.json"

    def _profile_cache_key(self, corpus: list[CorpusPaper]) -> str:
        return _corpus_hash(corpus)

    def _load_cached_profile(self, key: str) -> ResearchProfile | None:
        path = self._profile_cache_path()
        if not path.exists():
            return None
        try:
            data = json.loads(path.read_text())
            if data.get("key") != key:
                return None
            return ResearchProfile(
                topics=data.get("topics", []),
                keywords=data.get("keywords", []),
                methods=data.get("methods", []),
                summary=data.get("summary", ""),
            )
        except Exception as exc:
            logger.warning(f"Failed to load cached research profile: {exc}")
            return None

    def _save_cached_profile(self, key: str, profile: ResearchProfile) -> None:
        try:
            path = self._profile_cache_path()
            path.parent.mkdir(parents=True, exist_ok=True)
            tmp = path.with_suffix(".tmp")
            tmp.write_text(json.dumps({
                "key": key,
                "topics": profile.topics,
                "keywords": profile.keywords,
                "methods": profile.methods,
                "summary": profile.summary,
            }))
            tmp.replace(path)
        except Exception as exc:
            logger.warning(f"Failed to save research profile cache: {exc}")

    def build_profile(self, corpus: list[CorpusPaper]) -> ResearchProfile | None:
        """Distill the Zotero library into a research profile (cached by corpus hash)."""
        key = self._profile_cache_key(corpus)
        cached = self._load_cached_profile(key)
        if cached is not None:
            logger.info("Using cached research profile (corpus unchanged)")
            return cached

        prompt = _profile_prompt(corpus, self.language)
        try:
            response = self.client.chat.completions.create(
                messages=[
                    {"role": "system", "content": "You output valid JSON only."},
                    {"role": "user", "content": prompt},
                ],
                **self.generation_kwargs,
            )
            content = response.choices[0].message.content or "{}"
            data = _extract_json(content)
            profile = ResearchProfile(
                topics=data.get("topics", []),
                keywords=data.get("keywords", []),
                methods=data.get("methods", []),
                summary=data.get("summary", ""),
            )
            if not profile.topics and not profile.keywords:
                logger.warning("LLM returned an empty research profile; treating as failure")
                return None
            self._save_cached_profile(key, profile)
            logger.info(
                f"Built research profile: {len(profile.topics)} topics, "
                f"{len(profile.keywords)} keywords, {len(profile.methods)} methods"
            )
            return profile
        except Exception as exc:
            logger.warning(f"Failed to build research profile: {exc}")
            return None

    # -- rerank --------------------------------------------------------

    def rerank(self, candidates: list[Paper], profile: ResearchProfile) -> list[Paper]:
        """LLM scores candidates; returns the reordered list (failure = input order)."""
        if not candidates:
            return candidates
        scores: dict[int, float] = {}
        reasons: dict[int, str] = {}
        for start in range(0, len(candidates), self.batch_size):
            batch = candidates[start:start + self.batch_size]
            try:
                response = self.client.chat.completions.create(
                    messages=[
                        {"role": "system", "content": "You output valid JSON only."},
                        {"role": "user", "content": _rerank_prompt(profile, batch, self.language)},
                    ],
                    **self.generation_kwargs,
                )
                content = response.choices[0].message.content or "[]"
                items = _extract_json(content)
                if not isinstance(items, list):
                    raise ValueError("LLM rerank response is not a JSON array")
                for item in items:
                    idx = int(item.get("index", 0)) - 1  # 1-based from prompt
                    if 0 <= idx < len(batch):
                        scores[start + idx] = float(item.get("score", 0))
                        reasons[start + idx] = str(item.get("reason", ""))
            except Exception as exc:
                logger.warning(f"LLM rerank batch failed ({exc}); keeping embedding order")
                return candidates
        if not scores:
            logger.warning("LLM rerank returned no scores; keeping embedding order")
            return candidates
        for i, paper in enumerate(candidates):
            if i in scores:
                paper.score = round(scores[i], 1)
                paper.recommend_reason = reasons.get(i)
        ranked = sorted(candidates, key=lambda p: p.score if p.score is not None else -1, reverse=True)
        logger.info(f"LLM rerank scored {len(scores)}/{len(candidates)} candidates")
        return ranked


def _extract_json(content: str) -> object:
    """Parse JSON from an LLM reply, tolerating markdown fences / prose."""
    text = content.strip()
    if text.startswith("```"):
        text = re.sub(r"^```(?:json)?\s*", "", text)
        text = re.sub(r"\s*```$", "", text)
    try:
        return json.loads(text)
    except json.JSONDecodeError:
        # Fall back to the first [...] or {...} block in the reply
        match = re.search(r"[\[{].*[\]}]", text, flags=re.DOTALL)
        if match:
            return json.loads(match.group(0))
        raise
