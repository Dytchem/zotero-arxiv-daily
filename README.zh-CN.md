<p align="center">
  <img width="140" height="140" src="assets/logo.svg" alt="Zotero-arXiv-Daily logo">
</p>

<h1 align="center">Zotero-arXiv-Daily</h1>

<p align="center">
  <em>你的 AI 学术管家 —— 读你的 Zotero 文献库，每天扫描 arXiv/bioRxiv/medRxiv，用真正的编辑判断力推荐文章。</em>
</p>

<p align="center">
  <a href="https://github.com/Dytchem/zotero-arxiv-daily/actions"><img src="https://img.shields.io/github/actions/workflow/status/Dytchem/zotero-arxiv-daily/ci.yml?style=flat-square" alt="CI"></a>
  <img src="https://img.shields.io/badge/python-3.13+-blue?style=flat-square" alt="Python">
  <img src="https://img.shields.io/badge/tests-195-brightgreen?style=flat-square" alt="Tests">
  <img src="https://img.shields.io/badge/license-MIT-green?style=flat-square" alt="License">
</p>

<p align="center"><a href="README.md">English</a></p>

---

## 它做什么

每天早上，一个 GitHub Actions 工作流（免费、无需服务器）：

1. **学习你的口味** —— 从你的 Zotero 文献库提炼主题、方法，以及你真正阅读的*质量标准*。
2. **抓取最新论文** —— 来自 arXiv、bioRxiv 和 medRxiv。
3. **快速初筛** —— 用确定性的数学方法（向量嵌入 + BM25 + 时效加权）。
4. **让自主 agent 做决定** —— 它自己抓取全文、逐页精读、给每篇论文的*工作质量*打分（0–10），并用你的语言撰写摘要。
5. **发一封精美的 HTML 邮件**：导语、专家排序的卡片（带 **Relevance** 相关度徽章和 **Work** 工作水平徽章），以及完整的"其他候选"区 —— 每篇都打了分，不静默丢弃任何一篇。

数学负责排序，agent 负责判断。嵌入分数只是提示，不是结论。

## 核心特性

- **Pi agent 引擎**（`agent/run.mjs` + `agent/ROLE.md`）—— 一个真正的编码 agent，自带工具：`inspect_candidates`、`fetch_full_text`、`inspect_paper`（分页）、`search_candidates`、`search_web`、`compare_papers`、`submit_digest`。它自己决定读什么、自己抓全文逐页精读，每一条推荐都基于实际内容。
- **工作质量评分** —— 每个候选（选中与否）都有 **Work** 徽章（0–10），评判严谨性、新颖性和来源可信度。水文/低质论文即使看似相关也会被点名。
- **可辩护的排序** —— 更强的工作在前；评审器审计排序倒挂。
- **保证读全文** —— 阅读进度被追踪；一篇论文必须真正读过（而非扫标题）才能被推荐。
- **生成器 + 评审器** —— 独立评审器给每版草稿打分并驱动修订轮次。
- **安全渲染** —— 所有文本字段 HTML 转义、LaTeX→Unicode、链接白名单。agent 只写 JSON，不碰标记语言。
- **优雅降级** —— Pi 失败 → Python harness → 嵌入排序摘要。邮件永远发得出去。
- **无缝隙回溯、已发去重、多来源、多收件人、webhook 通知、中英双语。**

## 快速开始

1. **Fork** 本仓库。
2. **配置** —— 填写 `config/custom.yaml`（已提交示例；CI 会用 `CUSTOM_CONFIG` 变量覆盖它）：
   - Zotero：`user_id`、`api_key`
   - LLM：`OPENAI_API_KEY`、`OPENAI_API_BASE`（推荐 OpenRouter）
   - 邮箱：`SENDER`、`RECEIVER`、`SENDER_PASSWORD`
   - 你的 arXiv 分类：`source.arxiv.category`
3. **运行** —— 工作流每天 22:00 UTC（北京时间 06:00，紧跟 arXiv 凌晨发布）自动触发。也可随时手动：*Actions → Send emails daily → Run workflow*。

本地调试（渲染邮件但不发送）：

```bash
DEBUG=true uv run src/zotero_arxiv_daily/main.py
# 查看 .cache/last_email.html
```

完整配置参考：[`config/base.yaml`](config/base.yaml)。

## 架构

```
Zotero 文献库 ──► 构建画像（LLM，缓存）
数据源 ──► 抓取 ──► 重排（嵌入+BM25）──► 过滤 ──► 前 N 候选
                                                          │
                        ┌─────────────────────────────────▼──────────┐
                        │  Pi agent（Node，agent/run.mjs + ROLE.md）  │
                        │  读画像 + 原始 Zotero 库 + 候选列表          │
                        │  自己抓全文、分页精读、打 Work 0–10 分、提交  │
                        └─────────────────────────────────┬──────────┘
                        （降级：Python HarnessAgent → 嵌入排序）
                                                          ▼
                        construct_email（安全 HTML）──► email / webhook
```

## 项目结构

```
src/zotero_arxiv_daily/   Python 流水线：executor、harness（旧引擎）、
                          construct_email、retrievers、rerankers、notifier
agent/                    Pi agent 引擎：run.mjs、ROLE.md、fetch_text.py
                          （自定义 provider 从环境变量读 OPENAI_API_BASE + OPENAI_API_KEY，
                           不使用内置 provider catalog 以避免 mimo 等被调用）
config/                   base.yaml（schema）+ custom.yaml（覆盖）
tests/                    195 个测试，ruff 干净
docs/HARNESS.md           生成器/评审器设计文档
```

## 上游与许可

基于 [TideDra/zotero-arxiv-daily](https://github.com/TideDra/zotero-arxiv-daily) 的完全重写，围绕自主 agent 架构重建。相比上游的改动见 [Releases](https://github.com/Dytchem/zotero-arxiv-daily/releases)。

**MIT** —— 你的 Zotero 数据和邮箱地址始终属于你；这个工具只是读取它们来给你发一封摘要。
