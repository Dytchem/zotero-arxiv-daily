<p align="center">
  <a href="" rel="noopener">
 <img width=200px height=200px src="assets/logo.svg" alt="logo"></a>
</p>

<h3 align="center">Zotero-arXiv-Daily</h3>

<div align="center">

  [![Status](https://img.shields.io/badge/status-active-success.svg)]()
  ![Stars](https://img.shields.io/github/stars/TideDra/zotero-arxiv-daily?style=flat)
  [![GitHub Issues](https://img.shields.io/github/issues/TideDra/zotero-arxiv-daily)](https://github.com/TideDra/zotero-arxiv-daily/issues)
  [![GitHub Pull Requests](https://img.shields.io/github/issues-pr/TideDra/zotero-arxiv-daily)](https://github.com/TideDra/zotero-arxiv-daily/pulls)
  [![License](https://img.shields.io/github/license/TideDra/zotero-arxiv-daily)](/LICENSE)

</div>

---

<p align="center"> A HarnessAgent that reads your Zotero library, hunts arXiv daily, and writes a personalised paper digest — delivered to your inbox.</p>

> [!IMPORTANT]
> Keep an eye on this repo. When the upstream updates, merge your fork to enjoy new features and bug fixes.

## 🇨🇳 中文简介

**Zotero-arXiv-Daily** 的核心是一个 **HarnessAgent**（类似 Claude Code / Codex 的自主 Agent）：
它读取你的 Zotero 文献库建立研究画像，每天从 arXiv / bioRxiv / medRxiv 抓取新论文，
经 embedding 粗筛后，agent 自主决定推荐哪些、为什么推，并生成完整邮件发到你邮箱📮。

- **单 Agent 架构**：一个 HarnessAgent 搞定全部 —— 蒸馏研究画像 → 审阅候选 → 撰写邮件全文
- **工具调用循环**：真正的 while-loop + function calling（`inspect_candidates` / `inspect_paper` / `submit_digest`），不是每篇打分
- **结构化输出**：agent 通过 `submit_digest` 提交结构化 JSON（subject / intro / papers[DigestPaper] / outro），渲染层绝不信任 LLM 原文
- **安全渲染层**：HTML escape 所有文本字段，LaTeX 公式转 Unicode（`$\\alpha$` → `α`），链接只允许 http(s)
- **优雅降级**：agent 失败 → 回退到 embedding 顺序 + 简化邮件，保证每日必有邮件
- **多渠道通知**：邮件 / Webhook（Telegram、Server酱等）
- **零成本部署**：Fork 仓库 + GitHub Action Secrets 即可每日自动运行

基于 [TideDra/zotero-arxiv-daily](https://github.com/TideDra/zotero-arxiv-daily)，感谢原作者的出色工作。

## ✨ Features

| Feature | Description |
|---------|-------------|
| **HarnessAgent** | Single autonomous agent that builds a research profile from your Zotero library, inspects candidates via tool calls, and writes the full digest |
| **Tool-use loop** | Real OpenAI function-calling cycle: `inspect_candidates`, `inspect_paper`, `submit_digest` — not per-paper scoring |
| **Structured digest** | Agent submits a typed `Digest` object (subject / intro / papers / outro); render layer trusts only the structure, never raw LLM text |
| **Safe HTML rendering** | All text fields are HTML-escaped; inline LaTeX is converted to Unicode (`$\\alpha$` → `α`); links are sanitised to http(s) only |
| **Mail-client hardening** | CJK font stack, Outlook-safe solid-color buttons, responsive `@media` rules, hidden preheader, relevance-desc ordering |
| **Localised UI** | `llm.language` switches labels (相关度/推荐理由/其他候选 vs Relevance/Why/Other candidates) |
| **Email archive** | Every run writes `cache_dir/last_email.html`, uploaded as a `last-email` CI artifact for review |
| **Graceful fallback** | If the agent fails, the pipeline falls back to embedding-ordered cards with simplified emails — you always get something |
| **Multi-source** | arXiv, bioRxiv, medRxiv with cross-list support |
| **Hybrid reranking** | BM25 + vector similarity (local or API-based embeddings) |
| **Keyword filters** | Include/exclude by title/abstract substrings |
| **Sent-history dedupe** | Skip papers already emailed in previous runs |
| **Multi-recipient** | Send to multiple email addresses (`email.receivers`) |
| **Webhook notifier** | Deliver digests via HTTP POST (Telegram bot, Server酱, etc.) |
| **Zero-cost CI/CD** | Runs on GitHub Actions — no server needed |

## 🚀 Quick Start

### 1. Fork & Clone

```bash
git clone https://github.com/<YOUR_USERNAME>/zotero-arxiv-daily.git
cd zotero-arxiv-daily
```

### 2. Install Dependencies

```bash
uv sync          # or: pip install -e .
```

### 3. Configure

Copy `config/base.yaml` to `config/local.yaml` and fill in the required fields:

```yaml
zotero:
  user_id: "12345678"
  api_key: "your-zotero-api-key"

source:
  arxiv:
    category: ["cs.AI", "cs.LG"]   # your research areas

llm:
  api:
    key: "sk-xxx"                   # OpenRouter key
    base_url: "https://openrouter.ai/api/v1"
  generation_kwargs:
    model: "gpt-5.6-luna"           # recommended model
  harness:
    enabled: true
    top_k: 100
    full_text_budget: 10
    max_steps: 12

email:
  sender: "you@example.com"
  receiver: "you@outlook.com"
  smtp_server: "smtp.example.com"
  smtp_port: 587
  sender_password: "your-smtp-password"
```

See [`config/base.yaml`](config/base.yaml) for all options.

### 4. Run Locally (Debug)

```bash
python -m zotero_arxiv_daily.executor --debug
```

Check the generated email in `.cache/debug_email.html`.

### 5. Deploy on GitHub Actions

1. Push your fork to GitHub
2. Go to **Settings → Secrets and variables → Actions**
3. Add these secrets:
   - `ZOTERO_USER_ID`
   - `ZOTERO_API_KEY`
   - `LLM_API_KEY`
   - `EMAIL_SENDER`
   - `EMAIL_RECEIVER`
   - `SMTP_SERVER`
   - `SMTP_PORT`
   - `SENDER_PASSWORD`
4. Enable the workflow: **Actions → Select workflow → Enable workflow**

The action runs daily at 09:00 UTC (adjustable in `.github/workflows/digest.yml`).

## 🏗 Architecture

```
┌─────────────────────┐     ┌──────────────────┐     ┌─────────────────┐
│  Zotero Library      │     │  arXiv RSS/API   │     │  Corpus Cache   │
│  (CorpusPaper[])     │     │  (RawPaperItem[])│     │  (.cache/)      │
└─────────┬───────────┘     └────────┬─────────┘     └────────┬────────┘
          │                          │                        │
          ▼                          ▼                        │
    ┌─────────────┐          ┌──────────────┐                │
    │ build_profile│         │ retrieve     │                │
    │  (cached)    │         │ papers       │                │
    └──────┬──────┘          └──────┬───────┘                │
           │                       │                         │
           │                       ▼                         │
           │                 ┌──────────────┐               │
           │                 │ rerank       │               │
           │                 │ (BM25+vec)   │               │
           │                 └──────┬───────┘               │
           │                        │                        │
           │                        ▼                        │
           │                 ┌──────────────┐               │
           │                 │ filter       │               │
           │                 │ (score/kw)   │               │
           │                 └──────┬───────┘               │
           │                        │                        │
           │                        ▼                        │
           │                 ┌──────────────────┐           │
           │                 │ HarnessAgent     │◄──────────┘
           │                 │  · inspect_...   │
           │                 │  · submit_digest │
           │                 └──────┬───────────┘
           │                        │ Digest
           ▼                        ▼
    ┌──────────────────────────────────────┐
    │  construct_email                     │
    │  (safe HTML render + LaTeX→Unicode)  │
    └──────────────┬───────────────────────┘
                   │
                   ▼
            ┌─────────────┐
            │ notify      │
            │ (email/webhook)│
            └─────────────┘
```

## 📂 Project Structure

```
src/zotero_arxiv_daily/
├── protocol.py          # Data classes: Paper, CorpusPaper, RawPaperItem
├── harness.py           # HarnessAgent: single agent loop + tools + Digest
├── construct_email.py   # Safe HTML renderer: Digest → email HTML
├── executor.py          # Pipeline orchestrator: fetch → rerank → agent → deliver
├── retriever/           # Source-specific retrievers (arXiv, bioRxiv, medRxiv)
├── reranker/            # Hybrid reranker (BM25 + embeddings, local or API)
└── notifier/            # Delivery plugins (email, webhook)

config/
├── base.yaml            # Full config schema (copy to local.yaml)
└── default.yaml         # Hydra defaults

tests/                   # pytest suite (150 tests, ruff-clean)
.github/workflows/       # GitHub Actions CI/CD
```

## 🧪 Testing

```bash
pytest          # 150 tests
ruff check .    # linting
```

All tests pass with Python 3.13+.

## 🔧 Configuration Reference

| Section | Key | Description |
|---------|-----|-------------|
| `zotero` | `user_id`, `api_key` | Your Zotero account credentials |
| `source.arxiv` | `category` | arXiv categories to monitor |
| `llm.api` | `key`, `base_url` | LLM provider (OpenRouter recommended) |
| `llm.generation_kwargs` | `model` | Model name (e.g. `gpt-5.6-luna`) |
| `llm.harness` | `enabled`, `top_k`, `full_text_budget`, `max_steps` | Agent configuration |
| `reranker` | `local` / `api` | Embedding reranker choice |
| `email` | `sender`, `receiver`, `receivers`, `smtp_*` | SMTP delivery settings |
| `executor` | `source`, `reranker`, `debug`, `send_empty`, `notifiers` | Pipeline control |

## 🙏 Credits

This project is a complete rewrite of [TideDra/zotero-arxiv-daily](https://github.com/TideDra/zotero-arxiv-daily), introducing a single HarnessAgent architecture inspired by Claude Code, Codex, and OpenClaw agent patterns.

## 📄 License

MIT
