# Changelog

All notable changes to this project are documented here.

## [1.6.0] - 2026-08-04

### Changed
- **代码简化与清理（大道至简）**：
  - `agent/models.json` 已删除 —— run.mjs 自 v1.5.8 起使用编程式 `createProvider`（环境变量读 baseUrl/apiKey），不再读取该文件。
  - run.mjs 删除从未读取的 `inspected` 死集合、`buildTools` 中未使用的 `language` 参数；`toolLog` 现在打印真实工具名（之前全部硬编码 "TOOL"，日志无法区分）。
  - executor 传给 run.mjs 的 `input_payload` 删除 `max_steps` / `web_search_budget` 两个死字段（v1.5.6 起 agent 无硬限制，run.mjs 不读它们）。
  - **pool 去重修复**：传给 agent 的 pool 现在也过 `_filter_sent_history` 过滤（debug/reset_history 模式除外）——此前 pool 包含已发论文，agent 可能从池中重复推荐昨天已展示过的论文；现在已发论文不会再次进入 agent 视野。
  - email 发件人显示名从 "Github Action" 改为 "Zotero-arXiv-Daily"；other-candidates 列表标题补上 `_strip_markdown`（与卡片标题一致）。
- **文案："工作水平" → "推荐度"**（英文 Work → Recommendation）：邮件徽章、harness 提示词、README 同步改名，内部字段 `work_score` 不变。
- **成本优化（LLM 费用 ~$0.42 → 目标 $0.13-0.18/次）**：
  - summarize_paper 子代理 CHUNK 8000 → 16000 字符（请求数减半）+ max_tokens 4096 → 2048（输出减半）——13 次子代理调用占 agent 阶段 81% 时长，此改动时长/费用双减；
  - ROLE.md 新增 Reading budget 引导：先基于 abstract 给全池打分，深读只针对 top ~8 候选（软引导，无硬限制，agent 仍可任意深读）——避免 13 篇全量深读的过度消耗；
  - ROLE.md 明确阅读成本纪律：长文优先 summarize_paper、inspect_paper 每篇最多 2-3 页、上下文 ~272k tokens 单价翻倍红线、others note 一句话内可省略；
  - 初始上下文核实：progressive disclosure 早已实现（pool 索引不注入，agent 用 inspect_pool/inspect_candidates 按需拉取），大头是工具结果累积，已通过上述两项控制。
- **公式解析改用成熟库 pylatexenc**（弃用手写 LaTeX 替换表）：数学片段（$...$、\(...\)）交给 pylatexenc 解析，其上叠加薄显示层转 unicode 上下标/prime（CO_2 → CO₂、10^-3 → 10⁻³、A' → A′、MoS$2$ → MoS₂）；普通文本（含 &、_）不被误处理；`\frac{1}{2}` → `1/2`（旧手写版错误输出 `/12`）。
- **others 区块排序修复**：rescued（从池中救回）论文此前 append 在末尾不参与排序，现统一按 embedding 相关度降序排列，与候选区融为一体。
- 版本号 1.5.9 → 1.6.0。


### Changed
- **全池可见（避免高价值论文被略去）**：agent 现在看到当天去重后的**全部**论文，不只是被 min_score/keyword/max_paper_num 过滤后的 top-N 候选。候选区（indices 0..candidate_count-1）信息更足（带 embedding score，排序靠前），其余论文在候选区之后（indices candidate_count..pool_size-1，标记 `[pool]`）。
  - 新增 `inspect_pool` 工具：浏览全池（含候选标记 + score），分页。
  - `search_candidates` 改为搜**全池**（不只候选），避免漏掉被过滤掉的高价值论文。
  - `fetch_full_text` / `inspect_paper` / `compare_papers` / `finish_reading` / `summarize_paper` 的 index 基于 pool，可操作任意一篇当天论文。
  - `submit_digest` 校验：others 全覆盖仅针对**候选区**（index < candidate_count）未选的论文；非候选论文可选评分（agent 觉得值得提就放 others，或直接进 papers 精选）。
  - 初始 prompt 说明全池结构 + 可以推荐 filter 掉的论文。
  - 邮件渲染：originals 传 pool，others 区显示候选未选 + agent 明确放进 others 的非候选（digest.others 中 index >= candidate_count 的）。
  - sent_history 记录实际展示的论文（papers + others 区全部），避免明天重复发。
  - ROLE.md 更新：工具列表 + 全池/候选说明 + others 覆盖语义。

## [1.5.8] - 2026-08-04

### Fixed
- **mimo 模型被偷偷调用问题**（严重）：用户没配置过 mimo，但 OpenRouter 账单大量小 token 请求是 mimo。根因是 `ModelRuntime.create` 硬编码加载全部 builtin provider（内置 openrouter provider 的 303 模型目录含 mimo-v2.5/pro/free），用户的 OPENAI_API_KEY 对 mimo 有效 → Pi 内部辅助功能或模型 fallback 时选中 mimo。
- **修复方案**：弃用 `agent/models.json` 硬编码 openrouter provider；改为编程式 `createProvider({ id:"custom", baseUrl: env OPENAI_API_BASE, auth: { apiKey: envApiKeyAuth("API key", ["OPENAI_API_KEY"]) }, models: [单模型], api: openAICompletionsApi() })` + `runtime.builtins.clear()` + `runtime.models.clearProviders()` → runtime 只留 custom provider 且 available 只剩用户配置的模型，杜绝 fallback 到 xiaomi/mimo 等内置模型。
- **auth 嵌套 bug**（严重）：Pi 的 `checkAuth` 读 `provider.auth.apiKey`，平铺的 `envApiKeyAuth(...)` 会让 auth 检查失败（"No API key found for custom"），导致 Pi agent 静默回退 Python harness。auth 必须嵌套为 `{ apiKey: envApiKeyAuth(...) }`。
- **summarize_paper model 字段 bug**：传 Pi Model 对象而非字符串 ID，改为 `model: typeof model === "string" ? model : model?.id`。

### Changed
- **Provider 安全**：现在完全从环境变量读 baseUrl 和 apiKey（`OPENAI_API_BASE` + `OPENAI_API_KEY`），不使用内置 provider catalog，避免 mimo 等模型被调用。`agent/models.json` 不再使用（保留但 runtime 不再加载它）。

## [1.5.7] - 2026-08-04

### Changed
- **Progressive disclosure for the Zotero library**: the agent now gets ALL
  library papers (no more 50-paper cap) but with truncated abstracts in the
  initial context — a lean index. A new `inspect_library_paper(<index>)`
  tool returns any paper's FULL abstract + collection paths on demand.
  Context stays budgeted while nothing is hidden: the agent decides what to
  expand, matching the progressive-disclosure best practice (Anthropic /
  LangChain context-engineering guidance: keep an index in context, pull
  details when needed, avoid loading everything "just in case").

## [1.5.6] - 2026-08-04

### Changed
- **All hard limits removed** (owner decision): the Pi agent runs free — no
  step budget (`max_steps` no longer enforced), no `search_web` quota. The
  only remaining safety valve is `pi_timeout` on the subprocess (1800s).
  Initial prompt now says "No step limit: keep reading and searching until
  you have enough to judge the batch well."
- `thinking_level` default `max` (reasoning effort), passed through to the
  Pi engine.

## [1.5.5] - 2026-08-04

### Added
- **Sub-agent long-paper reading** (`summarize_paper`): when a paper is very
  long, the Pi agent can delegate reading to a sub-agent that chunks the
  full text and returns structured notes per chunk (methods / experiments /
  results / limitations) — no context flooding. The agent can then
  `inspect_paper` specific offsets verbatim. This is the missing "sub-agent"
  capability: full agentic freedom to read arbitrarily long papers.
- `llm.harness.thinking_level` (default `max`): reasoning effort for the Pi
  engine (minimal/low/medium/high/xhigh/max). `max` = most thorough.

### Changed
- `llm.harness.max_steps` default **100 → 300**: the agent counts its own
  tool invocations; 100 was still too tight for reading several full papers
  AND searching. 300 is a hard safety cap, not a target.
- `pi_timeout` default 900 → 1800s (300 steps + max thinking needs headroom).
- `inspect_paper` page size 4000 → 8000 chars (fewer calls per paper).
- `inspect_candidates` page size max 20 → 50 (see more candidates per call).
- `finish_reading` 50%-read gate → soft nudge (a paper read via
  `summarize_paper` has small readDepth but the sub-agent covered it all;
  only a zero-read paper is rejected).
- ROLE.md: "When NOT to submit an empty digest" — empty is valid only after
  actually doing the work; skimming one page per paper and quitting is a
  budget problem, not a quality verdict. Long papers: delegate to the
  sub-agent instead of flooding context.

## [1.5.4] - 2026-08-04

### Fixed
- **Agent was forced to submit after reading only the first page of each
  paper** (serious): `llm.harness.max_steps` defaulted to 12, and the Pi
  agent counts every tool invocation against that budget. A real run burned
  all 12 steps on 2 list views + 5 full-text fetches + 5 first-page reads —
  so the 5 follow-up `inspect_paper(offset=12000)` calls (second pages) were
  all rejected and the agent had to submit with every paper half-read, with
  no budget left for `search_web` / `compare_papers`. The default is now
  **100** — enough to fully read several papers AND search provenance.

### Changed
- ROLE.md gains a "How to spend your budget (deep, not wide)" section:
  read whole papers (page until the end / methods+results), depth over
  breadth (2–4 fully-read candidates > 10 half-read titles), use
  `search_candidates` / `compare_papers` / `finish_reading` / `search_web`
  instead of only inspect+submit, and verify provenance with `search_web`
  before giving any score ≥ 7.

## [1.5.3] - 2026-08-04

### Fixed
- **Email card titles were not HTML-escaped** (security/robustness):
  `_get_block_html` inlined the raw paper title into the card markup while
  every other text field was escaped — a title carrying `<`, `>` or `&`
  could break the layout or inject markup. Titles are now escaped like all
  other fields (regression test added).
- **Debug mode still sent email** (README promised "debug mode skips
  sending" but the pipeline delivered anyway — a local debug run would
  duplicate the daily email into the real inbox). Debug runs now render and
  archive `last_email.html` only, never send (regression test added).

### Changed
- Python harness `inspect_paper` now sends the abstract only on the first
  page of a multi-page read (was re-sent on every page — token waste),
  matching the Pi engine behavior.

## [1.5.2] - 2026-08-04

### Fixed
- **Pi agent could fake full reads** (serious): `inspect_paper` accepted any
  `offset`, and a huge offset returned an empty page while still marking the
  paper as "read to the end" (`readThrough = min(offset+4000, total)` = total
  on an empty slice) — so `finish_reading`'s 50% gate was trivially bypassed
  without reading anything. Offsets are now clamped, offsets past the end are
  rejected, and negative offsets (JS `slice` counts from the end) no longer
  return garbage.
- **No hard step budget** (serious): the Pi SDK's `createAgentSession` has no
  `maxSteps` option, so `max_steps` was only a soft hint in the prompt — a
  runaway agent could loop until the 900s subprocess timeout. `run.mjs` now
  counts tool invocations itself and refuses to continue past the budget
  (the agent must call `submit_digest`; otherwise the Python side falls back).
- **Full-text cache could grow without limit / corrupt**: the Pi side wrote
  `full_texts.json` with a plain `writeFileSync` (no cap, no atomicity) while
  the Python side bounds it by `full_text_cache_max` and writes atomically.
  The Pi side now applies the same cap and writes via tmp+rename.
- **`search_web` had no quota**: a runaway agent could burn the FREE-tier
  budget on trivia. New `llm.harness.web_search_budget` (default 15) hard-caps
  `search_web` calls per run.
- **Repeated abstract on every page**: `inspect_paper` re-sent the full
  abstract + metadata on every page of a multi-page read (token waste). The
  abstract now appears only on the first page (offset 0).

### Changed
- Pi agent's own tool log (stderr) is now kept at debug level on success, so
  a `DEBUG=true` workflow run can replay what the agent actually did.
- `inspect_candidates` / `fetch_full_text` / `search_candidates` /
  `compare_papers` / `finish_reading` / `search_web` all honour the step
  budget; only `submit_digest` always remains available.
- ROLE.md documents the `search_web` hard cap; README (EN/ZH) config table
  and example gain `web_search_budget`.

## [1.5.1] - 2026-08-04

### Fixed
- **Workflow dedupe cache never updated** (serious): the Actions cache key
  was a fixed `corpus-embeddings`, but `actions/cache` does NOT re-save when
  the primary key hits — so `.cache` (incl. `sent_papers.json`) froze at the
  first run's snapshot and yesterday's papers would be re-sent every day.
  The key is now `corpus-embeddings-${{ github.run_id }}` (with the same
  restore-keys prefix), so every run saves its own updated cache and the
  previous run's snapshot is restored as the fallback.
- **Digest subject date timezone**: the fixed subject now takes its date in
  Asia/Shanghai (owner's timezone), matching the in-email date line. The
  GitHub Actions runner runs in UTC, so `datetime.now()` previously made the
  subject date drift a day from the body (22:00 UTC is already the next day
  in Shanghai).
- **Pi agent full-text cache path**: `fetch_full_text` now respects
  `executor.cache_dir` instead of hard-coding `.cache` — the agent and the
  Python pipeline share the same on-disk cache even with a custom cache dir.
- **Empty-corpus rerank divide-by-zero**: `BaseReranker.rerank` with an empty
  corpus now keeps input order (scores 0) instead of dividing by zero.
- **Tar file-handle leak**: `extract_tex_code_from_tar` now closes each
  extracted file handle (`with tar.extractfile(...)`).

### Changed
- `submit_digest` (Pi agent) validates that every `papers[]`/`others[]`
  index is in range before accepting — an out-of-range index previously
  rendered as a bogus "Paper 999" card.
- `finish_reading` tool description aligned with ROLE.md (recommended, not
  a hard mandatory gate the digest contract refuses on).
- Removed the duplicate `_collect_receivers` in `notifier.py` (dead code;
  the single implementation lives in `email_sender.py`).

## [1.5.0] - 2026-08-04

### Added
- **Pi agent engine** (`agent/`): the digest is now produced by the Pi coding
  agent (`@earendil-works/pi-coding-agent`) driven by `agent/ROLE.md` — the
  repository's own role definition (SURVEY → DEEP-DIVE → FOCUS → DECIDE →
  ORDER → SUBMIT gates). Python keeps the data pipeline; the agent owns every
  editorial decision.
- `agent/run.mjs`: Node entry point — reads candidates + research profile as
  JSON, runs the Pi agent with custom tools (`inspect_candidates` /
  `inspect_paper` with offset pagination / `search_candidates` /
  `compare_papers` / `submit_digest`), writes the digest JSON the Python side
  reads back.
- `agent/models.json`: custom provider (baseUrl → OpenRouter, apiKey from
  `$OPENAI_API_KEY` env interpolation) — the repo has no
  `OPENROUTER_API_KEY`, so the built-in openrouter provider is unusable.
- `llm.harness.engine` config: `pi` (default) or `python` (legacy harness).
  Pi failures (missing node, timeout, malformed digest) degrade to the Python
  harness, then to the embedding-order digest — the daily email always goes
  out.
- Workflow now installs Node deps (`cd agent && npm ci`) before running.

## [1.4.2] - 2026-08-04

### Fixed
- **Defensible card ordering**: the generator prompt now mandates work quality
  descending (tie-break: relevance, then taste fit) with an explicit 0–10
  rubric, and the evaluator audits ordering — a clearly weaker paper listed
  above a stronger one is a high-severity issue. Previously the agent could
  bury a strong paper (e.g. Work 9.0) below weaker picks.
- **Other-candidates coverage**: the prompt now requires a work_score for
  EVERY unpicked candidate (n/a renders as a grey badge when genuinely
  missing), and the evaluator flags gaps. Previously most unpicked papers
  showed no Work badge at all.
- **Fixed subject applied before rendering**: the fixed
  `Zotero-arXiv-Daily … · <date>` subject is set before `render_email`, so
  the HTML title/preheader match the email header.
- **Prompt-cache stats now always logged**: the cached-token summary prints
  even on the normal submit path (previously skipped by the early return).

## [1.4.1] - 2026-08-03

### Added
- **Other-candidates badges + summary**: unpicked papers now render the same
  Relevance + Work chips as picked cards (on their own line, never inline
  with the title), plus an optional per-paper note. `submit_digest` gains
  `others_summary` (the agent's overall comment on why the rest were
  skipped) and `others[]` (reference work_scores for seriously-considered
  rejects).
- **Prompt-cache friendly harness**: the generator's system message now
  carries an explicit `prompt_cache_breakpoint` so the stable prefix hits
  the provider's prompt cache across turns; cached-token usage is logged
  per run (OpenRouter/Anthropic/OpenAI compatible).
- **On-disk full-text cache**: fetched paper full texts are cached in
  `cache_dir/full_texts.json` (keyed by URL, bounded by
  `full_text_cache_max`, default 200) so repeated runs stop re-downloading
  and re-parsing the same PDFs.
- **Fixed subject format**: the email subject is now a stable
  `Zotero-arXiv-Daily … · <date>` (localised), not free-form agent prose.

### Changed
- **Test/formal separation**: manual workflow runs accept a `reset_history`
  input that clears the sent-papers dedupe cache before running — so tests
  see fresh papers instead of "everything already sent". Scheduled (formal)
  runs keep daily dedupe untouched.
- README (EN/ZH) updated; tests 188 passed, ruff clean.

## [1.4.0] - 2026-08-03

### Added
- **Work-quality scoring (Work badge)**: every recommended card now shows a
  `work_score` (0–10) — the agent's judgement of the paper's own merit
  (rigour, novelty, method soundness, provenance). The generator must supply
  it for every pick, the evaluator audits it, and the email renders it as a
  color-coded chip next to Relevance (green ≥ 7, amber 5–7, red < 5; hidden
  when the LLM path is unavailable).
- **Taste-aware research profile**: the profile distillation now also infers
  the researcher's *taste* and quality bar (what they value, which
  provenance they trust, what they'd consider watery). Picks are matched to
  it, not just to topic keywords. Profile cache schema bumped (old caches
  rebuild automatically).
- **On-demand full-text reading**: `inspect_paper` now fetches the PDF full
  text lazily via an injected fetcher, so the agent actually reads paper
  content when judging work quality — not just titles/abstracts. Fetch
  failures degrade gracefully to whatever metadata exists.

### Changed
- Harness system prompt and evaluator prompt reworked: judge on two axes
  (relevance AND work quality), trust the Work chip over the embedding hint,
  order cards by (quality × relevance × taste fit) editorial judgement —
  watery/低质 work is dropped even when it looks on-topic.
- `DigestPaper` gains `work_score`; `submit_digest` requires it per paper.
- `HarnessAgent` accepts an optional `full_text_fetcher` (injected by the
  executor via `_populate_full_text`).
- README (EN/ZH) updated with the Work badge and taste-matching features.

## [1.3.1] - 2026-08-02

### Changed
- **Agent-ranked display order**: the email's recommended cards now render in
  the agent's own editorial order instead of being re-sorted by the embedding
  score. The generator is explicitly instructed to order its picks like an
  experienced researcher — lead with what matters most to this reader — and
  the render layer trusts that judgement.
- `max_paper_num` deployment config raised 10 → 30, giving the agent a wider
  candidate window to choose from (the unused `top_k=100` cap now has room to
  apply once more candidates survive the filters).

## [1.3.0] - 2026-08-02

### Added
- **Lookback time window** (`source.arxiv.lookback_days`, default 2): the arXiv
  retriever now keeps papers from the last N days (yesterday + today) instead
  of only the same-day RSS batch, and the published date is preserved from the
  feed. A missed/failed run no longer loses the previous day's papers — they
  are picked up on the next run and deduped against sent history.
- **API-fallback resilience**: the weekend arXiv API fallback now retries
  transient fetch/parse failures (3 attempts, backoff) instead of silently
  returning nothing on a 429 or a flaky response.
- **Generator + Evaluator two-agent architecture** (docs/HARNESS.md): after the
  generator loop submits a draft, an independent evaluator with fresh context
  and no tools grades it (score 0-10, issues, verdict approve/revise). A
  `revise` verdict feeds the issues back and re-runs the generator, capped at
  `llm.harness.max_revisions` (default 2). The highest-scoring draft wins when
  the budget exhausts.
- New generator tools: `search_candidates` (keyword filter over title+abstract)
  and `compare_papers` (side-by-side of two candidates).
- Hard submit gate: `llm.harness.min_inspections` (default 3) — the agent
  cannot submit before deep-diving that many papers with `inspect_paper`;
  premature submits are rejected and the loop continues.
- Evaluator degradation: if the evaluator call fails, the draft is kept; if
  it keeps saying `revise`, budget exhaustion returns the best-scoring draft.
- Full-width side-by-side PDF/Abstract buttons (100% table, two 50% columns).

### Changed
- System prompt reworked into an explicit SURVEY → DEEP-DIVE → FOCUS → DECIDE
  → WRITE → SUBMIT workflow.
- `llm.harness` gains `min_inspections`, `max_revisions`, `evaluator_enabled`.

### Fixed
- `executor` read `full_text_budget` from the wrong config path
  (`executor.harness` instead of `llm.harness`) — the prefetch budget never
  applied; it now reads the real `llm.harness.full_text_budget`.
- `llm.harness.top_k` was read but never used — the agent now caps the
  candidate window at `top_k` before exploring.
- `datetime.utcnow()` deprecation in the run report replaced with
  timezone-aware `datetime.now(UTC)`.
- `config/custom.yaml` example was missing the `openai/` provider prefix, the
  `lookback_days` / `fallback_days` source options and the harness
  `min_inspections` / `max_revisions` / `evaluator_enabled` knobs — aligned
  with the real deployment config.
- CLAUDE.md claimed "no linter configured" while `pyproject.toml` ships a
  `[tool.ruff]` block — corrected, and the architecture section now lists all
  five agent tools plus the evaluator stage.
- Redundant `_collect_receivers` duplicate in `notifier.py` removed (it now
  imports the single implementation from `email_sender.py`).
- README/README.zh-CN: local debug command was broken
  (`python -m zotero_arxiv_daily.executor --debug` →
  `DEBUG=true uv run src/zotero_arxiv_daily/main.py`), test counts refreshed
  (170+), and the configuration reference table gained `lookback_days`,
  `min_inspections`, `max_revisions`, `evaluator_enabled`, `receivers`,
  `rerank_alpha` and friends.

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
- Render layer strips stray Markdown the agent may leak into prose (`**bold**`,
  `[label](url)`, `` `code` ``, `# headers`) — reasons/intro/outro never show
  raw Markdown syntax in the email.
- Sent-history now records every candidate that made it into the email (picked
  AND "other candidates"), not just the picked ones — papers shown yesterday
  are never re-shown, while fresh papers keep flowing in from the feeds.
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
