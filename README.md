<p align="center">
  <img width="160" height="160" src="assets/logo.svg" alt="Zotero-arXiv-Daily logo">
</p>

<h1 align="center">Zotero-arXiv-Daily</h1>

<p align="center">
  <em>Your personal AI research librarian — reads your Zotero library, hunts arXiv every day, and writes a personalised paper digest straight to your inbox.</em>
</p>

<p align="center">
  <a href="https://github.com/Dytchem/zotero-arxiv-daily/actions"><img src="https://img.shields.io/github/actions/workflow/status/Dytchem/zotero-arxiv-daily/ci.yml?style=flat-square" alt="CI"></a>
  <a href="https://github.com/Dytchem/zotero-arxiv-daily/blob/main/LICENSE"><img src="https://img.shields.io/github/license/Dytchem/zotero-arxiv-daily?style=flat-square" alt="License"></a>
  <img src="https://img.shields.io/badge/python-3.13+-blue?style=flat-square" alt="Python">
  <img src="https://img.shields.io/badge/coverage-89%25-brightgreen?style=flat-square" alt="Coverage">
</p>

<p align="center">
  <a href="README.zh-CN.md">🇨🇳 中文版</a>
</p>

---

## What is it?

**Zotero-arXiv-Daily** is a **HarnessAgent** — an autonomous agent in the spirit of Claude Code, Codex and OpenClaw — that does the job of a research librarian every morning:

1. It **reads your Zotero library** and distills a research profile (topics, keywords, methods).
2. It **pulls the newest papers** from arXiv, bioRxiv and medRxiv, pre-ranked by embedding similarity.
3. It **decides what you should read** — inspecting candidates, weighing relevance against your actual interests — and writes the whole email itself: subject, intro, per-paper reasons, outro.
4. It **delivers a polished HTML digest** to your inbox. Zero cost, fully automated via GitHub Actions.

> No rigid pipeline. No per-paper score-and-dump. One agent makes every editorial call.

---

## ✨ Features

| Feature | Description |
|---------|-------------|
| 🧠 **Single HarnessAgent** | One agent loop distills your research profile, inspects candidates with its own tools, and writes the complete digest |
| 🔧 **Real tool-use loop** | OpenAI function calling: `inspect_candidates` → `inspect_paper` → `submit_digest` — not mechanical per-paper scoring |
| 📝 **Structured output** | The agent submits a typed `Digest` (subject / intro / papers / outro); the render layer trusts only the structure, never raw LLM text |
| 🛡️ **Safe rendering** | Every text field is HTML-escaped, LaTeX becomes Unicode (`$\alpha$` → `α`), links are whitelisted to http(s) |
| 📧 **Mail-client hardened** | CJK font stack, Outlook-safe solid-color buttons, responsive layout, hidden preheader, relevance-desc ordering |
| 🌐 **Localised UI** | Labels switch with `llm.language` (Chinese: 相关度/推荐理由/其他候选 · English: Relevance/Why/Other candidates) |
| 🪂 **Graceful fallback** | If the agent fails, you still get an embedding-ordered digest — the email always goes out |
| 📦 **Email archive** | Every run saves `cache_dir/last_email.html` and uploads it as a CI artifact for review |
| 📚 **Multi-source** | arXiv (with weekend API fallback), bioRxiv, medRxiv, cross-list support |
| 🎯 **Hybrid reranking** | BM25 + vector similarity, local or API embeddings |
| 🔍 **Keyword filters** | Include/exclude papers by title/abstract substrings |
| 🚫 **Sent-history dedupe** | Papers already emailed are never re-sent |
| 👥 **Multi-recipient** | One digest, many inboxes (`email.receivers`) |
| 🔔 **Webhook notifier** | Telegram, Server酱, Discord, Slack… deliver anywhere via HTTP POST |
| 💸 **Zero-cost CI/CD** | Runs on GitHub Actions — no server, no subscription |

---

## 🚀 Quick Start

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
  api_key: "sk-..."            # Zotero API key (read access)

source:
  arxiv:
    category: ["cs.AI", "cs.LG"]   # your research areas

llm:
  api:
    key: "sk-or-..."               # OpenRouter API key
    base_url: "https://openrouter.ai/api/v1"
  generation_kwargs:
    model: "openai/gpt-5.6-luna"   # recommended agent model
  language: English                # digest language: English | Chinese
  harness:
    enabled: true
    top_k: 100
    full_text_budget: 10
    max_steps: 12

email:
  sender: "you@example.com"
  receiver: "you@outlook.com"
  smtp_server: "smtp.example.com"
  smtp_port: 465
  sender_password: "your-smtp-password"

executor:
  source: ["arxiv"]
  reranker: api                    # 'api' (OpenAI-compatible) or 'local'
  max_paper_num: 10
```

All options are documented in [`config/base.yaml`](config/base.yaml).

### 4. Run locally (debug)

```bash
python -m zotero_arxiv_daily.executor --debug
```

Inspect the rendered email at `.cache/last_email.html` — no sending in debug mode.

### 5. Deploy on GitHub Actions

1. Push your fork to GitHub.
2. **Settings → Secrets and variables → Actions**, add:
   - `ZOTERO_ID`, `ZOTERO_KEY`
   - `OPENAI_API_KEY`, `OPENAI_API_BASE`
   - `SENDER`, `RECEIVER`, `SENDER_PASSWORD`
3. Optionally set a **variable** `CUSTOM_CONFIG` (raw YAML merged over the defaults) — useful for tweaking categories, language or recipient lists without touching code.
4. The workflow runs **daily at 22:00 UTC** (adjust the cron in `.github/workflows/main.yml`). Trigger it manually anytime with *Actions → Send emails daily → Run workflow*.

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
         │            │  submit_digest        │
         │            └──────────┬───────────┘
         │                       │  Digest (typed JSON)
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

**Key idea:** the pipeline feeds the agent cheap signals (vector order, abstracts); every *editorial* decision — what to recommend, how to phrase each reason, how to structure the mail — belongs to the agent.

---

## 📂 Project Structure

```
src/zotero_arxiv_daily/
├── protocol.py          # Data classes: Paper, CorpusPaper, RawPaperItem
├── harness.py           # HarnessAgent: agent loop + tools + Digest + fallback
├── construct_email.py   # Safe HTML renderer: Digest → email HTML
├── executor.py          # Orchestrator: fetch → rerank → agent → deliver
├── retriever/           # arXiv, bioRxiv, medRxiv retrievers
├── reranker/            # Hybrid reranker (BM25 + embeddings, local or API)
└── notifier.py          # Delivery plugins (email, webhook)

config/
├── base.yaml            # Full config schema
└── custom.yaml          # Your overrides (gitignored by default)

tests/                   # 160+ tests, ruff-clean
.github/workflows/       # CI + daily digest + keep-alive
```

---

## 🧪 Testing

```bash
uv run pytest        # 160+ tests, ~89% coverage
uvx ruff check src/ tests/
```

Supports Python 3.13+.

---

## 🔧 Configuration Reference

| Section | Key | Description |
|---------|-----|-------------|
| `zotero` | `user_id`, `api_key` | Zotero account credentials |
| `source.arxiv` | `category`, `fallback_days` | arXiv categories; API fallback when RSS is empty (weekends) |
| `source.biorxiv` / `source.medrxiv` | `category` | bioRxiv / medRxiv categories |
| `llm.api` | `key`, `base_url` | LLM provider (OpenRouter recommended) |
| `llm.generation_kwargs` | `model`, `max_tokens` | Agent model + generation settings |
| `llm.language` | `English` / `Chinese` | Digest language (labels + agent output) |
| `llm.harness` | `enabled`, `top_k`, `full_text_budget`, `max_steps` | Agent loop tuning |
| `reranker` | `local` / `api` | Embedding backend (model, batch_size, cache) |
| `email` | `sender`, `receiver`, `receivers`, `smtp_*` | SMTP delivery |
| `executor` | `source`, `reranker`, `max_paper_num`, `min_score`, `keywords_*`, `dedupe_history`, `notifiers` | Pipeline control |

---

## 🙏 Credits

This project is a complete rewrite of [TideDra/zotero-arxiv-daily](https://github.com/TideDra/zotero-arxiv-daily), rebuilt around a single HarnessAgent architecture inspired by Claude Code, Codex and OpenClaw agent patterns. Many thanks to the original author.

## 📄 License

MIT
