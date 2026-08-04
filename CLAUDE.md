# CLAUDE.md

Guidance for Claude Code when working in this repository.

## Project Overview

Zotero-arXiv-Daily turns your Zotero library into a daily arXiv/bioRxiv/medRxiv
digest email. The pipeline (Python) does the cheap deterministic work —
fetching, embedding, reranking, filtering, safe HTML rendering; the editorial
work — what to recommend, why, in what order — is done by an autonomous
**Pi agent** (`agent/run.mjs` + `agent/ROLE.md`, the repo's innovation: a
requirements-based role contract). Runs free on GitHub Actions.

## Commands

```bash
uv run src/zotero_arxiv_daily/main.py   # run the pipeline (needs config/custom.yaml)
uv run pytest                           # tests (skips slow ones)
uvx ruff check src/ tests/              # lint (line-length 120, ruff config in pyproject.toml)
node --check agent/run.mjs              # syntax-check the Pi agent entry point
```

## Architecture

`src/zotero_arxiv_daily/executor.py` orchestrates:

1. **Fetch Zotero corpus** — pyzotero; empty abstracts fall back to the title
   (PDF imports are kept).
2. **Filter corpus** — `include_path` / `ignore_path` glob patterns.
3. **Retrieve new papers** — arXiv RSS (+weekend API fallback), bioRxiv/medRxiv.
4. **Rerank** — embedding + optional BM25 hybrid vs corpus, recency-weighted
   (a *hint* for the agent, not the final ranking).
5. **Filter** — min_score / keywords / sent-history dedupe / max_paper_num.
6. **Agent digest** — default engine `pi`: `executor._agent_digest_pi` runs
   `node agent/run.mjs` with the Pi coding agent (ROLE.md system prompt,
   custom tools: inspect_candidates / fetch_full_text / inspect_paper (paged)
   / search_candidates / search_web / compare_papers / finish_reading /
   submit_digest). The agent fetches full texts itself, reads them page by
   page, scores every candidate with a Work badge (0–10), and writes a typed
   digest JSON. On any Pi failure it falls back to the legacy Python
   `HarnessAgent` (`harness.py`), then to a plain embedding-order digest.
7. **Render + send** — `construct_email.py` is a pure safe render layer
   (HTML-escape, LaTeX→Unicode, link whitelist, localised labels); delivered
   via notifiers (email / webhook) with a fixed subject.

Key invariants: the render layer never trusts raw LLM text; every digest
degrades gracefully to embedding order so the email always goes out.

## Plugin Systems

- **Retrievers** (`retriever/`): `@register_retriever` + `get_retriever_cls`.
- **Rerankers** (`reranker/`): `@register_reranker`; `local` (sentence-transformers)
  and `api` (OpenAI-compatible embeddings).
- **Notifiers** (`notifier.py`): `@register_notifier`; built-ins `email` / `webhook`.

## Configuration

Hydra + OmegaConf; `config/default.yaml` composes `base.yaml` (schema+defaults)
with `custom.yaml` (overrides, env-interpolated via `${oc.env:...}`).
`llm.harness.engine`: `pi` (default) | `python`. See `config/base.yaml`.

## Data Classes

`Paper`, `CorpusPaper` in `protocol.py`. `Paper` is a plain data class now —
the old LLM-powered TLDR/affiliations methods are gone (the agent owns all
editorial output).

## Testing

Tests use pytest monkeypatch + SimpleNamespace stubs (no Docker, no network).
`tests/conftest.py` provides a session Hydra config copied per test; canned
factories in `tests/canned_responses.py`. Slow tests (`@pytest.mark.slow`,
heavy model downloads) are excluded by default (`addopts = "-m 'not slow'"`).

## Git Workflow

- PRs target `dev`, not `main`.
