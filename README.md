<p align="center">
  <img width="160" height="160" src="assets/logo.svg" alt="Zotero-arXiv-Daily logo">
</p>

<h1 align="center">Zotero-arXiv-Daily</h1>

<p align="center">
  <em>Your personal AI research librarian — reads your Zotero library, hunts arXiv every day, and recommends papers like an experienced researcher would.</em>
</p>

<p align="center">
  <a href="https://github.com/Dytchem/zotero-arxiv-daily/actions"><img src="https://img.shields.io/github/actions/workflow/status/Dytchem/zotero-arxiv-daily/ci.yml?style=flat-square" alt="CI"></a>
  <a href="https://github.com/Dytchem/zotero-arxiv-daily/blob/main/LICENSE"><img src="https://img.shields.io/github/license/Dytchem/zotero-arxiv-daily?style=flat-square" alt="License"></a>
  <img src="https://img.shields.io/badge/python-3.13+-blue?style=flat-square" alt="Python">
  <img src="https://img.shields.io/badge/tests-182-brightgreen?style=flat-square" alt="Tests">
</p>

<p align="center">
  <a href="README.zh-CN.md">🇨🇳 中文版</a>
</p>

---

## Table of Contents

- [What is it?](#-what-is-it)
- [How it works](#-how-it-works)
- [Features](#-features)
- [Quick Start](#-quick-start)
- [Email anatomy](#-email-anatomy)
- [Architecture](#-architecture)
- [Project Structure](#-project-structure)
- [Configuration Reference](#-configuration-reference)
- [Testing](#-testing)
- [FAQ](#-faq)
- [Credits & License](#-credits--license)

---

## 🧠 What is it?

**Zotero-arXiv-Daily** is an **AI research librarian** that runs for free on GitHub Actions. Every morning it:

1. **Learns your research interests** from your Zotero library (topics, keywords, methods).
2. **Pulls the newest papers** from arXiv, bioRxiv and medRxiv.
3. **Shortlists the best candidates** with fast, deterministic math (embeddings + BM25 + recency weighting).
4. **Lets an agent make the call** — the same kind of editorial judgement a senior researcher applies when skimming a new batch: *is this paper worth your time, and why?*
5. **Sends you a polished HTML digest** — with a subject line, an intro, per-paper reasons, and an outro, all written by the agent in your language.

> No rigid pipeline. No score-and-dump. The math ranks; the agent decides.

---

## ⚙️ How it works

The pipeline separates **cheap computation** from **editorial judgement**:

```
arXiv/bioRxiv/medRxiv feeds          Your Zotero library
        │                                    │
        ▼                                    ▼
  ┌─────────────────────────────────────────────────┐
  │  1. RERANK (deterministic, no LLM)              │
  │     • every candidate is embedded               │
  │     • every library paper is embedded (cached)  │
  │     • cosine similarity × recency weight        │
  │       + 30% BM25 lexical score → 0–10 score     │
  └───────────────────────┬─────────────────────────┘
                          ▼
  ┌─────────────────────────────────────────────────┐
  │  2. FILTER (deterministic)                      │
  │     • min_score / keyword include-exclude       │
  │     • sent-history dedupe (never re-send)       │
  │     • keep top N (max_paper_num, e.g. 30)       │
  └───────────────────────┬─────────────────────────┘
                          ▼
  ┌─────────────────────────────────────────────────┐
  │  3. AGENT (LLM, the expert)                     │
  │     • reads your research profile               │
  │     • inspects candidates with tools            │
  │     • picks 3–6 papers and writes the reasons   │
  │     • ORDERS the picks by editorial value —     │
  │       the email shows exactly this order        │
  └───────────────────────┬─────────────────────────┘
                          ▼
  ┌─────────────────────────────────────────────────┐
  │  4. EVALUATOR (independent reviewer)            │
  │     • fresh context, no tools                   │
  │     • scores the draft (0–10), lists issues     │
  │     • approve → send; revise → agent improves   │
  └───────────────────────┬─────────────────────────┘
                          ▼
              Safe HTML render → email + webhook
```

**Key idea:** the embedding score is only a *hint*. The agent is the expert — it decides what to recommend, what to say about each paper, and in what order you should read them.

---

## ✨ Features

| Feature | Description |
|---------|-------------|
| 🧠 **Single agent, full editorial control** | One agent loop reads your profile, inspects candidates with tools, and writes the whole email |
| 🏆 **Expert-ranked picks** | The email's card order is the agent's editorial order — not a raw score sort |
| ⭐ **Work-quality scoring** | Every card shows a **Work** badge (0–10): the agent's judgement of the paper's own merit — rigour, novelty, method soundness, provenance. Watery / low-quality work from weak institutions gets called out, even when embedding relevance is high |
| 🎯 **Taste-aware selection** | The research profile distills not just topics but the researcher's *taste* and quality bar; picks are matched to it, not just to keyword overlap |
| 🔧 **Real tool-use loop** | `inspect_candidates` → `inspect_paper` → `search_candidates` → `compare_papers` → `submit_digest`, with a hard submit gate (≥3 inspections) |
| ⚖️ **Generator + Evaluator** | An independent reviewer grades every draft (score, issues, approve/revise); `revise` feeds issues back, capped at `max_revisions` |
| 📝 **Structured output** | The agent submits a typed `Digest` (subject / intro / papers / outro); the render layer trusts only the structure, never raw LLM text |
| 🛡️ **Safe rendering** | Every text field HTML-escaped, LaTeX → Unicode (`$\alpha$` → `α`), links whitelisted to http(s) |
| 📧 **Mail-client hardened** | CJK font stack, Outlook-safe buttons, responsive layout, hidden preheader |
| 🌐 **Localised** | Labels and digest language follow `llm.language` (English / Chinese) |
| 📅 **Gap-free lookback** | `lookback_days` (default 2) keeps yesterday + today — a missed run never loses the previous day's papers |
| 🪂 **Graceful fallback** | If the agent fails, you still get an embedding-ordered digest — the email always goes out |
| 📦 **Email archive** | Every run saves `cache_dir/last_email.html` and uploads it as a CI artifact |
| 📚 **Multi-source** | arXiv (with weekend API fallback), bioRxiv, medRxiv, cross-list support |
| 🎯 **Hybrid reranking** | BM25 + vector similarity, local or API embeddings |
| 🔍 **Keyword filters** | Include/exclude papers by title/abstract substrings |
| 🚫 **Sent-history dedupe** | Papers already emailed are never re-sent |
| 👥 **Multi-recipient** | One digest, many inboxes (`email.receivers`) |
| 🔔 **Webhook notifier** | Telegram, Server酱, Discord, Slack… deliver anywhere via HTTP POST |
| 💸 **Zero-cost CI/CD** | Runs on GitHub Actions — no server, no subscription |

---

## 🚀 Quick Start

### Prerequisites

- A [Zotero](https://www.zotero.org/) library with a few papers
- An LLM API key (e.g. [OpenRouter](https://openrouter.ai))
- An email account with SMTP access (QQ, Gmail, Outlook…)

### 1. Fork & clone

```bash
git clone https://github.com/<YOUR_USERNAME>/zotero-arxiv-daily.git
cd zotero-arxiv-daily
```

### 2. Install

```bash
uv sync        # or: pip install -e .
```

### 3. Configure

Copy `config/base.yaml` to `config/custom.yaml` and fill in the essentials:

```yaml
zotero:
  user_id: "12345678"          # your Zotero user id
  api_key: "***"            # Zotero API key (read access)

source:
  arxiv:
    category: ["cs.AI", "cs.LG"]   # your research areas
    lookback_days: 2               # keep yesterday + today (gap-free)

llm:
  api:
    key: "sk-or-..."               # OpenRouter API key
    base_url: "https://openrouter.ai/api/v1"
  generation_kwargs:
    model: "openai/gpt-5.6-luna"   # recommended agent model
  language: English                # digest language: English | Chinese
  harness:
    enabled: true
    top_k: 100                     # max candidates the agent may see
    full_text_budget: 10           # full-text prefetch for top candidates
    max_steps: 12                  # agent loop budget
    min_inspections: 3             # submit gate: inspect ≥3 papers first
    max_revisions: 2               # evaluator improvement rounds
    evaluator_enabled: true        # independent reviewer on/off

email:
  sender: "you@example.com"
  receiver: "you@outlook.com"
  smtp_server: "smtp.example.com"
  smtp_port: 465
  sender_password: "your-smtp-password"

executor:
  source: ["arxiv"]
  reranker: api                    # 'api' (OpenAI-compatible) or 'local'
  max_paper_num: 30                # candidate window for the agent
```

All options are documented in [`config/base.yaml`](config/base.yaml).

### 4. Run locally (debug)

```bash
DEBUG=true uv run src/zotero_arxiv_daily/main.py
```

Inspect the rendered email at `.cache/last_email.html` — debug mode skips sending.

### 5. Deploy on GitHub Actions

1. Push your fork to GitHub.
2. **Settings → Secrets and variables → Actions**, add:
   - `ZOTERO_ID`, `ZOTERO_KEY`
   - `OPENAI_API_KEY`, `OPENAI_API_BASE`
   - `SENDER`, `RECEIVER`, `SENDER_PASSWORD`
3. Optionally set a **variable** `CUSTOM_CONFIG` (raw YAML merged over the defaults) — useful for tweaking categories, language or recipient lists without touching code.
4. The workflow runs **daily at 22:00 UTC** (06:00 Beijing time — right after arXiv's 04:00 daily release). Adjust the cron in `.github/workflows/main.yml` if needed. Trigger it manually anytime with *Actions → Send emails daily → Run workflow*.

---

## 📧 Email anatomy

A rendered digest looks like this:

| Part | Who writes it | Notes |
|------|---------------|-------|
| **Subject** | Agent | Short, informative, in your language |
| **Intro** | Agent | Context for today's batch |
| **Cards** | Agent picks + order | Title → abstract link, source badge, relevance chip, **work-quality chip**, "Why" reason, PDF/Abstract buttons |
| **Other candidates** | Pipeline | Compact list of everything else that survived filtering (still deduped) |
| **Outro** | Agent | Sign-off and look-ahead |
| **Footer** | Template | Unsubscribe hint, localised |

The **order of the cards is the agent's editorial ranking** — the paper it thinks matters most to you comes first. Each card carries two chips: **Relevance** (the cheap embedding/BM25 hint of topical match) and **Work** (the agent's own quality judgement of the paper — how rigorous, novel and trustworthy the work is). Trust the Work chip over the Relevance chip: the feed is full of papers that look on-topic but are shallow or from dubious provenance.

---

## 🏗 Architecture

```
┌──────────────────┐      ┌───────────────────┐      ┌─────────────────┐
│   Zotero Library  │      │  arXiv/bioRxiv/   │      │  .cache/        │
│  (CorpusPaper[])  │      │  medRxiv feeds    │      │  (embeddings,   │
└────────┬─────────┘      └─────────┬─────────┘      │   sent-history) │
         │                          │                └────────┬────────┘
         ▼                          ▼                         │
   build_profile              retrieve_papers                │
   (LLM-distilled,       ┌───────┴────────┐                  │
    cached by hash)      │  rerank (BM25  │                  │
         │               │  + embeddings) │                  │
         │               └───────┬────────┘                  │
         │                       ▼                           │
         │                 filter (min_score /              │
         │                 keywords / sent-history)          │
         │                       │                           │
         │                       ▼                           │
         │            ┌──────────────────────┐               │
         │            │     HarnessAgent      │◄─────────────┘
         │            │  inspect_candidates   │   candidate list
         │            │  inspect_paper        │   + embedding scores
         │            │  search_candidates    │
         │            │  compare_papers       │
         │            │  submit_digest        │
         │            └──────────┬───────────┘
         │                       │  draft Digest
         │                       ▼
         │            ┌──────────────────────┐
         │            │   EVALUATOR          │  fresh context, no tools
         │            │  score + issues +    │  approve → done
         │            │  verdict (revise?)   │  revise → feedback loop
         │            └──────────┬───────────┘
         │                       │  final Digest (typed JSON)
         ▼                       ▼
   ┌──────────────────────────────────────┐
   │   construct_email (safe HTML render) │
   └──────────────────┬───────────────────┘
                      ▼
            ┌──────────────────┐
            │  notifiers       │
            │  email / webhook │
            └──────────────────┘
```

**Why two agents?** Anthropic reports the generator/evaluator pattern as the biggest quality lever for taste-dependent tasks. The generator explores and writes; an independent evaluator with fresh context and no tools grades the draft and drives improvement rounds — see [`docs/HARNESS.md`](docs/HARNESS.md) for the full design.

---

## 📂 Project Structure

```
src/zotero_arxiv_daily/
├── protocol.py          # Data classes: Paper, CorpusPaper, RawPaperItem
├── harness.py           # HarnessAgent: generator loop + tools + evaluator
├── construct_email.py   # Safe HTML renderer: Digest → email HTML
├── executor.py          # Orchestrator: fetch → rerank → filter → agent → deliver
├── retriever/           # arXiv, bioRxiv, medRxiv retrievers
├── reranker/            # Hybrid reranker (BM25 + embeddings, local or API)
└── notifier.py          # Delivery plugins (email, webhook)

config/
├── base.yaml            # Full config schema (defaults + documentation)
├── custom.yaml          # Your overrides (example committed; CI overwrites it)
└── default.yaml         # Composition root: base + custom

tests/                   # 170+ tests, ruff-clean
docs/HARNESS.md          # Generator/evaluator design document
.github/workflows/       # CI + daily digest + keep-alive
```

---

## 🔧 Configuration Reference

| Section | Key | Description |
|---------|-----|-------------|
| `zotero` | `user_id`, `api_key` | Zotero account credentials |
| `zotero` | `include_path`, `ignore_path` | Glob patterns to include/exclude library collections |
| `source.arxiv` | `category`, `include_cross_list`, `lookback_days`, `fallback_days` | arXiv categories; keep last N days (gap-free); API fallback when RSS is empty (weekends) |
| `source.biorxiv` / `source.medrxiv` | `category` | bioRxiv / medRxiv categories |
| `llm.api` | `key`, `base_url` | LLM provider (OpenRouter recommended) |
| `llm.generation_kwargs` | `model`, `max_tokens` | Agent model + generation settings |
| `llm.language` | `English` / `Chinese` | Digest language (labels + agent output) |
| `llm.harness` | `enabled`, `top_k`, `full_text_budget`, `max_steps`, `min_inspections`, `max_revisions`, `evaluator_enabled` | Agent loop tuning |
| `reranker` | `local` / `api` | Embedding backend (model, batch_size, cache_dir) |
| `executor` | `rerank_alpha` | Hybrid weight: 1.0 = pure vector, 0.0 = pure BM25, null = vector only |
| `executor` | `min_score`, `keywords_include`, `keywords_exclude` | Deterministic pre-agent filters |
| `executor` | `max_paper_num`, `dedupe_history`, `cache_dir` | Candidate window, dedupe switch, state location |
| `executor` | `notifiers` | Delivery channels: `['email']`, `['email', 'webhook']` |
| `email` | `sender`, `receiver`, `receivers`, `smtp_*` | SMTP delivery (plus extra Cc recipients) |

---

## 🧪 Testing

```bash
uv run pytest        # 170+ tests, ~89% coverage
uvx ruff check src/ tests/
```

Supports Python 3.13+.

---

## ❓ FAQ

**How are papers actually selected?**
The pipeline embeds every candidate and every library paper, scores matches (cosine × recency + BM25), applies deterministic filters (min score, keywords, sent-history), and hands the surviving top-N to the agent. The agent — not a formula — makes the final call on what to recommend.

**Who decides the order of the email?**
The agent. It's instructed to order its picks like an experienced researcher: what matters most to *you* comes first. The embedding score is a hint, not a sort key.

**Why do I get papers on weekends?**
arXiv publishes nothing on weekends, so the RSS feed is empty. The workflow falls back to the arXiv API for the last `fallback_days` (default 7) so your Monday digest still has content.

**What if a daily run is missed?**
`lookback_days` (default 2) keeps yesterday's papers in the window, and sent-history dedupe ensures nothing is re-sent — a missed run simply catches up the next day.

**Will I see the same paper twice?**
No. Every paper that appears in an email (picked or in "other candidates") is recorded in `sent_papers.json` and filtered out on later runs.

**Can I change the language?**
Set `llm.language: Chinese` or `English`. Both the agent's writing and the UI labels switch.

**What models do I need?**
One LLM for the agent (e.g. `openai/gpt-5.6-luna` on OpenRouter) and optionally an embeddings API for reranking (e.g. `qwen/qwen3-embedding-8b`) — or the local reranker via `pip install .[local-reranker]`.

**Is it really free?**
GitHub Actions' free tier runs the daily workflow for public repositories. No server, no subscription.

---

## 🙏 Credits & License

This project is a complete rewrite of [TideDra/zotero-arxiv-daily](https://github.com/TideDra/zotero-arxiv-daily), rebuilt around a generator/evaluator agent architecture inspired by Claude Code, Codex and OpenClaw patterns, and by Anthropic's harness research. Many thanks to the original author.

**License:** MIT — do what you want, but remember this is a personal research tool: your Zotero data and email addresses are yours, keep them safe.
