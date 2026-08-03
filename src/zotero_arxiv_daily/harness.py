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
    work_score: float | None = None  # LLM quality judgement 0-10 (rigour / novelty / provenance)


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
    """LLM-distilled description of the user's research interests and taste."""

    topics: list[str] = field(default_factory=list)
    keywords: list[str] = field(default_factory=list)
    methods: list[str] = field(default_factory=list)
    summary: str = ""
    taste: str = ""  # distilled research taste: rigour bar, venue/provenance expectations, style


@dataclass
class Evaluation:
    """Structured feedback from the independent evaluator agent."""

    score: float = 0.0
    issues: list[dict] = field(default_factory=list)
    verdict: str = "revise"  # "approve" | "revise"


# ----------------------------------------------------------------------
# Small helpers
# ----------------------------------------------------------------------


def _truncate(text: str, limit: int) -> str:
    if text is None:
        return ""
    return text if len(text) <= limit else text[:limit] + "…"


def _parse_work_score(value) -> float | None:
    """Parse a work_score from LLM args; None when absent/invalid."""
    if value is None:
        return None
    try:
        score = float(value)
    except (TypeError, ValueError):
        return None
    return min(max(score, 0.0), 10.0)


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
        f'"methods": ["...", "..."], "summary": "one paragraph describing research interests in {language}", '
        f'"taste": "a short paragraph (in {language}) describing the researcher\'s taste and quality bar: '
        f'how rigorous/selective they are, which venues/provenance they trust, what kind of work they tend '
        f'to value (theory vs application, depth vs breadth), and what they likely consider low-quality or '
        f'watery work"}}, \n'
        f'Rules: 4-8 topics, 8-15 keywords, 3-8 methods, summary max 120 words in {language}, '
        f'taste max 100 words in {language}. Infer the taste from the library itself; if the library is '
        f'small or ambiguous, state the most reasonable default for a careful researcher.'
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

    def __init__(self, config: DictConfig, full_text_fetcher=None):
        self.config = config
        # Callable[[Paper], str | None]: lazily fetch full text (PDF) on demand
        # when the agent inspects a paper that has none. Injected by the
        # executor so the agent can actually read paper content, not just metadata.
        self.full_text_fetcher = full_text_fetcher
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
            # schema bump invalidates caches written before the taste field
            if data.get("key") != key or data.get("schema") != 2:
                return None
            return ResearchProfile(
                topics=data.get("topics", []),
                keywords=data.get("keywords", []),
                methods=data.get("methods", []),
                summary=data.get("summary", ""),
                taste=data.get("taste", ""),
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
                "schema": 2,
                "key": key,
                "topics": profile.topics,
                "keywords": profile.keywords,
                "methods": profile.methods,
                "summary": profile.summary,
                "taste": profile.taste,
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
                taste=data.get("taste", ""),
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
                        "before deciding what to recommend. Page through with start/count."
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
                        "Show the full content of one candidate by its index: authors, "
                        "affiliations when available, abstract, and the paper's full "
                        "text (fetched from the PDF on demand if not already loaded). "
                        "Use this to actually READ a paper before judging it — judge "
                        "the work's quality from its content, not just the title."
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
                    "name": "search_candidates",
                    "description": (
                        "Filter the candidate list by keywords (title + abstract substring "
                        "match, case-insensitive). Returns only the matching papers. Use "
                        "this to focus on a topic, e.g. \"quantum\" or \"nonadiabatic\"."
                    ),
                    "parameters": {
                        "type": "object",
                        "properties": {"query": {"type": "string", "description": "keyword(s) to match"}},
                        "required": ["query"],
                    },
                },
            },
            {
                "type": "function",
                "function": {
                    "name": "compare_papers",
                    "description": (
                        "Side-by-side view (title, score, abstract) of two candidates so "
                        "you can weigh which one to recommend."
                    ),
                    "parameters": {
                        "type": "object",
                        "properties": {
                            "index_a": {"type": "integer"},
                            "index_b": {"type": "integer"},
                        },
                        "required": ["index_a", "index_b"],
                    },
                },
            },
            {
                "type": "function",
                "function": {
                    "name": "submit_digest",
                    "description": (
                        "Submit the final digest: your editorial decision about which "
                        "papers to recommend and the full email content. You may only "
                        "submit after inspecting at least a few papers. Call this once "
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
                                        "work_score": {
                                            "type": "number",
                                            "description": (
                                                "YOUR quality judgement of the paper's work itself, 0-10: "
                                                "how rigorous, novel and trustworthy it is (method soundness, "
                                                "experimental completeness, author/institution provenance, "
                                                "venue). High embedding relevance does NOT mean high work "
                                                "quality — reject watery/低质 work from weak or unknown "
                                                "institutions. Required for every paper."
                                            ),
                                        },
                                    },
                                    "required": ["index", "reason", "work_score"],
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
        """Run the two-agent loop and return the Digest. None on any failure.

        Generator agent explores and writes a draft; an independent evaluator
        (fresh context, no tools) grades it; if the verdict is ``revise`` and
        revision budget remains, the issues are fed back and the generator
        tries again. An ``approve`` verdict or exhausted budget returns the
        best draft so far.
        """
        if self.client is None or not candidates:
            return None
        # top_k caps how many candidates the agent may see (embedding order is
        # a cheap hint; the agent explores within this window).
        if self.top_k > 0 and len(candidates) > self.top_k:
            logger.info(f"Capping candidates for the agent at top_k={self.top_k} (of {len(candidates)})")
            candidates = candidates[: self.top_k]
        profile = self.build_profile(corpus)
        if profile is None:
            return None

        profile_text = (
            f"Topics: {', '.join(profile.topics)}\n"
            f"Keywords: {', '.join(profile.keywords)}\n"
            f"Methods: {', '.join(profile.methods)}\n"
            f"Summary: {profile.summary}\n"
            f"Taste / quality bar: {profile.taste or 'careful researcher: values rigorous, well-sourced work'}"
        )

        harness_cfg = self.config.llm.get("harness") or {}
        max_steps = int(harness_cfg.get("max_steps", 12))
        min_inspections = int(harness_cfg.get("min_inspections", 3))
        max_revisions = int(harness_cfg.get("max_revisions", 2))
        evaluator_enabled = bool(harness_cfg.get("evaluator_enabled", True))

        digest: Digest | None = None
        feedback: str | None = None
        best_digest: Digest | None = None
        best_score: float = -1.0
        for round_no in range(max_revisions + 1):
            if round_no:
                logger.info(f"Harness revision round {round_no}/{max_revisions}...")
            digest = self._generator_loop(
                candidates, profile_text, max_steps, min_inspections, feedback=feedback
            )
            if digest is None:
                return best_digest or None
            if not evaluator_enabled:
                return digest

            evaluation = self._evaluate(profile_text, candidates, digest)
            if evaluation is None:
                # Evaluator failed — keep the current draft.
                return digest
            # Always track the best-scoring draft so budget exhaustion returns
            # the strongest version, not merely the last one.
            if evaluation.score > best_score:
                best_score = evaluation.score
                best_digest = digest
            if evaluation.verdict == "approve":
                if evaluation.issues:
                    logger.info(
                        f"Evaluator approved (score={evaluation.score}) with "
                        f"{len(evaluation.issues)} minor note(s)"
                    )
                return digest

            logger.warning(
                f"Evaluator scored {evaluation.score}/10, verdict=revise, "
                f"{len(evaluation.issues)} issue(s)"
            )
            feedback = self._feedback_prompt(evaluation)
            if round_no >= max_revisions:
                logger.warning(
                    f"Revision budget exhausted; returning best draft "
                    f"(score={best_score})"
                )
                return best_digest or digest

        return best_digest or digest

    def _generator_loop(
        self,
        candidates: list[Paper],
        profile_text: str,
        max_steps: int,
        min_inspections: int,
        feedback: str | None = None,
    ) -> Digest | None:
        """One generator pass: SURVEY -> DEEP-DIVE -> FOCUS -> DECIDE -> SUBMIT.

        ``feedback`` (optional) carries the evaluator's issues from a previous
        round; when present it is prepended to the user context so the agent
        revises the draft instead of starting from scratch.
        """
        system_prompt = (
            "You are an elite research-recommendation agent. Your job is to read a "
            "scientist's research profile, inspect the day's candidate papers, and "
            "produce a high-quality daily digest email.\n\n"
            f"Research profile:\n{profile_text}\n\n"
            f"Today: {_today_str()}. The candidates are the newest papers from the "
            "user's subscribed feeds (on weekends/holidays arXiv publishes nothing, "
            "so the feed may roll back a few days — say so in the intro if so).\n\n"
            "Workflow (follow it in order, like a careful researcher):\n"
            "1. SURVEY: page through the day's papers with inspect_candidates "
            "(embedding score 0-10 is a hint, not a command). Look at the whole "
            "list, not just the top of the first page.\n"
            "2. DEEP-DIVE: use inspect_paper on at least 3 papers you are seriously "
            "considering. Read their abstracts and full text — inspect_paper shows "
            "the full paper content (fetched from the PDF when needed), so actually "
            "read it to judge the work, not just the title. Do not recommend a "
            "paper you have not inspected.\n"
            "3. FOCUS: use search_candidates to zoom into a topic, and "
            "compare_papers to weigh two candidates against each other when in doubt.\n"
            "4. DECIDE — judge on TWO axes, and trust BOTH:\n"
            "   (a) RELEVANCE: does the paper serve the profile's topics/methods? "
            "The embedding score is a cheap hint; your read of the actual content "
            "is authoritative.\n"
            "   (b) WORK QUALITY (most important — the web is full of watery papers): "
            "score the paper's own merit 0-10 (work_score): is the method sound and "
            "novel, are experiments/evidence complete, are the claims backed by the "
            "content, and — critically — is the provenance credible (known labs / "
            "real institutions / reputable venues) versus unknown or low-credibility "
            "affiliations? A paper can rank high by embedding yet be shallow, padded, "
            "or from a sketchy source — do not be fooled. Drop watery/low-quality "
            "papers even when they look relevant, and never pad the digest with them.\n"
            "   (c) TASTE: the profile's taste line describes what this researcher "
            "actually values. Prefer papers that fit their taste (depth, style, "
            "provenance), not just topic keywords.\n"
            "5. ORDER: the order of the papers array in submit_digest is exactly the "
            "order of the cards in the email. Rank like an experienced researcher "
            "recommending to a colleague: lead with the paper that best combines "
            "(work quality x relevance x taste fit) for THIS reader — strongest "
            "match, most important result, best timing. Do NOT sort by the embedding "
            "score; your editorial judgement rules.\n"
            "6. WRITE: the digest in " + self.language + ". The subject should be "
            "short, informative, and in the same language; the intro should give "
            "context (what today's batch looks like overall); the outro should sign "
            "off warmly and look ahead.\n"
            "IMPORTANT: never refer to papers by their candidate index numbers "
            "(e.g. 'the 3rd paper', '第9篇') in the intro, reasons or outro — the "
            "reader only sees the papers you pick, never the index list, so such "
            "references are meaningless and confusing. Refer to papers by their "
            "titles instead.\n"
            "7. SUBMIT: call submit_digest with the finished subject, intro, per-paper "
            "recommendation reasons, and outro. Every paper MUST carry a work_score "
            "(0-10) reflecting your quality judgement — the reader sees it next to "
            "the relevance badge, so make it honest and defensible. You may only "
            "submit after you have inspected at least 3 papers with inspect_paper; "
            "if you try to submit earlier you will be asked to keep working.\n\n"
            "Quality bar: reasons should be specific and insightful, not generic. "
            "Never invent content that is not in the paper's abstract or full text. "
            "If nothing is worth recommending, submit an empty papers list with an "
            "honest intro."
        )

        messages = [{"role": "system", "content": system_prompt}]
        if feedback:
            messages.append({"role": "user", "content": feedback})

        digest: Digest | None = None
        inspected: set[int] = set()

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
                    if len(inspected) < min(min_inspections, len(candidates)):
                        missing = min(min_inspections, len(candidates)) - len(inspected)
                        messages.append({
                            "role": "tool",
                            "tool_call_id": tc.id,
                            "content": (
                                f"Too early to submit: you have only inspected "
                                f"{len(inspected)} paper(s) with inspect_paper. "
                                f"Inspect at least {min(min_inspections, len(candidates))} "
                                f"({missing} more) before submitting. Use inspect_paper "
                                f"on the candidates you are most likely to recommend."
                            ),
                        })
                        continue
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
                    if 0 <= idx < len(candidates):
                        inspected.add(idx)
                    result = self._describe_paper(candidates, idx)
                    messages.append({
                        "role": "tool",
                        "tool_call_id": tc.id,
                        "content": result,
                    })
                elif name == "search_candidates":
                    result = self._search_candidates(candidates, str(args.get("query", "")))
                    messages.append({
                        "role": "tool",
                        "tool_call_id": tc.id,
                        "content": result,
                    })
                elif name == "compare_papers":
                    a = int(args.get("index_a", -1))
                    b = int(args.get("index_b", -1))
                    result = self._compare_papers(candidates, a, b)
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

    def _evaluate(
        self,
        profile_text: str,
        candidates: list[Paper],
        draft: Digest,
    ) -> Evaluation | None:
        """Independent reviewer: fresh context, no tools, strict JSON verdict.

        Returns None on any failure (caller treats None as 'keep the draft').
        """
        if self.client is None:
            return None
        brief_lines = []
        for i, p in enumerate(candidates):
            score = round(p.score, 1) if p.score is not None else "?"
            brief_lines.append(f"[{i}] {p.title} | embedding={score}/10 | {p.source}")
        candidate_brief = "\n".join(brief_lines) if brief_lines else "(no candidates)"

        draft_lines = [f"Subject: {draft.subject}", f"Intro: {draft.intro}"]
        for dp in draft.papers:
            title = candidates[dp.index].title if 0 <= dp.index < len(candidates) else f"(paper {dp.index})"
            draft_lines.append(f"- [{dp.index}] {title}: {dp.reason}")
        draft_lines.append(f"Outro: {draft.outro}")
        draft_text = "\n".join(draft_lines)

        prompt = (
            "You are a strict, independent reviewer of a daily research-digest "
            "email. You have NOT written it yourself — judge it on its own merits.\n\n"
            f"Researcher profile:\n{profile_text}\n\n"
            f"Candidates available (embedding score is a hint):\n{candidate_brief}\n\n"
            f"Draft digest:\n{draft_text}\n\n"
            "Grade the draft on:\n"
            "- Relevance: do the picks genuinely serve the profile topics/methods?\n"
            "- Work quality (critical): did the generator reject watery/low-quality "
            "papers (weak methods, incomplete evidence, low-credibility provenance) "
            "even when they look relevant? Are the work_score values honest and "
            "defensible (0-10)? Is any recommended paper likely to be 水文/低质?\n"
            "- Taste fit: do the picks match the profile's taste line (depth, style, "
            "provenance expectations), not just topic keywords?\n"
            "- Specificity: is each reason concrete and tied to the paper, or a generic paraphrase?\n"
            "- Coverage: are any highly-relevant candidates wrongly ignored?\n"
            "- Language/format: consistent language, no index-number references, sane length.\n\n"
            "Respond with STRICT JSON only, no markdown, in this exact shape:\n"
            '{"score": 0.0-10.0, "issues": [{"severity": "high|medium|low", '
            '"problem": "...", "suggestion": "..."}], "verdict": "approve|revise"}\n'
            "Rules: verdict 'approve' only when the draft is clearly good; "
            "any high-severity issue forces 'revise'. List at most 5 issues, "
            "most important first. Be specific and actionable."
        )

        try:
            response = self.client.chat.completions.create(
                messages=[
                    {"role": "system", "content": "You output valid JSON only."},
                    {"role": "user", "content": prompt},
                ],
                **self.generation_kwargs,
            )
            data = _extract_json(response.choices[0].message.content or "{}")
            verdict = str(data.get("verdict", "revise")).lower()
            score = float(data.get("score", 0.0))
            issues = data.get("issues") or []
            if not isinstance(issues, list):
                issues = []
            return Evaluation(
                score=score,
                issues=[{"severity": "medium", "problem": str(i)} if isinstance(i, str) else {
                    "severity": str(i.get("severity", "medium")),
                    "problem": str(i.get("problem", "")),
                    "suggestion": str(i.get("suggestion", "")),
                } for i in issues],
                verdict=verdict if verdict in ("approve", "revise") else "revise",
            )
        except Exception as exc:
            logger.warning(f"Evaluator call failed (keeping draft): {exc}")
            return None

    @staticmethod
    def _feedback_prompt(evaluation: Evaluation) -> str:
        """Turn an Evaluation into user context for the next generator round."""
        lines = [
            "An independent reviewer evaluated your draft and asks for revision. "
            "Fix these issues and resubmit via submit_digest:"
        ]
        for issue in evaluation.issues:
            sev = issue.get("severity", "medium")
            prob = issue.get("problem", "")
            sug = issue.get("suggestion", "")
            lines.append(f"- [{sev}] {prob}" + (f" Suggestion: {sug}" if sug else ""))
        if not evaluation.issues:
            lines.append("- (reviewer gave no specific issues; tighten the draft overall)")
        lines.append("Keep papers you are confident about; replace or drop weak picks. "
                     "Do not just rephrase — actually address the feedback.")
        return "\n".join(lines)

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
        # Lazily fetch the full text (PDF) on demand so the agent can actually
        # read the paper's content when it wants to judge quality — not just
        # metadata. Failures degrade to whatever we already have.
        if not p.full_text and self.full_text_fetcher is not None:
            try:
                fetched = self.full_text_fetcher(p)
                if fetched:
                    p.full_text = fetched
            except Exception as exc:
                logger.warning(f"On-demand full-text fetch failed for {p.title}: {exc}")
        body = p.full_text or p.abstract or ""
        affil = ""
        if getattr(p, "affiliations", None):
            affil = f"Affiliations: {', '.join(p.affiliations[:6])}\n"
        return (
            f"[{index}] {p.title}\n"
            f"Authors: {_authors_line(p)}\n"
            f"{affil}"
            f"URL: {p.url}\n"
            f"Full abstract:\n{p.abstract}\n"
            f"Full-text preview:\n{_truncate(body, 4000)}"
        )

    def _search_candidates(self, candidates: list[Paper], query: str) -> str:
        """Filter candidates by keyword(s) in title+abstract (case-insensitive)."""
        q = (query or "").strip().lower()
        if not q:
            return "Empty query. Pass a keyword to search for."
        terms = [t for t in re.split(r"[\s,;]+", q) if t]
        matches = []
        for i, p in enumerate(candidates):
            hay = f"{p.title} {p.abstract}".lower()
            if all(t in hay for t in terms):
                score = round(p.score, 1) if p.score is not None else "?"
                matches.append(f"[{i}] {p.title} | embedding={score}/10 | {p.source}")
        if not matches:
            return f"No candidates match '{query}'."
        return "\n".join(matches)

    def _compare_papers(self, candidates: list[Paper], index_a: int, index_b: int) -> str:
        """Side-by-side view of two candidates to weigh a choice."""
        parts = []
        for idx in (index_a, index_b):
            if not (0 <= idx < len(candidates)):
                parts.append(f"[{idx}] Index out of range (0..{len(candidates)-1}).")
                continue
            p = candidates[idx]
            score = round(p.score, 1) if p.score is not None else "?"
            parts.append(
                f"[{idx}] {p.title} | embedding={score}/10 | {p.source}\n"
                f"    {_authors_line(p)}\n"
                f"    Abstract: {_truncate(p.abstract, 400)}"
            )
        return "\n\n---\n\n".join(parts)

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
                    work_score=_parse_work_score(item.get("work_score")),
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
    def fallback_digest(candidates: list[Paper], max_papers: int, language: str = "English") -> Digest:
        """Plain embedding-order digest used when the agent can't run."""
        papers = [
            DigestPaper(index=i, reason=_truncate(p.recommend_reason or "", 200))
            for i, p in enumerate(candidates[:max_papers])
        ]
        if language.lower().startswith("chinese"):
            return Digest(
                subject="每日论文速递",
                intro="以下是今天与你的研究方向最相关的论文，按相关度排序。",
                papers=papers,
                sections=[],
                outro="祝阅读愉快！",
            )
        return Digest(
            subject="Daily paper digest",
            intro="Here is today's selection, ordered by relevance to your library.",
            papers=papers,
            sections=[],
            outro="Enjoy reading!",
        )
