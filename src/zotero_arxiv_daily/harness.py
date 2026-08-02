"""LLM Harness — a single autonomous agent that produces the daily digest.

Instead of a rigid pipeline (embedding rerank -> per-paper TLDR/affiliations ->
hard-coded HTML template), this module runs ONE agent loop. The agent:

  1. reads a research profile distilled from the user's Zotero library,
  2. inspects the day's embedding-ranked candidates (its own tools),
  3. decides which papers to recommend and why,
  4. writes the complete email: subject, intro, per-paper cards, outro.

The pipeline *feeds* the agent the cheap stuff (embedding+BM25 vector order),
but every editorial decision — what to include, how to phrase each reason,
how to structure the mail — is the agent's job.

The agent ends by calling ``submit_digest`` with a structured JSON payload.
A thin, safe render layer (``construct_email``) turns that payload into HTML,
so the agent never touches raw markup and the pipeline can never be broken by
a stray ``$\\alpha$`` or a malformed link.

Failure handling: any LLM error, missing credentials, or malformed reply
degrades gracefully to the embedding order (no digest -> fallback rendering),
so the daily email always goes out.

Only ONE LLM provider is used (``llm.api``, e.g. OpenRouter ``gpt-5.6-luna``).
The legacy per-paper TLDR / affiliations providers are gone.
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
class DigestPaper:
    """One recommended paper as decided by the agent."""

    index: int  # position in the candidate list passed to the agent
    reason: str  # the agent's why-this-paper rationale (shown in the email)
    tldr: str = ""  # optional one-line takeaway


@dataclass
class Digest:
    """The agent's complete editorial output — becomes the email."""

    subject: str
    intro: str
    papers: list[DigestPaper] = field(default_factory=list)
    sections: list[dict] = field(default_factory=list)  # optional grouping
    outro: str = ""


@dataclass
class ResearchProfile:
    """LLM-distilled description of the user's research interests."""

    topics: list[str] = field(default_factory=list)
    keywords: list[str] = field(default_factory=list)
    methods: list[str] = field(default_factory=list)
    summary: str = ""


# ----------------------------------------------------------------------
# Small helpers
# ----------------------------------------------------------------------


def _truncate(text: str, limit: int) -> str:
    if text is None:
        return ""
    return text if len(text) <= limit else text[:limit] + "…"


def _today_str() -> str:
    """Today's date in the user's timezone (Asia/Shanghai), e.g. '2026-08-02 (Sunday)'."""
    try:
        from datetime import datetime
        from zoneinfo import ZoneInfo

        now = datetime.now(ZoneInfo("Asia/Shanghai"))
        return now.strftime("%Y-%m-%d (%A)")
    except Exception:
        from datetime import datetime

        return datetime.now().strftime("%Y-%m-%d")


def _extract_json(content: str) -> object:
    """Parse JSON from an LLM reply, tolerating markdown fences / prose."""
    text = (content or "").strip()
    if text.startswith("```"):
        text = re.sub(r"^```(?:json)?\s*", "", text)
        text = re.sub(r"\s*```$", "", text)
    try:
        return json.loads(text)
    except json.JSONDecodeError:
        match = re.search(r"[\[{].*[\]}]", text, flags=re.DOTALL)
        if match:
            return json.loads(match.group(0))
        raise


def _authors_line(paper: Paper) -> str:
    if not paper.authors:
        return ""
    head = ", ".join(paper.authors[:3])
    return head + (" et al." if len(paper.authors) > 3 else "")


def _profile_prompt(corpus: list[CorpusPaper], language: str) -> str:
    """Ask the LLM to distill the Zotero library into a research profile."""
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


# ----------------------------------------------------------------------
# The agent
# ----------------------------------------------------------------------


class HarnessAgent:
    """A single tool-using agent that produces the daily Digest.

    The agent builds the research profile (cached), inspects candidates with
    its own tools, then calls ``submit_digest`` with the finished editorial
    output. On any failure it falls back to a plain embedding-order digest.
    """

    def __init__(self, config: DictConfig):
        self.config = config
        llm_cfg = config.llm or {}
        api_cfg = llm_cfg.get("api") or {}
        self.language = llm_cfg.get("language", "English")
        self.top_k = int((llm_cfg.get("harness") or {}).get("top_k", 100))
        self.cache_dir = Path(config.executor.get("cache_dir") or ".cache")

        self.api_key = api_cfg.get("key")
        self.api_base = api_cfg.get("base_url")
        self.model = (llm_cfg.get("generation_kwargs") or {}).get("model") or api_cfg.get("model")
        self.enabled = bool(llm_cfg.get("harness", {}).get("enabled", False))

        if not (self.enabled and self.api_key and self.api_base and self.model):
            logger.warning(
                "llm (harness) is disabled or incomplete (key/base_url/model); "
                "falling back to embedding-order digest"
            )
            self.client = None
        else:
            self.client = OpenAI(api_key=self.api_key, base_url=self.api_base)
            self.generation_kwargs = dict(llm_cfg.get("generation_kwargs") or {})
            self.generation_kwargs["model"] = self.model

    # -- research profile (cached) ------------------------------------

    def _profile_cache_path(self) -> Path:
        return self.cache_dir / "research_profile.json"

    def _profile_cache_key(self, corpus: list[CorpusPaper]) -> str:
        return self._corpus_hash(corpus)

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
        if self.client is None:
            return None
        key = self._corpus_hash(corpus)
        cached = self._load_cached_profile(key)
        if cached is not None:
            logger.info("Using cached research profile (corpus unchanged)")
            return cached
        try:
            response = self.client.chat.completions.create(
                messages=[
                    {"role": "system", "content": "You output valid JSON only."},
                    {"role": "user", "content": _profile_prompt(corpus, self.language)},
                ],
                **self.generation_kwargs,
            )
            data = _extract_json(response.choices[0].message.content or "{}")
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

    def _corpus_hash(self, corpus: list[CorpusPaper]) -> str:
        h = hashlib.sha256()
        for c in corpus:
            h.update(c.title.encode("utf-8", errors="ignore"))
            h.update(c.abstract.encode("utf-8", errors="ignore"))
            h.update(c.added_date.isoformat().encode("utf-8", errors="ignore"))
        return h.hexdigest()

    # -- tools exposed to the agent -----------------------------------

    def _tool_defs(self, candidate_count: int) -> list[dict]:
        return [
            {
                "type": "function",
                "function": {
                    "name": "inspect_candidates",
                    "description": (
                        "List the day's candidate papers with their embedding relevance "
                        "score and a short abstract. Call this to see what is available "
                        "before deciding what to recommend."
                    ),
                    "parameters": {
                        "type": "object",
                        "properties": {
                            "start": {"type": "integer", "description": "0-based start index"},
                            "count": {"type": "integer", "description": "how many to show (max 20)"},
                        },
                        "required": ["start"],
                    },
                },
            },
            {
                "type": "function",
                "function": {
                    "name": "inspect_paper",
                    "description": (
                        "Show the full abstract (and preview of full text, if already "
                        "fetched) for one candidate by its index."
                    ),
                    "parameters": {
                        "type": "object",
                        "properties": {"index": {"type": "integer"}},
                        "required": ["index"],
                    },
                },
            },
            {
                "type": "function",
                "function": {
                    "name": "submit_digest",
                    "description": (
                        "Submit the final digest: your editorial decision about which "
                        "papers to recommend and the full email content. Call this once "
                        "when done. It ends the loop."
                    ),
                    "parameters": {
                        "type": "object",
                        "properties": {
                            "subject": {"type": "string", "description": "email subject line"},
                            "intro": {"type": "string", "description": "opening paragraph"},
                            "papers": {
                                "type": "array",
                                "description": "papers you recommend, by candidate index",
                                "items": {
                                    "type": "object",
                                    "properties": {
                                        "index": {"type": "integer"},
                                        "reason": {
                                            "type": "string",
                                            "description": "why this paper matters to the user",
                                        },
                                        "tldr": {"type": "string", "description": "optional one-line takeaway"},
                                    },
                                    "required": ["index", "reason"],
                                },
                            },
                            "outro": {"type": "string", "description": "closing paragraph"},
                        },
                        "required": ["subject", "intro", "papers", "outro"],
                    },
                },
            },
        ]

    # -- digest generation ---------------------------------------------

    def generate(self, candidates: list[Paper], corpus: list[CorpusPaper]) -> Digest | None:
        """Run the agent loop and return the Digest. None on any failure."""
        if self.client is None or not candidates:
            return None
        profile = self.build_profile(corpus)
        if profile is None:
            return None

        profile_text = (
            f"Topics: {', '.join(profile.topics)}\n"
            f"Keywords: {', '.join(profile.keywords)}\n"
            f"Methods: {', '.join(profile.methods)}\n"
            f"Summary: {profile.summary}"
        )

        system_prompt = (
            "You are an elite research-recommendation agent. Your job is to read a "
            "scientist's research profile, inspect the day's candidate papers, and "
            "produce a high-quality daily digest email.\n\n"
            f"Research profile:\n{profile_text}\n\n"
            f"Today: {_today_str()}. The candidates are the newest papers from the "
            "user's subscribed feeds (on weekends/holidays arXiv publishes nothing, "
            "so the feed may roll back a few days — say so in the intro if so).\n\n"
            "Tasks:\n"
            "1. Use inspect_candidates to survey the day's papers (embedding score 0-10 "
            "is a hint, not a command — use your judgement).\n"
            "2. Use inspect_paper on any paper you are unsure about.\n"
            "3. Decide which papers to recommend. Prefer a focused set of genuinely "
            "relevant papers (typically 3-6) over a huge dump: quality over quantity. "
            "Every pick must earn its place; unpicked candidates will still be listed "
            "separately at the bottom of the email, so you are free to be selective. "
            "A good reason must connect the paper to the user's actual interests — "
            "say what the paper contributes and why it matters to this specific "
            "researcher, not a generic abstract paraphrase. Keep each reason compact "
            "(2-4 sentences); skip filler.\n"
            "4. Write the digest in " + self.language + ". The subject should be "
            "short, informative, and in the same language; the intro should give "
            "context (what today's batch looks like overall); the outro should sign "
            "off warmly and look ahead.\n"
            "5. Call submit_digest with the finished subject, intro, per-paper "
            "recommendation reasons, and outro.\n\n"
            "Quality bar: reasons should be specific and insightful, not generic. "
            "Never invent content that is not in the paper's abstract or full text. "
            "If nothing is worth recommending, submit an empty papers list with an "
            "honest intro."
        )

        messages = [{"role": "system", "content": system_prompt}]
        digest: Digest | None = None
        max_steps = 12

        # Keep full texts of inspected papers for tools.
        for step in range(max_steps):
            try:
                response = self.client.chat.completions.create(
                    model=self.model,
                    messages=messages,
                    tools=self._tool_defs(len(candidates)),
                    tool_choice="auto",
                )
            except Exception as exc:
                logger.warning(f"LLM harness call failed at step {step}: {exc}")
                return None

            msg = response.choices[0].message
            if not msg.tool_calls:
                # No tool call — nudge the model (or bail after a couple tries).
                messages.append({"role": "assistant", "content": msg.content or ""})
                messages.append({
                    "role": "user",
                    "content": "Please either continue inspecting or call submit_digest to finish.",
                })
                continue

            messages.append(msg)
            for tc in msg.tool_calls:
                name = tc.function.name
                args = json.loads(tc.function.arguments or "{}")
                if name == "submit_digest":
                    digest = self._digest_from_args(args, len(candidates))
                    # Acknowledge and finish.
                    messages.append({
                        "role": "tool",
                        "tool_call_id": tc.id,
                        "content": "Digest received.",
                    })
                    return digest
                elif name == "inspect_candidates":
                    start = int(args.get("start", 0))
                    count = min(int(args.get("count", 20)), 20)
                    result = self._describe_candidates(candidates, start, count)
                    messages.append({
                        "role": "tool",
                        "tool_call_id": tc.id,
                        "content": result,
                    })
                elif name == "inspect_paper":
                    idx = int(args.get("index", -1))
                    result = self._describe_paper(candidates, idx)
                    messages.append({
                        "role": "tool",
                        "tool_call_id": tc.id,
                        "content": result,
                    })
                else:
                    messages.append({
                        "role": "tool",
                        "tool_call_id": tc.id,
                        "content": "Unknown tool.",
                    })

        logger.warning("Harness agent reached max steps without submitting a digest")
        return digest

    def _describe_candidates(self, candidates: list[Paper], start: int, count: int) -> str:
        if not candidates:
            return "No candidates."
        lines = []
        for i in range(start, min(start + count, len(candidates))):
            p = candidates[i]
            score = round(p.score, 1) if p.score is not None else "?"
            lines.append(
                f"[{i}] {p.title} | embedding={score}/10 | {p.source}\n"
                f"    {_authors_line(p)}\n"
                f"    Abstract: {_truncate(p.abstract, 220)}"
            )
        return "\n".join(lines) if lines else f"Index out of range (0..{len(candidates)-1})."

    def _describe_paper(self, candidates: list[Paper], index: int) -> str:
        if not (0 <= index < len(candidates)):
            return f"Index out of range (0..{len(candidates)-1})."
        p = candidates[index]
        body = p.full_text or p.abstract or ""
        return (
            f"[{index}] {p.title}\n"
            f"Authors: {_authors_line(p)}\n"
            f"URL: {p.url}\n"
            f"Full abstract:\n{p.abstract}\n"
            f"Full-text preview:\n{_truncate(body, 4000)}"
        )

    @staticmethod
    def _digest_from_args(args: dict, candidate_count: int) -> Digest:
        papers = []
        for item in args.get("papers") or []:
            try:
                idx = int(item.get("index", -1))
            except (TypeError, ValueError):
                idx = -1
            papers.append(
                DigestPaper(
                    index=idx,
                    reason=str(item.get("reason", "")),
                    tldr=str(item.get("tldr", "")),
                )
            )
        return Digest(
            subject=str(args.get("subject", "")),
            intro=str(args.get("intro", "")),
            papers=papers,
            sections=args.get("sections") or [],
            outro=str(args.get("outro", "")),
        )

    # -- fallback -------------------------------------------------------

    @staticmethod
    def fallback_digest(candidates: list[Paper], max_papers: int) -> Digest:
        """Plain embedding-order digest used when the agent can't run."""
        papers = [
            DigestPaper(index=i, reason=_truncate(p.recommend_reason or "", 200))
            for i, p in enumerate(candidates[:max_papers])
        ]
        return Digest(
            subject="Daily paper digest",
            intro="Here is today's selection, ordered by relevance to your library.",
            papers=papers,
            sections=[],
            outro="Enjoy reading!",
        )
