# Harness Design — Generator/Evaluator Agent Architecture

> Status: implemented 2026-08-02. Inspired by Anthropic's *Effective harnesses for
> long-running agents*, *Harness design for long-running application development*
> (generator + evaluator, GAN-style), the *Anatomy of an Agent Harness* 12-component
> model, and the agentic-RAG literature. The original repository history is at
> [TideDra/zotero-arxiv-daily](https://github.com/TideDra/zotero-arxiv-daily).

## 1. Why a harness at all?

A raw LLM is a CPU with no RAM, no disk and no I/O. The context window is RAM,
tools are device drivers, files/DBs are disk — and the **harness is the operating
system**. "If you're not the model, you're the harness." (LangChain, via *Anatomy
of an Agent Harness*).

An earlier version of this project was a *workflow*: embedding rerank → per-paper
TLDR → hard-coded HTML template. That is a fixed recipe, not an agent. The rewrite
turned it into a single ReAct-style agent loop — but a single loop with weak
self-evaluation still tends to under-explore and under-edit. This document
describes the next step: a **generator + evaluator** pair, the pattern Anthropic
reports as the biggest quality lever for long-running, taste-dependent tasks.

## 2. Architecture at a glance

```
                     ┌────────────────────────────────────────────┐
                     │              HarnessAgent.generate()       │
                     │                                            │
  Zotero corpus ────►│  build_profile (cached, corpus-hash keyed) │
                     │        │                                   │
  candidates ───────►│        ▼                                   │
  (embedding-ranked) │  ┌──────────────────────────────┐         │
                     │  │  GENERATOR LOOP (max_steps)  │         │
                     │  │  SURVEY → DEEP-DIVE → FOCUS  │         │
                     │  │  → DECIDE → submit_digest    │         │
                     │  └──────────────┬───────────────┘         │
                     │                 │ draft Digest            │
                     │                 ▼                         │
                     │  ┌──────────────────────────────┐         │
                     │  │  EVALUATOR (fresh context,   │         │
                     │  │  no tools)                   │         │
                     │  │  score + issues + verdict    │         │
                     │  └──────────────┬───────────────┘         │
                     │                 │                          │
                     │    verdict?     │                          │
                     │  approve ───────┼───► return Digest        │
                     │  revise  ───────┘                           │
                     │    (rounds < max) ──► generator again      │
                     └────────────────────────────────────────────┘
```

Two agents, one model, two very different jobs:

| Agent | Context | Tools | Job |
|-------|---------|-------|-----|
| **Generator** | profile + candidate list + growing scratchpad | `inspect_candidates`, `inspect_paper`, `search_candidates`, `compare_papers`, `submit_digest` | Explore, decide, write the draft |
| **Evaluator** | profile + candidate list + the draft | **none** | Grade the draft against the profile; return structured feedback |

The evaluator never sees the generator's scratchpad and never writes anything.
Fresh context + no write tools = an independent reviewer, exactly the
"fresh-context evaluator" pattern from Anthropic's long-running-agents work.

## 3. Why this beats a single loop

1. **Exploration vs. editing are different skills.** Searching candidates and
   writing polished prose fight for the same context window. Splitting them gives
   each phase a clean, focused prompt.
2. **Self-evaluation is unreliable.** A model asked "is your own draft good?"
   tends to say yes. An evaluator with fresh context and explicit grading criteria
   (below) is measurably stricter and finds real gaps (Anthropic, Mar 2026).
3. **Structured feedback drives real revision.** The evaluator returns a typed
   `Evaluation` (score, issues, verdict), not prose. The generator can act on it
   deterministically: fix these N issues, then resubmit.
4. **Bounded cost.** Rounds are capped (`max_revisions`, default 2), and an
   `approve` verdict short-circuits. Worst case = 1 extra small LLM call.

## 4. The generator loop

One ReAct loop with a step budget (`max_steps`, default 12) and a **submit gate**:

- **SURVEY**: page through candidates with `inspect_candidates` (embedding score
  is a hint, not a command).
- **DEEP-DIVE**: `inspect_paper` on at least 3 papers before submitting. The
  harness **rejects** premature `submit_digest` calls and asks the agent to keep
  working — this is a hard guardrail, not a suggestion.
- **FOCUS**: `search_candidates` (keyword filter) and `compare_papers`
  (side-by-side) to resolve doubt.
- **DECIDE + WRITE**: a focused set (typically 3-6), each with a specific reason
  tied to the profile; digest written in `llm.language`.
- **SUBMIT**: `submit_digest` returns the draft `Digest`.

Guardrails borrowed from production harness practice:
- step budget (max_steps) — no infinite loops
- observation truncation (abstracts capped, full text capped) — no context poisoning
- submit gate (min 3 inspections) — no lazy one-shot digests
- graceful fallback — any failure degrades to embedding order, email always ships

## 5. The evaluator

Called with a **fresh message list** (system prompt + profile + compact candidate
list + the draft). No tools. It must answer with strict JSON:

```json
{
  "score": 7.5,                    // 0-10 overall quality
  "issues": [
    {"severity": "high", "problem": "Paper X barely matches the profile",
     "suggestion": "Replace with paper Y or shorten the reason"},
    {"severity": "low", "problem": "Subject is generic",
     "suggestion": "Name the specific subfield"}
  ],
  "verdict": "revise"              // "approve" | "revise"
}
```

Criteria embedded in the evaluator prompt:
- **Relevance**: do the picks actually serve the profile topics/methods?
- **Specificity**: is each reason concrete, or generic paraphrase?
- **Coverage**: are any highly-relevant candidates ignored for no reason?
- **Language/format**: consistent language, no index-number references, sane length.

Verdict logic (deterministic, in the harness):
- `approve` → return the draft as the final digest.
- `revise` + rounds < `max_revisions` → feed the issues back into the generator
  ("The reviewer found these problems; fix them and resubmit") and run the
  generator loop again (its scratchpad keeps the previous draft + feedback).
- `revise` + rounds exhausted → return the best draft so far (never empty-handed).
- Evaluator call fails → keep the draft (degrade gracefully).

## 6. Config

```yaml
llm:
  harness:
    enabled: true
    top_k: 100
    full_text_budget: 10
    max_steps: 12
    min_inspections: 3     # submit gate
    max_revisions: 2       # evaluator rounds
    evaluator_enabled: true
```

## 7. Failure modes and safeguards

| Failure | Safeguard |
|---------|-----------|
| Generator loop exhausts steps | return whatever draft exists, else fallback |
| Evaluator returns malformed JSON | treat as `approve` (keep draft) |
| Evaluator call errors | keep draft, log warning |
| Evaluator keeps saying `revise` | hard cap `max_revisions`, return best draft |
| LLM/credentials missing | fallback digest (embedding order), email still ships |
| Empty candidates | no-email path with run report |

## 8. Cost model

- 1 × profile build (cached by corpus hash — free on unchanged corpus)
- generator loop: ~2-6 tool-turns × one completion each
- evaluator: 1 small completion (compact candidate list, no full texts)
- revision round: +1 generator loop +1 evaluator (worst case, capped)

For a typical day: well under ~50k tokens on gpt-5.6-luna class models.
