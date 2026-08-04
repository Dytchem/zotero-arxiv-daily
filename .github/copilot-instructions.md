# Copilot Instructions

## Project Overview

Zotero-arXiv-Daily turns your Zotero library into a daily arXiv/bioRxiv/medRxiv
digest email. The Python pipeline does the deterministic work (fetch, embed,
rerank, filter, safe HTML render); an autonomous **Pi agent** (`agent/run.mjs`
+ `agent/ROLE.md`) does the editorial work — what to recommend, why, in what
order, with a Work-quality score (0–10) on every candidate. Runs free on
GitHub Actions.

## Commands

```bash
uv sync                                  # install/sync dependencies
uv run src/zotero_arxiv_daily/main.py    # run the pipeline
uv run pytest                            # tests (skips slow ones)
uvx ruff check src/ tests/               # lint (ruff IS configured, see pyproject.toml)
node --check agent/run.mjs               # syntax-check the Pi agent entry point
```

## Architecture

Orchestrated by `Executor` (`src/zotero_arxiv_daily/executor.py`):

1. **Fetch Zotero corpus** → pyzotero API (empty abstracts fall back to title)
2. **Filter corpus** → `include_path` / `ignore_path` glob patterns
3. **Retrieve new papers** → arXiv RSS (+weekend API fallback), bioRxiv/medRxiv
4. **Rerank** → embedding + optional BM25 hybrid vs corpus, recency-weighted
   (a hint for the agent, not the final ranking)
5. **Filter** → min_score / keywords / sent-history dedupe / max_paper_num
6. **Agent digest** → engine `pi` (default): `node agent/run.mjs` runs the Pi
   coding agent with ROLE.md as the requirements contract and custom tools
   (inspect_candidates / fetch_full_text / inspect_paper paged /
   search_candidates / search_web / compare_papers / finish_reading /
   submit_digest). Pi failure → legacy Python `HarnessAgent` (`harness.py`) →
   plain embedding-order digest.
7. **Render + send** → `construct_email.py` (pure safe HTML render), delivered
   via notifiers (email / webhook), fixed subject.

### Plugin Systems

- **Retrievers** (`retriever/`): `@register_retriever("name")` on a
  `BaseRetriever` subclass; implement `_retrieve_raw_papers()` and
  `convert_to_paper()`; discovered via `get_retriever_cls(name)`.
- **Rerankers** (`reranker/`): `@register_reranker("name")`; `local`
  (sentence-transformers) and `api` (OpenAI-compatible embeddings).
- **Notifiers** (`notifier.py`): `@register_notifier("name")`; built-ins
  `email` (SMTP in `email_sender.py`) and `webhook`.

Follow the existing pattern when adding a plugin: new file, subclass the base,
apply the registration decorator, implement the abstract methods.

## Configuration

Hydra + OmegaConf. `config/default.yaml` composes `base.yaml` (schema/defaults)
+ `custom.yaml` (overrides). Env interpolation: `${oc.env:VAR,default}`.
`llm.harness.engine`: `pi` (default) | `python`. Entry: `@hydra.main`.

## Data Classes

`Paper` / `CorpusPaper` in `protocol.py`. `Paper` is a plain data class —
the old LLM-powered `generate_tldr` / `generate_affiliations` methods are gone.

## Testing Conventions

- pytest monkeypatch + `SimpleNamespace` stubs, no Docker/network.
- Session-scoped Hydra config in `tests/conftest.py`, deep-copied per test.
- Canned factories in `tests/canned_responses.py`.
- Slow tests (`@pytest.mark.slow`, model downloads) excluded by default
  (`addopts = "-m 'not slow'"`).
- Monkeypatch module-level import paths (e.g. `"zotero_arxiv_daily.executor.zotero.Zotero"`).

## Coding Conventions

- **Logging:** `loguru.logger` — never `print()` or stdlib `logging`.
- **Type hints:** modern syntax (`list[Paper]`, `str | None`).
- **Constants:** module-level `UPPER_SNAKE_CASE`; private methods `_`-prefixed.
- **Error handling:** graceful degradation with try/except + fallback; log
  warnings rather than raising.
- **Config injection:** components receive `DictConfig` at init, stored as
  `self.config`.

## Git Workflow

- PRs target the `dev` branch, not `main`.
