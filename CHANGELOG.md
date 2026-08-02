# Changelog

All notable changes to this project are documented here.

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
