# Changelog

All notable changes to this project are documented here.

## [1.3.1] - 2026-08-02

### Changed
- **Agent-ranked display order**: the email's recommended cards now render in
  the agent's own editorial order instead of being re-sorted by the embedding
  score. The generator is explicitly instructed to order its picks like an
  experienced researcher — lead with what matters most to this reader — and
  the render layer trusts that judgement.
- `max_paper_num` deployment config raised 10 → 30, giving the agent a wider
  candidate window to choose from (the unused `top_k=100` cap now has room to
  apply once more candidates survive the filters).

## [1.3.0] - 2026-08-02

### Added
- **Lookback time window** (`source.arxiv.lookback_days`, default 2): the arXiv
  retriever now keeps papers from the last N days (yesterday + today) instead
  of only the same-day RSS batch, and the published date is preserved from the
  feed. A missed/failed run no longer loses the previous day's papers — they
  are picked up on the next run and deduped against sent history.
- **API-fallback resilience**: the weekend arXiv API fallback now retries
  transient fetch/parse failures (3 attempts, backoff) instead of silently
  returning nothing on a 429 or a flaky response.
- **Generator + Evaluator two-agent architecture** (docs/HARNESS.md): after the
  generator loop submits a draft, an independent evaluator with fresh context
  and no tools grades it (score 0-10, issues, verdict approve/revise). A
  `revise` verdict feeds the issues back and re-runs the generator, capped at
  `llm.harness.max_revisions` (default 2). The highest-scoring draft wins when
  the budget exhausts.
- New generator tools: `search_candidates` (keyword filter over title+abstract)
  and `compare_papers` (side-by-side of two candidates).
- Hard submit gate: `llm.harness.min_inspections` (default 3) — the agent
  cannot submit before deep-diving that many papers with `inspect_paper`;
  premature submits are rejected and the loop continues.
- Evaluator degradation: if the evaluator call fails, the draft is kept; if
  it keeps saying `revise`, budget exhaustion returns the best-scoring draft.
- Full-width side-by-side PDF/Abstract buttons (100% table, two 50% columns).

### Changed
- System prompt reworked into an explicit SURVEY → DEEP-DIVE → FOCUS → DECIDE
  → WRITE → SUBMIT workflow.
- `llm.harness` gains `min_inspections`, `max_revisions`, `evaluator_enabled`.

### Fixed
- `executor` read `full_text_budget` from the wrong config path
  (`executor.harness` instead of `llm.harness`) — the prefetch budget never
  applied; it now reads the real `llm.harness.full_text_budget`.
- `llm.harness.top_k` was read but never used — the agent now caps the
  candidate window at `top_k` before exploring.
- `datetime.utcnow()` deprecation in the run report replaced with
  timezone-aware `datetime.now(UTC)`.
- `config/custom.yaml` example was missing the `openai/` provider prefix, the
  `lookback_days` / `fallback_days` source options and the harness
  `min_inspections` / `max_revisions` / `evaluator_enabled` knobs — aligned
  with the real deployment config.
- CLAUDE.md claimed "no linter configured" while `pyproject.toml` ships a
  `[tool.ruff]` block — corrected, and the architecture section now lists all
  five agent tools plus the evaluator stage.
- Redundant `_collect_receivers` duplicate in `notifier.py` removed (it now
  imports the single implementation from `email_sender.py`).
- README/README.zh-CN: local debug command was broken
  (`python -m zotero_arxiv_daily.executor --debug` →
  `DEBUG=true uv run src/zotero_arxiv_daily/main.py`), test counts refreshed
  (170+), and the configuration reference table gained `lookback_days`,
  `min_inspections`, `max_revisions`, `evaluator_enabled`, `receivers`,
  `rerank_alpha` and friends.

## [1.2.0] - 2026-08-02

### Added
- **Single HarnessAgent architecture** (replaces the rigid two-stage pipeline):
  one autonomous agent loop (OpenAI-style function calling) reads the Zotero
  research profile, inspects the day's embedding-ranked candidates with its own
  tools (`inspect_candidates` / `inspect_paper`), decides what to recommend and
  why, and writes the complete digest — subject, intro, per-paper reasons,
  outro — via `submit_digest`.
- One LLM provider only: `llm.api` (e.g. OpenRouter `gpt-5.6-luna`). The legacy
  per-paper TLDR / affiliations calls and the separate harness provider are gone.
- Agent tools follow the Anthropic/Braintrust practice: small, high-signal,
  natural-language outputs instead of raw JSON dumps.
- Graceful degradation: any LLM failure falls back to the embedding order with
  a simple digest, so the daily email always goes out.
- Cached research profile (`.cache/research_profile.json`, keyed by corpus hash).
- Rendered email archive: `cache_dir/last_email.html` is written every run and
  uploaded as a `last-email` workflow artifact for review.
- Email polish (from subagent review): CJK font stack, Outlook-safe buttons
  (solid background + gradient enhancement), responsive `@media` rules, hidden
  preheader, date in header, higher-contrast footer, localised UI labels via
  `llm.language` (相关度 / 推荐理由 / 其他候选 / 退订), picks sorted by
  relevance desc, unpicked candidates listed compactly at the bottom, no
  dangling border on the last list item.

### Changed
- `llm.api` is the single LLM entry point: `key` / `base_url` / `generation_kwargs.model`.
- `llm.harness` now only carries agent-loop tuning: `enabled`, `top_k`,
  `full_text_budget`, `max_steps`.
- Agent's own subject line is used in the delivered email (was hard-coded
  `Daily arXiv YYYY/MM/DD`).

### Fixed
- Render layer strips stray Markdown the agent may leak into prose (`**bold**`,
  `[label](url)`, `` `code` ``, `# headers`) — reasons/intro/outro never show
  raw Markdown syntax in the email.
- Sent-history now records every candidate that made it into the email (picked
  AND "other candidates"), not just the picked ones — papers shown yesterday
  are never re-shown, while fresh papers keep flowing in from the feeds.
- arXiv RSS entries that use full `https://arxiv.org/abs/...` URLs as entry ids
  now yield a bare paper id, so derived PDF/e-print links no longer 404.
- `Relevance` badge showed `n/a` for every card (the render layer hard-coded
  `None`); the real embedding score is now shown.
- Cards no longer show both a Why note and a TLDR when both are present.
- Zotero corpus no longer drops papers whose `abstractNote` is empty (common
  for PDF imports) — the title is used as the embedding/profile fallback, so
  the research profile reflects the whole library instead of a handful of
  hand-annotated items.

## [1.1.0] - 2026-08-02

### Added
- `executor.min_score` config: drop papers below a relevance threshold (0-10) before sending, `null` keeps all. Reduces noise from barely-related candidates.
- Cross-source dedupe: the same preprint posted to both arXiv and bioRxiv/medRxiv is now matched by normalized title and sent only once.
- Email rendering upgrade: paper titles link to the abstract page, source badge (arXiv/bioRxiv/medRxiv) per card, and a header summary ("N papers recommended for you").
- `email.receivers`: extra recipients in addition to `receiver` (Cc), as a YAML list or comma-separated string.
- Top-level exception handling in `main.py`: fatal errors are logged with a full traceback and a non-zero exit code (so the workflow failure notification fires reliably).
- arXiv retrieval now fetches each configured category as its own RSS feed and dedupes across categories, avoiding the arXiv 1000-entry truncation cap when many categories are set.
- bioRxiv/medRxiv papers now link to the abstract page (`url`), with the PDF download as `pdf_url` (previously both pointed at the PDF).
- Removed the pointless 1-second sleep per paper during conversion (~100s saved per 100 papers).
- Chinese intro section in README.
- `executor.keywords_include` / `keywords_exclude`: optional substring (case-insensitive) filters on title+abstract. Useful to lock in a topic ("diffusion", "LLM") or exclude meta-papers ("survey", "tutorial").
- `executor.dedupe_history` (default `true`): papers already emailed in previous runs are skipped on re-runs. State is persisted to `executor.cache_dir/sent_papers.json`. Automatically bypassed in debug mode so re-runs always re-send.
- `executor.cache_dir` (default `.cache`): centralized config for all run-state files.
- Notifier plugin system (`src/zotero_arxiv_daily/notifier.py`): the digest can fan out to several channels in one run. Built-ins: `email` (SMTP, original behavior extracted to `email_sender.py`) and `webhook` (generic JSON POST for Telegram / Server酱 / 钉钉 / Discord / Slack — set `executor.notifiers: ['email', 'webhook']` and `webhook.url`).
- Structured run report: every run writes `cache_dir/last_run.json` (timestamp, corpus/candidates/ranked counts, elapsed, source, reranker) for machine-readable monitoring.
- CI now enforces a coverage floor (`--cov-fail-under=80`).
- Email/digest subject is now source-aware (`Daily arXiv 2026/08/02` vs `Daily Digest (arxiv, biorxiv) 2026/08/02`).
- Workflow cache key fixed to `corpus-embeddings` so GitHub Actions overwrites one cache entry instead of accumulating one per run.
- Resilience: a failing source (e.g. one API down) no longer kills the whole run — other sources still deliver, and the failure is recorded in the run report (`last_run.json`).
- Run report is now also written on the no-email path, so every execution leaves a machine-readable trace.

### Changed
- No-email decision now also triggers when every candidate was filtered out by `min_score` (previously only when zero papers were retrieved).

### Fixed
- HTML email renders gracefully when a paper has no PDF URL (button omitted instead of a broken `None` link).

## [1.0.0] - 2026-07

Baseline fork of [TideDra/zotero-arxiv-daily](https://github.com/TideDra/zotero-arxiv-daily) with hardened retrieval, hybrid reranking and modern email.
