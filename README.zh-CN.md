<p align="center">
  <img width="160" height="160" src="assets/logo.svg" alt="Zotero-arXiv-Daily logo">
</p>

<h1 align="center">Zotero-arXiv-Daily</h1>

<p align="center">
  <em>你的专属 AI 学术图书管理员 —— 读懂你的 Zotero 文献库，每天替你追踪 arXiv 新论文，像一位有经验的资深研究者那样，为你挑出值得读的论文。</em>
</p>

<p align="center">
  <a href="https://github.com/Dytchem/zotero-arxiv-daily/actions"><img src="https://img.shields.io/github/actions/workflow/status/Dytchem/zotero-arxiv-daily/ci.yml?style=flat-square" alt="CI"></a>
  <a href="https://github.com/Dytchem/zotero-arxiv-daily/blob/main/LICENSE"><img src="https://img.shields.io/github/license/Dytchem/zotero-arxiv-daily?style=flat-square" alt="License"></a>
  <img src="https://img.shields.io/badge/python-3.13+-blue?style=flat-square" alt="Python">
  <img src="https://img.shields.io/badge/tests-188-brightgreen?style=flat-square" alt="Tests">
</p>

<p align="center">
  <a href="README.md">🌐 English</a>
</p>

---

## 目录

- [这是什么？](#-这是什么)
- [工作原理](#-工作原理)
- [功能特性](#-功能特性)
- [快速开始](#-快速开始)
- [邮件结构](#-邮件结构)
- [架构](#-架构)
- [项目结构](#-项目结构)
- [配置参考](#-配置参考)
- [测试](#-测试)
- [常见问题](#-常见问题)
- [致谢与许可](#-致谢与许可)

---

## 🧠 这是什么？

**Zotero-arXiv-Daily** 是一个 **AI 学术图书管理员**，依托 GitHub Actions 免费运行。每天清晨它替你完成：

1. **从你的 Zotero 文献库学习研究方向**（主题、关键词、方法）。
2. **抓取最新论文**（arXiv / bioRxiv / medRxiv）。
3. **用快速、确定性的计算粗筛**（向量相似度 + BM25 + 时间衰减加权）。
4. **让智能体做最终判断** —— 就像资深研究者浏览新一期论文时那样思考：*这篇论文值得你花时间吗？为什么？*
5. **给你发一封排版精美的 HTML 邮件** —— 主题、开场、每篇的推荐理由、结尾，全部由智能体用你的语言撰写。

> 没有僵硬的流水线，没有逐篇打分的机械流程。计算负责排序，智能体负责决策。

---

## ⚙️ 工作原理

流水线把**廉价计算**和**编辑判断**清晰地分开：

```
arXiv/bioRxiv/medRxiv 订阅源        你的 Zotero 文献库
        │                                    │
        ▼                                    ▼
  ┌─────────────────────────────────────────────────┐
  │  1. 重排（确定性计算，无 LLM）                    │
  │     • 每篇候选论文都做向量化                      │
  │     • 每篇库论文都做向量化（有缓存）               │
  │     • 余弦相似度 × 时间衰减 + 30% BM25 词法分     │
  │       → 得到 0–10 分                             │
  └───────────────────────┬─────────────────────────┘
                          ▼
  ┌─────────────────────────────────────────────────┐
  │  2. 过滤（确定性计算）                            │
  │     • min_score / 关键词包含-排除                 │
  │     • 已发送历史去重（绝不重复推荐）               │
  │     • 保留前 N 篇（max_paper_num，如 30）         │
  └───────────────────────┬─────────────────────────┘
                          ▼
  ┌─────────────────────────────────────────────────┐
  │  3. 智能体（LLM，专家）                           │
  │     • 读取你的研究画像                            │
  │     • 用工具亲自审阅候选论文                      │
  │     • 精选 3–6 篇并撰写推荐理由                   │
  │     • 按编辑价值排列顺序 ——                      │
  │       邮件展示的顺序就是它的判断                  │
  └───────────────────────┬─────────────────────────┘
                          ▼
  ┌─────────────────────────────────────────────────┐
  │  4. 评审器（独立审稿人）                          │
  │     • 全新上下文、无工具                          │
  │     • 给草稿打分（0–10）、列出问题                │
  │     • 通过 → 发送；需修改 → 智能体改进            │
  └───────────────────────┬─────────────────────────┘
                          ▼
              安全 HTML 渲染 → 邮件 + Webhook
```

**核心思想**：向量分数只是一个*提示*。智能体才是专家 —— 它决定推荐什么、每篇怎么说、以及你应该按什么顺序读。

---

## ✨ 功能特性

| 特性 | 说明 |
|------|------|
| 🧠 **单一智能体，完整编辑权** | 一个智能体循环读取你的画像、用工具审阅候选、撰写整封邮件 |
| 🏆 **专家排序** | 邮件卡片的顺序就是智能体的编辑顺序 —— 而不是机械的分数排序 |
| ⭐ **工作水平评分** | 每张卡片都带一个 **工作水平** 徽章（0–10）：智能体对该论文本身质量的评判 —— 严谨性、创新性、方法完备性、作者/机构可信度。即使向量相关度很高，水文/野鸡机构论文也会被揪出来 |
| 🧾 **候选也带标签** | 未入选的论文同样获得 相关度 + 工作水平 两个标签（独立一行显示），并附智能体对这批候选为什么落选的整体点评 |
| 🎯 **品味匹配** | 研究画像不仅提炼主题，还提炼研究者的 *品味* 与质量底线；选稿不仅看关键词重合，更看是否符合你的口味 |
| ⚡ **提示词缓存友好** | agent 循环给稳定的系统提示词打上 prompt-cache 断点，多轮对话命中供应商缓存 —— 更省 token、更低延迟 |
| 🧱 **固定邮件标题** | 邮件主题是固定的 `Zotero-arXiv-Daily … · <日期>` —— 可扫读，绝不自由发挥 |
| 🔧 **真正的工具调用循环** | `inspect_candidates` → `inspect_paper` → `search_candidates` → `compare_papers` → `submit_digest`，带硬性提交门禁（至少深挖 3 篇） |
| ⚖️ **生成 + 评审双智能体** | 独立评审器给每份草稿打分（分数/问题/通过与否）；`revise` 会把问题反馈给生成器修订，最多 `max_revisions` 轮 |
| 📝 **结构化输出** | 智能体提交类型化的 `Digest`（主题 / 开场 / 论文 / 结尾）；渲染层只信任结构，绝不信任 LLM 原文 |
| 🛡️ **安全渲染** | 所有文本字段 HTML 转义，LaTeX 公式转 Unicode（`$\alpha$` → `α`），链接只放行 http(s) |
| 📧 **邮件客户端适配** | 中文字体栈、Outlook 安全的纯色按钮、响应式布局、隐藏 preheader |
| 🌐 **本地化** | 界面标签和邮件语言随 `llm.language` 切换（中文 / English） |
| 📅 **无漏回溯** | `lookback_days`（默认 2）保留昨天+今天 —— 漏跑一天也不会丢前一天的论文 |
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

### 前置条件

- 一个 [Zotero](https://www.zotero.org/) 文献库（有几篇论文即可）
- 一个 LLM API 密钥（如 [OpenRouter](https://openrouter.ai)）
- 一个支持 SMTP 的邮箱（QQ、Gmail、Outlook 等）

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
  api_key: "***"            # Zotero API Key（只读权限）

source:
  arxiv:
    category: ["cs.AI", "cs.LG"]   # 你的研究方向分类
    lookback_days: 2               # 保留昨天+今天（无漏回溯）

llm:
  api:
    key: "sk-or-..."               # OpenRouter API Key
    base_url: "https://openrouter.ai/api/v1"
  generation_kwargs:
    model: "openai/gpt-5.6-luna"   # 推荐的智能体模型
  language: Chinese                # 邮件语言：Chinese | English
  harness:
    enabled: true
    top_k: 100                     # 智能体最多可见的候选数
    full_text_budget: 10           # 顶部候选的全文预取数
    max_steps: 12                  # 智能体循环预算
    min_inspections: 3             # 提交门禁：至少深挖 3 篇
    max_revisions: 2               # 评审改进轮数
    evaluator_enabled: true        # 独立评审器开关

email:
  sender: "you@example.com"
  receiver: "you@outlook.com"
  smtp_server: "smtp.example.com"
  smtp_port: 465
  sender_password: "你的SMTP授权码"

executor:
  source: ["arxiv"]
  reranker: api                    # 'api'（OpenAI 兼容）或 'local'
  max_paper_num: 30                # 给智能体的候选窗口
```

全部配置项见 [`config/base.yaml`](config/base.yaml)。

### 4. 本地调试运行

```bash
DEBUG=true uv run src/zotero_arxiv_daily/main.py
```

渲染结果在 `.cache/last_email.html` —— 调试模式不会真的发信。

### 5. 部署到 GitHub Actions

1. 把你的 Fork 推到 GitHub。
2. **Settings → Secrets and variables → Actions** 添加：
   - `ZOTERO_ID`、`ZOTERO_KEY`
   - `OPENAI_API_KEY`、`OPENAI_API_BASE`
   - `SENDER`、`RECEIVER`、`SENDER_PASSWORD`
3. 可选：设置变量 `CUSTOM_CONFIG`（一段 YAML，合并覆盖默认配置）—— 不用改代码就能调整分类、语言、收件人。
4. 工作流**每天 22:00 UTC 自动运行**（北京时间早 6:00 —— 正好在 arXiv 每日凌晨 4 点发布之后）。可在 `.github/workflows/main.yml` 修改 cron。想立即触发？*Actions → Send emails daily → Run workflow* 手动跑一次。

---

## 📧 邮件结构

一封渲染好的推荐邮件长这样：

| 部分 | 谁写的 | 说明 |
|------|--------|------|
| **主题** | 模板 | 固定格式：`Zotero-arXiv-Daily … · <日期>` —— 稳定、可扫读 |
| **开场** | 智能体 | 今天这批论文的整体情况 |
| **卡片** | 智能体精选 + 排序 | 标题 → 摘要链接、来源徽章、相关度标签、**工作水平标签**、「推荐理由」、PDF/摘要按钮 |
| **其他候选** | 智能体点评 + 流水线 | 未入选论文每篇也带同样的两个标签（独立一行），顶部是智能体对它们为何落选的整体点评 |
| **结尾** | 智能体 | 收尾与展望 |
| **页脚** | 模板 | 退订提示，本地化 |

**卡片的顺序就是智能体的编辑排序** —— 它认为对你最重要的论文排在最前。每张卡片带两个标签：**相关度**（向量/BM25 的廉价主题匹配提示）和**工作水平**（智能体对该论文质量的自主评判 —— 工作是否严谨、新颖、可信）。请更相信工作水平而不是相关度：订阅流里多的是表面相关、实则肤浅或出处可疑的论文。

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
         │            │  search_candidates    │
         │            │  compare_papers       │
         │            │  submit_digest        │
         │            └──────────┬───────────┘
         │                       │  草稿 Digest
         │                       ▼
         │            ┌──────────────────────┐
         │            │   评审器 EVALUATOR    │  fresh context、无工具
         │            │  分数 + 问题 + 判定    │  approve → 完成
         │            │  (revise?)           │  revise → 反馈修订循环
         │            └──────────┬───────────┘
         │                       │  最终 Digest（结构化 JSON）
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

**为什么需要两个智能体？** Anthropic 的研究表明，生成器/评审器模式对口位型任务的质量提升最大。生成器负责探索和撰写；独立的评审器用全新上下文、不带工具地给草稿打分，并驱动改进循环 —— 完整设计见 [`docs/HARNESS.md`](docs/HARNESS.md)。

---

## 📂 项目结构

```
src/zotero_arxiv_daily/
├── protocol.py          # 数据类：Paper、CorpusPaper、RawPaperItem
├── harness.py           # HarnessAgent：生成器循环 + 工具 + 评审器
├── construct_email.py   # 安全 HTML 渲染：Digest → 邮件 HTML
├── executor.py          # 编排器：抓取 → 重排 → 过滤 → 智能体 → 发送
├── retriever/           # arXiv、bioRxiv、medRxiv 抓取器
├── reranker/            # 混合重排（BM25 + 向量，本地或 API）
└── notifier.py          # 发送插件（邮件、Webhook）

config/
├── base.yaml            # 完整配置模板（默认值 + 文档）
├── custom.yaml          # 你的覆盖配置（示例已提交；CI 会覆盖它）
└── default.yaml         # 组合根：base + custom

tests/                   # 170+ 个测试，ruff 干净
docs/HARNESS.md          # 生成器/评审器设计文档
.github/workflows/       # CI + 每日推送 + keep-alive
```

---

## 🔧 配置参考

| 配置段 | 键 | 说明 |
|--------|-----|------|
| `zotero` | `user_id`、`api_key` | Zotero 账户凭据 |
| `zotero` | `include_path`、`ignore_path` | 包含/排除文献库集合的 glob 模式 |
| `source.arxiv` | `category`、`include_cross_list`、`lookback_days`、`fallback_days` | arXiv 分类；保留最近 N 天（无漏）；RSS 为空（周末）时用 API 兜底 |
| `source.biorxiv` / `source.medrxiv` | `category` | bioRxiv / medRxiv 分类 |
| `llm.api` | `key`、`base_url` | LLM 提供商（推荐 OpenRouter） |
| `llm.generation_kwargs` | `model`、`max_tokens` | 智能体模型 + 生成参数 |
| `llm.language` | `Chinese` / `English` | 邮件语言（界面标签 + 智能体输出） |
| `llm.harness` | `enabled`、`top_k`、`full_text_budget`、`max_steps`、`min_inspections`、`max_revisions`、`evaluator_enabled` | 智能体循环调优 |
| `reranker` | `local` / `api` | 向量后端（模型、batch_size、cache_dir） |
| `executor` | `rerank_alpha` | 混合权重：1.0 = 纯向量，0.0 = 纯 BM25，null = 仅向量 |
| `executor` | `min_score`、`keywords_include`、`keywords_exclude` | 智能体前的确定性过滤 |
| `executor` | `max_paper_num`、`dedupe_history`、`cache_dir` | 候选窗口、去重开关、状态文件位置 |
| `executor` | `notifiers` | 发送渠道：`['email']`、`['email', 'webhook']` |
| `email` | `sender`、`receiver`、`receivers`、`smtp_*` | SMTP 发送（可加抄送收件人） |

---

## 🧪 测试

```bash
uv run pytest        # 170+ 个测试，覆盖率约 89%
uvx ruff check src/ tests/
```

支持 Python 3.13+。

---

## ❓ 常见问题

**论文到底是怎么选出来的？**
流水线先给每篇候选论文和每篇库论文做向量化，计算匹配分（余弦 × 时间衰减 + BM25），再经过确定性过滤（最低分、关键词、已发送历史），把幸存的前 N 篇交给智能体。最终推荐什么，由智能体 —— 而不是公式 —— 决定。

**邮件里的顺序是谁定的？**
智能体。它被明确要求像资深研究者一样排序：对你最重要的排在前面。向量分数只是提示，不是排序依据。

**为什么周末也能收到论文？**
arXiv 周末不发布新论文，RSS 是空的。工作流会自动回退到 arXiv API 拉取最近 `fallback_days`（默认 7）天的论文，保证周一的邮件也有内容。

**如果某天运行失败/漏跑了怎么办？**
`lookback_days`（默认 2）会把昨天的论文留在窗口内，加上已发送历史去重 —— 漏跑一天，第二天自动补上，不会重复也不会丢失。

**会不会收到重复论文？**
不会。每封邮件里出现过的论文（无论精选还是「其他候选」）都会记入 `sent_papers.json`，之后运行自动过滤掉。

**怎么切换语言？**
设置 `llm.language: Chinese` 或 `English`。智能体的写作和界面标签都会切换。

**需要什么模型？**
一个 LLM 给智能体用（如 OpenRouter 上的 `openai/gpt-5.6-luna`），可选一个向量 API 做重排（如 `qwen/qwen3-embedding-8b`）—— 或者 `pip install .[local-reranker]` 用本地模型。

**真的免费吗？**
GitHub Actions 免费额度足够公共仓库每日运行。不需要服务器，不需要订阅。

---

## 🙏 致谢与许可

本项目是对 [TideDra/zotero-arxiv-daily](https://github.com/TideDra/zotero-arxiv-daily) 的彻底重写，围绕生成器/评审器双智能体架构重建 —— 借鉴了 Claude Code、Codex、OpenClaw 的智能体模式，以及 Anthropic 的 harness 研究。感谢原作者。

**许可**：MIT —— 自由使用。但请记住这是一个个人科研工具：你的 Zotero 数据和邮箱地址属于你自己，请妥善保管。
