<p align="center">
  <img width="140" height="140" src="assets/logo.svg" alt="Zotero-arXiv-Daily logo">
</p>

<h1 align="center">Zotero-arXiv-Daily</h1>

<p align="center">
  <em>Your AI research librarian — reads your Zotero library, scans arXiv/bioRxiv/medRxiv daily, and emails a digest with real editorial judgement.</em>
</p>

<p align="center">
  <a href="https://github.com/Dytchem/zotero-arxiv-daily/actions/workflows/ci.yml"><img src="https://img.shields.io/github/actions/workflow/status/Dytchem/zotero-arxiv-daily/ci.yml?style=flat-square" alt="CI"></a>
  <img src="https://img.shields.io/badge/python-3.13+-blue?style=flat-square" alt="Python">
  <img src="https://img.shields.io/badge/tests-198-brightgreen?style=flat-square" alt="Tests">
  <img src="https://img.shields.io/badge/license-MIT-green?style=flat-square" alt="License">
</p>

<p align="center"><a href="README.zh-CN.md">🇨🇳 中文版</a></p>

---

## What it does

Every morning a GitHub Actions workflow (free, no server of yours):

1. **Learns your taste** from your Zotero library — topics, methods, and the *quality bar* you actually read at.
2. **Pulls the newest papers** from arXiv, bioRxiv and medRxiv.
3. **Shortlists candidates** with fast deterministic math (embeddings + BM25 + recency).
4. **Lets an autonomous agent decide** — it browses the *full pool* of today's papers (not just the pre-filtered list), fetches full texts itself, reads them (delegating long papers to a sub-agent), verifies provenance online, and scores each paper's **Recommendation** (0–10).
5. **Emails you a polished HTML digest**: intro, expert-ordered cards with **Relevance** and **Recommendation** chips, and a full "other candidates" section — every candidate scored, nothing silently dropped, **papers the agent actually read and annotated listed first**.

**The math ranks; the agent decides.** Embedding scores are hints, not verdicts.

## Key features

- **Pi agent engine** (`agent/run.mjs` + `agent/ROLE.md`) — a real coding agent with its own tools: `inspect_candidates`, `inspect_pool`, `fetch_full_text`, `inspect_paper` (paged), `summarize_paper` (sub-agent for long papers), `search_web`, `search_candidates`, `compare_papers`, `finish_reading`, `submit_digest`. It decides what to read, fetches and reads the papers itself, and grounds every recommendation in the actual content.
- **Full-pool visibility** — the agent sees every deduplicated paper from today's fetch, including ones the keyword/min-score/max-count filters dropped. A high-value paper that the heuristics missed can still be rescued, read and recommended — and it shows up in the email with a "pool" note.
- **Recommendation scoring** — every candidate (picked or not) gets a **Recommendation** badge (0–10) judging rigour, novelty and provenance. Weak papers are called out even when they look on-topic.
- **Defensible ordering** — stronger work first; the reader can compare the badges.
- **Full-text reading, guaranteed** — reading progress is tracked; a paper must actually be read (not skimmed) before it can be recommended. Long papers are delegated to a sub-agent that reads the **entire full text in one pass** (no fragmentary chunking), so nothing is skipped and cross-section connections survive. Already-read papers float to the top of the agent's candidate/pool lists tagged `[READ]` (pool indices stay stable).
- **Cost-conscious by design** — the agent scores the whole pool from abstracts first and deep-reads only the shortlist (~8 papers), so a daily run costs well under $0.20; long-paper sub-agent reads the full text in one pass. No hard limits — the agent can always read more if it needs to.
- **Safe rendering** — every text field HTML-escaped, LaTeX→Unicode, links whitelisted. The agent writes JSON, never markup.
- **Provider-safe by design** — the LLM provider is created programmatically from the base URL hardcoded in config (`llm.api.base_url`, default `https://opencode.ai/zen/go/v1`) + the `LLM_API_KEY` secret with *only* your configured model; Pi's built-in provider catalog (which can silently fall back to unconfigured models) is never loaded.
- **Graceful degradation** — Pi failure → Python harness → embedding-order digest. The email always goes out.
- **Provider unbundling** — the LLM (`LLM_API_KEY` → `https://opencode.ai/zen/go/v1`) and reranker (`RERANKER_API_KEY` → `https://openrouter.ai/api/v1`) use **independent secrets** with the base URLs hardcoded in config; nothing shares a key between them.
- **Gap-free lookback, sent-history dedupe, multi-source, multi-recipient, webhook notifier, bilingual (EN/ZH).**

## Quick start

1. **Fork** this repo.
2. **Configure** — set repository **Secrets** (Actions → Settings → Secrets):
   - `ZOTERO_ID`, `ZOTERO_KEY` — your Zotero user ID and API key
   - `LLM_API_KEY` — your LLM API key (base URL is hardcoded in config: `https://opencode.ai/zen/go/v1`)
   - `RERANKER_API_KEY` — your reranker/embedding API key (base URL is hardcoded in config: `https://openrouter.ai/api/v1`)
   - `SENDER`, `RECEIVER`, `SENDER_PASSWORD` — SMTP credentials
   - *Optional but recommended:* `ANYSEARCH_API_KEY` — a free [AnySearch](https://anysearch.com/console/api-keys) API key for `search_web`. The agent uses web search to verify paper provenance (authors, venue, novelty claims). **Without a key it still works via anonymous access, but the shared GitHub runner IP is easily rate-limited and searches may fail**, which slows the agent down. With a key you get 1,000 requests/day (20 QPS).
   - Set the **Variable** `CUSTOM_CONFIG` — a YAML override with your arXiv categories and reranker (see `config/custom.yaml` in the repo)
3. **Run** — the workflow fires daily at 22:00 UTC (06:00 Beijing, right after arXiv's release). Trigger manually anytime: *Actions → Send emails daily → Run workflow*. Use **Run workflow → `reset_history` = true** for a test send.

Local debug (renders the email without sending):

```bash
DEBUG=true uv run src/zotero_arxiv_daily/main.py
# inspect .cache/last_email.html
```

Full config reference: [`config/base.yaml`](config/base.yaml).

## Real-world example (2026-08-04 test run)

A full test send (`reset_history=true`) on 2026-08-04, on the free GitHub Actions runner:

- **Input**: 2 Zotero library papers → 281 deduplicated arXiv papers fetched → 30 embedding-shortlisted candidates (the agent sees all 281 as the pool).
- **Agent work**: fetched 13 full texts itself, delegated **10 long papers to the sub-agent (each read in full, one pass)**, paged through 16 deep reads, recorded 24 reading notes, ran 8 candidate searches + 4 provenance web searches.
- **Email**: 6 recommended cards (Recommendation 7.5 → 6.2, descending) + 275 other candidates scored, **0 n/a**; analysed papers listed first in the "other candidates" block.
- **Cost**: **$0.1245** for the whole run — main agent loop $0.064 (reasoning/writing) + full-text sub-agent reads $0.060 + embeddings $0.001; web search $0 (AnySearch). Prompt cache hit ~76%, peak context 125k tokens (no price-doubling tier).
- **Runtime**: ~10 min end-to-end on the shared runner.

## Email digest preview

Below is a screenshot of a typical digest email as received on mobile (Zotero-arXiv-Daily WeChat article format):

![Email screenshot](assets/email-screenshot-2026-08-10.jpg)

## Architecture

```
Zotero library ──► build_profile (LLM, cached)
Feeds ──► retrieve ──► rerank (embeddings+BM25) ──► filter ──► candidates (top-N)
                                                                 │
                      ┌──────────────────────────────────────────▼─────────────┐
                      │  Pi agent (Node, agent/run.mjs + ROLE.md)               │
                      │  sees the FULL pool (candidates + filtered-out papers)  │
                      │  fetches full texts itself, pages through,              │
                      │  scores Recommendation 0–10, submits digest JSON        │
                      └──────────────────────────────────────────┬─────────────┘
                      (fallback: Python HarnessAgent → embedding order)
                                                                 ▼
                      construct_email (safe HTML) ──► email / webhook
```

## Project layout

```
src/zotero_arxiv_daily/   Python pipeline: executor, construct_email, retrievers,
                          rerankers, notifier, legacy Python harness
agent/                    Pi agent engine: run.mjs, ROLE.md, fetch_text.py
                          (provider built from config llm.api.base_url + LLM_API_KEY;
                           no models.json, no built-in provider catalog)
config/                   base.yaml (schema) + custom.yaml (overrides)
tests/                    198 tests, ruff-clean
docs/HARNESS.md           generator/evaluator design notes
```

## Upstream & license

A complete rewrite of [TideDra/zotero-arxiv-daily](https://github.com/TideDra/zotero-arxiv-daily),
rebuilt around an autonomous-agent architecture. See the [releases](https://github.com/Dytchem/zotero-arxiv-daily/releases)
for what changed vs. upstream.

**MIT** — your Zotero data and email addresses stay yours; this tool just reads them to send you a digest.
