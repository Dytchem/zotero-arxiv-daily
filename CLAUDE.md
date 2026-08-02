# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Project Overview

Zotero-arXiv-Daily recommends new arXiv/bioRxiv/medRxiv papers based on a user's Zotero library. A single **HarnessAgent** (OpenAI-compatible function-calling loop) reads the library profile, inspects embedding-ranked candidates with its own tools, decides what to recommend and why, and writes the complete digest — subject, intro, per-paper reasons, outro. A thin safe render layer turns that into a polished HTML email. Designed to run as a GitHub Actions workflow at zero cost.

## Commands

```bash
# Run the application
uv run src/zotero_arxiv_daily/main.py

# Run tests (excludes slow tests by default)
uv run pytest

# Run all tests including slow ones
uv run pytest -m ""

# Run a single test
uv run pytest tests/test_utils.py::TestGlobMatch -v

# Install/sync dependencies
uv sync
```

No linter or formatter is configured.

## Architecture

The pipeline feeds cheap signals to ONE agent; every editorial decision is the agent's job (`src/zotero_arxiv_daily/executor.py`):

1. **Fetch Zotero corpus** — retrieves user's library papers via pyzotero API (empty abstracts fall back to the title so PDF imports are not dropped)
2. **Filter corpus** — applies `include_path` / `ignore_path` glob patterns
3. **Retrieve new papers** — fetches from configured sources (arXiv RSS + weekend API fallback, bioRxiv/medRxiv REST API)
4. **Rerank** — embedding (+optional BM25 hybrid) similarity to corpus; this is a *hint* for the agent, not the final ranking
5. **HarnessAgent digest** — `src/zotero_arxiv_daily/harness.py`: one agent loop with tools `inspect_candidates` / `inspect_paper` / `submit_digest`; produces a typed `Digest` (subject / intro / papers[DigestPaper] / outro). Single LLM provider (`llm.api`, e.g. OpenRouter `gpt-5.6-luna`). Falls back to embedding order on any failure.
6. **Render + send** — `construct_email.py` is a pure safe render layer (HTML-escape, LaTeX→Unicode, link whitelist, localised labels via `llm.language`); delivered via notifiers (email / webhook) with the agent's subject line

### Plugin Systems

**Retrievers** (`src/zotero_arxiv_daily/retriever/`): Register via `@register_retriever` decorator, discovered by `get_retriever_cls()`. Each retriever implements `_retrieve_raw_papers()` and `convert_to_paper()`.

**Rerankers** (`src/zotero_arxiv_daily/reranker/`): Register via `@register_reranker` decorator, discovered by `get_reranker_cls()`. Two implementations: `local` (sentence-transformers) and `api` (OpenAI-compatible embeddings endpoint).

**Notifiers** (`src/zotero_arxiv_daily/notifier.py`): Register via `@register_notifier`, discovered by `get_notifier_cls()`. The executor fans out the rendered digest to every channel in `executor.notifiers` (default `['email']`). Built-ins: `email` (SMTP, implemented in `email_sender.py`) and `webhook` (generic JSON POST). Add a channel by subclassing `BaseNotifier` and registering it.

### Configuration

Uses Hydra + OmegaConf. Config is composed from `config/base.yaml` (defaults) + `config/custom.yaml` (user overrides). Environment variables are interpolated via `${oc.env:VAR_NAME,default}` syntax. Entry point uses `@hydra.main`.

### Data Classes

`Paper` and `CorpusPaper` in `src/zotero_arxiv_daily/protocol.py`. `Paper` has LLM-powered methods (`generate_tldr`, `generate_affiliations`) that call the OpenAI API directly.

## Testing

Tests marked `@pytest.mark.slow` require heavy dependencies (e.g., sentence-transformers model download) and are skipped locally by default (`addopts = "-m 'not slow'"` in pyproject.toml). All other tests run with pure Python stubs (no Docker containers needed).

```bash
# Run tests (excludes slow tests)
uv run pytest

# Run all tests including slow ones
uv run pytest -m ""

# Run with coverage
uv run pytest --cov=src/zotero_arxiv_daily --cov-report=term-missing
```

## gstack

Use the `/browse` skill from gstack for all web browsing. Never use `mcp__claude-in-chrome__*` tools.

Available skills: `/office-hours`, `/plan-ceo-review`, `/plan-eng-review`, `/plan-design-review`, `/design-consultation`, `/design-shotgun`, `/design-html`, `/review`, `/ship`, `/land-and-deploy`, `/canary`, `/benchmark`, `/browse`, `/connect-chrome`, `/qa`, `/qa-only`, `/design-review`, `/setup-browser-cookies`, `/setup-deploy`, `/retro`, `/investigate`, `/document-release`, `/codex`, `/cso`, `/autoplan`, `/plan-devex-review`, `/devex-review`, `/careful`, `/freeze`, `/guard`, `/unfreeze`, `/gstack-upgrade`, `/learn`.

If gstack skills aren't working, run `cd .claude/skills/gstack && ./setup` to build the binary and register skills.

## Git Workflow

- PRs should target the `dev` branch, not `main`
- Current development branch: `dev`
