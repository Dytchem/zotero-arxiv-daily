<p align="center">
  <img width="140" height="140" src="assets/logo.svg" alt="Zotero-arXiv-Daily logo">
</p>

<h1 align="center">Zotero-arXiv-Daily</h1>

<p align="center">
  <em>Your AI research librarian — reads your Zotero library, scans arXiv/bioRxiv/medRxiv daily, and recommends papers with real editorial judgement.</em>
</p>

<p align="center">
  <a href="https://github.com/Dytchem/zotero-arxiv-daily/actions"><img src="https://img.shields.io/github/actions/workflow/status/Dytchem/zotero-arxiv-daily/ci.yml?style=flat-square" alt="CI"></a>
  <img src="https://img.shields.io/badge/python-3.13+-blue?style=flat-square" alt="Python">
  <img src="https://img.shields.io/badge/tests-195-brightgreen?style=flat-square" alt="Tests">
  <img src="https://img.shields.io/badge/license-MIT-green?style=flat-square" alt="License">
</p>

<p align="center"><a href="README.zh-CN.md">🇨🇳 中文版</a></p>

---

## What it does

Every morning, a GitHub Actions workflow (free, no server):

1. **Learns your taste** from your Zotero library — topics, methods, and the *quality bar* you actually read at.
2. **Pulls the newest papers** from arXiv, bioRxiv and medRxiv.
3. **Shortlists candidates** with fast deterministic math (embeddings + BM25 + recency).
4. **Lets an autonomous agent decide** — it fetches full texts itself, reads them page by page, scores each paper's *work quality* (0–10), and writes the digest in your language.
5. **Emails you a polished HTML digest**: intro, expert-ordered cards with a **Relevance** chip and a **Work** chip, and a full "other candidates" section — every paper scored, nothing silently dropped.

The math ranks; the agent decides. Embedding scores are hints, not verdicts.

## Key features

- **Pi agent engine** (`agent/run.mjs` + `agent/ROLE.md`) — a real coding agent with its own tools: `inspect_candidates`, `fetch_full_text`, `inspect_paper` (paged), `search_candidates`, `search_web`, `compare_papers`, `submit_digest`. It decides what to read, fetches and reads the papers itself, and grounds every recommendation in the actual content.
- **Work-quality scoring** — every candidate (picked or not) gets a **Work** badge (0–10) judging rigour, novelty and provenance. Watery/低质 papers are called out even when they look on-topic.
- **Defensible ordering** — stronger work first; the evaluator audits inversions.
- **Full-text reading, guaranteed** — reading progress is tracked; a paper must actually be read (not skimmed) before it can be recommended.
- **Generator + Evaluator** — an independent reviewer grades each draft and drives revision rounds.
- **Safe rendering** — every text field HTML-escaped, LaTeX→Unicode, links whitelisted. The agent writes JSON, never markup.
- **Graceful degradation** — Pi failure → Python harness → embedding-order digest. The email always goes out.
- **Gap-free lookback, sent-history dedupe, multi-source, multi-recipient, webhook notifier, bilingual (EN/ZH).**

## Quick start

1. **Fork** this repo.
2. **Configure** — fill in `config/custom.yaml` (committed example; CI overwrites it from the `CUSTOM_CONFIG` variable) with:
   - Zotero: `user_id`, `api_key`
   - LLM: `OPENAI_API_KEY`, `OPENAI_API_BASE` (OpenRouter recommended)
   - Email: `SENDER`, `RECEIVER`, `SENDER_PASSWORD`
   - Your arXiv categories under `source.arxiv.category`
3. **Run** — the workflow fires daily at 22:00 UTC (06:00 Beijing, right after arXiv's release). Trigger manually anytime: *Actions → Send emails daily → Run workflow*.

Local debug (renders the email without sending):

```bash
DEBUG=true uv run src/zotero_arxiv_daily/main.py
# inspect .cache/last_email.html
```

Full config reference: [`config/base.yaml`](config/base.yaml).

## Architecture

```
Zotero library ──► build_profile (LLM, cached)
Feeds ──► retrieve ──► rerank (embeddings+BM25) ──► filter ──► top-N candidates
                                                            │
                        ┌───────────────────────────────────▼──────────┐
                        │  Pi agent (Node, agent/run.mjs + ROLE.md)     │
                        │  reads profile + raw Zotero library + list    │
                        │  fetches full texts itself, pages through,    │
                        │  scores Work 0–10, submits digest JSON        │
                        └───────────────────────────────────┬──────────┘
                        (fallback: Python HarnessAgent → embedding order)
                                                            ▼
                        construct_email (safe HTML) ──► email / webhook
```

## Project layout

```
src/zotero_arxiv_daily/   Python pipeline: executor, harness (legacy engine),
                          construct_email, retrievers, rerankers, notifier
agent/                    Pi agent engine: run.mjs, ROLE.md, fetch_text.py
                          (custom provider from env vars OPENAI_API_BASE + OPENAI_API_KEY,
                           no built-in provider catalog to avoid mimo etc.)
config/                   base.yaml (schema) + custom.yaml (overrides)
tests/                    195 tests, ruff-clean
docs/HARNESS.md           generator/evaluator design
```

## Upstream & license

A complete rewrite of [TideDra/zotero-arxiv-daily](https://github.com/TideDra/zotero-arxiv-daily),
rebuilt around an autonomous-agent architecture. See the [releases](https://github.com/Dytchem/zotero-arxiv-daily/releases)
for what changed vs. upstream.

**MIT** — your Zotero data and email addresses stay yours; this tool just reads them to send you a digest.
