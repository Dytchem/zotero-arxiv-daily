<p align="center">
  <img width="160" height="160" src="assets/logo.svg" alt="Zotero-arXiv-Daily logo">
</p>

<h1 align="center">Zotero-arXiv-Daily</h1>

<p align="center">
  <em>你的专属 AI 学术图书管理员 —— 读懂你的 Zotero 文献库，每天替你追踪 arXiv 新论文，把一份精心撰写的个性化论文推荐邮件送到你的邮箱。</em>
</p>

<p align="center">
  <a href="https://github.com/Dytchem/zotero-arxiv-daily/actions"><img src="https://img.shields.io/github/actions/workflow/status/Dytchem/zotero-arxiv-daily/ci.yml?style=flat-square" alt="CI"></a>
  <a href="https://github.com/Dytchem/zotero-arxiv-daily/blob/main/LICENSE"><img src="https://img.shields.io/github/license/Dytchem/zotero-arxiv-daily?style=flat-square" alt="License"></a>
  <img src="https://img.shields.io/badge/python-3.13+-blue?style=flat-square" alt="Python">
  <img src="https://img.shields.io/badge/coverage-89%25-brightgreen?style=flat-square" alt="Coverage">
</p>

<p align="center">
  <a href="README.md">🌐 English</a>
</p>

---

## 这是什么？

**Zotero-arXiv-Daily** 的核心是一个 **HarnessAgent** —— 一个借鉴 Claude Code、Codex、OpenClaw 思路的自主智能体，每天替你完成学术图书管理员的工作：

1. **读懂你的 Zotero 文献库**，提炼出你的研究方向画像（主题、关键词、方法）。
2. **抓取最新论文**（arXiv / bioRxiv / medRxiv），先用向量相似度粗排。
3. **自主决定推荐什么** —— 亲自审阅候选论文、对照你的真实兴趣判断取舍，然后亲自撰写整封邮件：标题、开场、每篇的推荐理由、结尾。
4. **把排版精美的 HTML 邮件送进你的收件箱**。零成本，GitHub Actions 全自动运行。

> 没有僵硬的流水线，没有逐篇打分的机械流程。所有的编辑决策，都交给这一个智能体。

---

## ✨ 功能特性

| 特性 | 说明 |
|------|------|
| 🧠 **单一 HarnessAgent** | 一个智能体循环完成全部工作：提炼研究画像 → 用工具审阅候选 → 撰写完整邮件 |
| 🔧 **真正的工具调用循环** | OpenAI function calling：`inspect_candidates` → `inspect_paper` → `submit_digest`，不是机械地逐篇打分 |
| 📝 **结构化输出** | 智能体提交类型化的 `Digest`（主题 / 开场 / 论文 / 结尾）；渲染层只信任结构，绝不信任 LLM 原文 |
| 🛡️ **安全渲染** | 所有文本字段 HTML 转义，LaTeX 公式转 Unicode（`$\alpha$` → `α`），链接只放行 http(s) |
| 📧 **邮件客户端适配** | 中文字体栈、Outlook 安全的纯色按钮、响应式布局、隐藏 preheader、按相关度降序 |
| 🌐 **界面本地化** | 标签随 `llm.language` 切换（中文：相关度/推荐理由/其他候选 · English：Relevance/Why/Other candidates） |
| 🪂 **优雅降级** | 智能体故障时自动回退为按向量排序的简化邮件 —— 每天的邮件保证送达 |
| 📦 **邮件存档** | 每次运行保存 `cache_dir/last_email.html` 并上传为 CI 产物，方便随时复查 |
| 📚 **多数据源** | arXiv（含周末 API 兜底）、bioRxiv、medRxiv，支持交叉列表 |
| 🎯 **混合重排** | BM25 + 向量相似度，支持本地或 API 向量模型 |
| 🔍 **关键词过滤** | 按标题/摘要子串包含/排除论文 |
| 🚫 **历史去重** | 已推送过的论文绝不重复发送 |
| 👥 **多收件人** | 一封推荐，多人共享（`email.receivers`） |
| 🔔 **Webhook 通知** | Telegram、Server酱、Discord、Slack……通过 HTTP POST 推送到任意平台 |
| 💸 **零成本部署** | 依托 GitHub Actions 免费运行 —— 不需要服务器，不需要订阅 |

---

## 🚀 快速开始

### 1. Fork 并克隆

```bash
git clone https://github.com/<你的用户名>/zotero-arxiv-daily.git
cd zotero-arxiv-daily
```

### 2. 安装依赖

```bash
uv sync        # 或：pip install -e .
```

### 3. 配置

把 `config/base.yaml` 复制为 `config/custom.yaml`，填写必填项：

```yaml
zotero:
  user_id: "12345678"          # 你的 Zotero 用户 ID
  api_key: "sk-..."            # Zotero API Key（只读权限）

source:
  arxiv:
    category: ["cs.AI", "cs.LG"]   # 你的研究方向分类

llm:
  api:
    key: "sk-or-..."               # OpenRouter API Key
    base_url: "https://openrouter.ai/api/v1"
  generation_kwargs:
    model: "openai/gpt-5.6-luna"   # 推荐的智能体模型
  language: Chinese                # 邮件语言：Chinese | English
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
  sender_password: "你的SMTP授权码"

executor:
  source: ["arxiv"]
  reranker: api                    # 'api'（OpenAI 兼容）或 'local'
  max_paper_num: 10
```

全部配置项见 [`config/base.yaml`](config/base.yaml)。

### 4. 本地调试运行

```bash
python -m zotero_arxiv_daily.executor --debug
```

渲染结果在 `.cache/last_email.html` —— 调试模式不会真的发信。

### 5. 部署到 GitHub Actions

1. 把你的 Fork 推到 GitHub。
2. **Settings → Secrets and variables → Actions** 添加：
   - `ZOTERO_ID`、`ZOTERO_KEY`
   - `OPENAI_API_KEY`、`OPENAI_API_BASE`
   - `SENDER`、`RECEIVER`、`SENDER_PASSWORD`
3. 可选：设置变量 `CUSTOM_CONFIG`（一段 YAML，合并覆盖默认配置）—— 不用改代码就能调整分类、语言、收件人。
4. 工作流**每天 22:00 UTC 自动运行**（可在 `.github/workflows/main.yml` 修改 cron）。想立即触发？*Actions → Send emails daily → Run workflow* 手动跑一次。

---

## 🏗 架构

```
┌──────────────────┐      ┌───────────────────┐      ┌─────────────────┐
│   Zotero 文献库    │      │  arXiv/bioRxiv/   │      │  .cache/        │
│  (CorpusPaper[])  │      │  medRxiv 订阅源    │      │  (向量缓存、    │
└────────┬─────────┘      └─────────┬─────────┘      │   已发送历史)    │
         │                          │                └────────┬────────┘
         ▼                          ▼                         │
   build_profile              retrieve_papers                │
   (LLM 提炼画像，       ┌───────┴────────┐                  │
    按内容哈希缓存)       │  rerank (BM25  │                  │
         │               │  + 向量相似度)  │                  │
         │               └───────┬────────┘                  │
         │                       ▼                           │
         │                 filter（最低分 /                 │
         │                 关键词 / 已发送去重）               │
         │                       │                           │
         │                       ▼                           │
         │            ┌──────────────────────┐               │
         │            │     HarnessAgent      │◄─────────────┘
         │            │  inspect_candidates   │   候选列表
         │            │  inspect_paper        │   + 向量分数
         │            │  submit_digest        │
         │            └──────────┬───────────┘
         │                       │  Digest（结构化 JSON）
         ▼                       ▼
   ┌──────────────────────────────────────┐
   │   construct_email（安全 HTML 渲染）    │
   └──────────────────┬───────────────────┘
                      ▼
            ┌──────────────────┐
            │  通知发送         │
            │  邮件 / Webhook  │
            └──────────────────┘
```

**核心思想**：流水线只负责把廉价信号喂给智能体（向量排序、摘要）；所有*编辑性*决策 —— 推荐什么、每篇理由怎么写、邮件如何组织 —— 都属于智能体。

---

## 📂 项目结构

```
src/zotero_arxiv_daily/
├── protocol.py          # 数据类：Paper、CorpusPaper、RawPaperItem
├── harness.py           # HarnessAgent：智能体循环 + 工具 + Digest + 降级方案
├── construct_email.py   # 安全 HTML 渲染：Digest → 邮件 HTML
├── executor.py          # 编排器：抓取 → 重排 → 智能体 → 发送
├── retriever/           # arXiv、bioRxiv、medRxiv 抓取器
├── reranker/            # 混合重排（BM25 + 向量，本地或 API）
└── notifier.py          # 发送插件（邮件、Webhook）

config/
├── base.yaml            # 完整配置模板
└── custom.yaml          # 你的覆盖配置（默认已 gitignore）

tests/                   # 160+ 测试，ruff 通过
.github/workflows/       # CI + 每日邮件 + keep-alive
```

---

## 🧪 测试

```bash
uv run pytest        # 160+ 测试，覆盖率约 89%
uvx ruff check src/ tests/
```

支持 Python 3.13+。

---

## 🔧 配置参考

| 配置段 | 键 | 说明 |
|--------|-----|------|
| `zotero` | `user_id`、`api_key` | Zotero 账号凭据 |
| `source.arxiv` | `category`、`fallback_days` | arXiv 分类；RSS 为空（周末）时用 API 兜底最近 N 天 |
| `source.biorxiv` / `source.medrxiv` | `category` | bioRxiv / medRxiv 分类 |
| `llm.api` | `key`、`base_url` | LLM 服务商（推荐 OpenRouter） |
| `llm.generation_kwargs` | `model`、`max_tokens` | 智能体模型与生成参数 |
| `llm.language` | `English` / `Chinese` | 邮件语言（界面标签 + 智能体输出） |
| `llm.harness` | `enabled`、`top_k`、`full_text_budget`、`max_steps` | 智能体循环调参 |
| `reranker` | `local` / `api` | 向量后端（模型、批大小、缓存） |
| `email` | `sender`、`receiver`、`receivers`、`smtp_*` | SMTP 发送配置 |
| `executor` | `source`、`reranker`、`max_paper_num`、`min_score`、`keywords_*`、`dedupe_history`、`notifiers` | 流水线控制 |

---

## 🙏 致谢

本项目基于 [TideDra/zotero-arxiv-daily](https://github.com/TideDra/zotero-arxiv-daily) 的完全重写，围绕单一 HarnessAgent 架构重新设计，灵感来自 Claude Code、Codex 和 OpenClaw 的智能体模式。感谢原作者的出色工作。

## 📄 许可证

MIT
