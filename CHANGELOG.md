# Changelog

All notable changes to this project are documented here.

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

### Changed
- No-email decision now also triggers when every candidate was filtered out by `min_score` (previously only when zero papers were retrieved).

### Fixed
- HTML email renders gracefully when a paper has no PDF URL (button omitted instead of a broken `None` link).

## [1.0.0] - 2026-07

Baseline fork of [TideDra/zotero-arxiv-daily](https://github.com/TideDra/zotero-arxiv-daily) with hardened retrieval, hybrid reranking and modern email.
