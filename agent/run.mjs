#!/usr/bin/env node
// agent/run.mjs — Pi-agent entry point for the daily digest.
//
// Reads a JSON input file (candidates + research profile), runs the Pi coding
// agent with ROLE.md as the system prompt and custom research tools
// (inspect_candidates / inspect_paper / search_candidates / compare_papers /
// submit_digest), and writes the digest JSON that the Python pipeline reads
// back. Exit code 0 with a digest file = success; anything else lets the
// Python side fall back to the embedding-order digest.
//
// Usage:
//   OPENAI_API_KEY=... node run.mjs --input in.json --output digest.json
//
// The provider is custom (see models.json): apiKey comes from $OPENAI_API_KEY
// (env interpolation), baseUrl points at OpenRouter. The repo deliberately has
// no OPENROUTER_API_KEY — the built-in openrouter provider reads that var and
// would find nothing, so we define our own.

import { existsSync, readFileSync, writeFileSync } from "node:fs";
import path from "node:path";
import { fileURLToPath } from "node:url";

import {
  createAgentSession,
  SessionManager,
  ModelRuntime,
} from "@earendil-works/pi-coding-agent";
import { Type } from "@earendil-works/pi-ai";

const __dirname = path.dirname(fileURLToPath(import.meta.url));
const AGENT_DIR = __dirname;
const DEFAULT_MODEL = "openai/gpt-5.6-luna";

function parseArgs(argv) {
  const args = { input: null, output: null };
  for (let i = 2; i < argv.length; i++) {
    if (argv[i] === "--input") args.input = argv[++i];
    else if (argv[i] === "--output") args.output = argv[++i];
  }
  if (!args.input || !args.output) {
    console.error("usage: node run.mjs --input <in.json> --output <digest.json>");
    process.exit(2);
  }
  return args;
}

function textResult(text) {
  return { content: [{ type: "text", text }], details: {} };
}

// Debug log for every tool invocation (stderr, visible in workflow logs).
function toolLog(name, params) {
  const brief = params
    ? JSON.stringify(params).slice(0, 200)
    : "";
  console.error(`[tool] ${name} ${brief}`);
}

// Wraps a tool's execute() to log elapsed time per call — the workflow log
// then shows exactly how long each agent step (and each fetch) took.
function timed(tool) {
  const exec = tool.execute;
  tool.execute = async (...args) => {
    const t0 = Date.now();
    const result = await exec(...args);
    const dt = Date.now() - t0;
    console.error(`[tool:done] ${tool.name} ${dt}ms`);
    return result;
  };
  return tool;
}

// ---------------------------------------------------------------------------
// Custom tools (closure over the loaded candidates/profile)
// ---------------------------------------------------------------------------

function buildTools(ctx) {
  const { candidates, profile, language, digestPath, cacheDir, maxSteps, fullTextCacheMax, webSearchBudget, model } = ctx;
  const inspected = new Set();
  // Deep-read tracking: candidate index -> highest character offset actually
  // read via inspect_paper. Guarantees the agent READ the papers it recommends
  // instead of skimming titles/abstracts.
  const readDepth = new Map();
  // Structured reading notes: candidate index -> {methods, experiments,
  // results, limitations, confidence}. Optional intermediate artifact —
  // scoring and recommendations are expected to be grounded in these, but
  // the digest contract itself does not require them (some papers have no
  // accessible full text).
  const readingNotes = new Map();
  // Shared full-text disk cache (also written by the Python pipeline), so
  // texts fetched by either side are reused by the other. Located under the
  // configured cache_dir (default .cache) — same file the Python side uses.
  const fullTextCachePath = path.join(cacheDir, "full_texts.json");
  // Hard step budget: the SDK has no maxSteps option, so we count tool
  // invocations ourselves and refuse to continue past the budget — the
  // agent must submit (or the run ends and the Python side falls back).
  let stepsUsed = 0;
  const budgetExceeded = () => stepsUsed >= maxSteps;
  const stepMessage =
    `Step budget exhausted (${maxSteps} steps). You must call submit_digest NOW with what you have — no more tools.`;
  // Web-search quota: FREE tier is limited; cap per-run usage so a runaway
  // agent cannot burn the budget on trivia.
  let webSearchesUsed = 0;

  const tools = [
    {
      name: "inspect_candidates",
      label: "Inspect candidates",
      description:
        "List the day's candidate papers with their embedding relevance score and a short abstract. Call this to see what is available before deciding what to recommend. Page through with start/count.",
      parameters: Type.Object({
        start: Type.Integer({
          default: 0,
          description: "0-based start index",
        }),
        count: Type.Integer({
          default: 20,
          minimum: 1,
          maximum: 50,
          description: "how many to show (max 50)",
        }),
      }),
      execute: async (_toolCallId, params) => {
        toolLog("TOOL", params);
        if (budgetExceeded()) return textResult(stepMessage);
        stepsUsed++;
        const start = params.start ?? 0;
        const count = Math.min(params.count ?? 20, 50);
        const slice = candidates.slice(start, start + count);
        const lines = slice.map((p, i) => {
          const idx = start + i;
          const authors = (p.authors || []).slice(0, 8).join(", ");
          const ab = p.abstract || "";
          const abShort = ab.length > 400 ? ab.slice(0, 400) + "…" : ab;
          return `${idx}. [score ${p.score ?? "?"}] ${p.title}\n   ${authors}\n   ${abShort}`;
        });
        const total = candidates.length;
        const end = Math.min(start + count, total);
        const more =
          end < total
            ? `\n\nMore: call inspect_candidates with start=${end}`
            : "\n\n(end of list)";
        return textResult(
          `Candidates ${start}-${end - 1} of ${total}:\n\n` +
            lines.join("\n\n") +
            more
        );
      },
    },
    {
      name: "fetch_full_text",
      label: "Fetch full text",
      description:
        "Download and extract the full text of a candidate paper (by index) using the command line. The pipeline does NOT preload full texts — you decide what to read and fetch it yourself. Returns the extracted text (or a cache hit) and lets you then read it page by page with inspect_paper. Papers with no accessible full text return an error; you may still judge them from the abstract with lower confidence.",
      parameters: Type.Object({
        index: Type.Integer({ description: "candidate index" }),
      }),
      execute: async (_toolCallId, params) => {
        toolLog("TOOL", params);
        if (budgetExceeded()) return textResult(stepMessage);
        stepsUsed++;
        const p = candidates[params.index];
        if (!p) return textResult(`No candidate at index ${params.index}`);
        if (p.full_text) {
          return textResult(
            `Already fetched (${p.full_text.length} chars). Use inspect_paper(index=${params.index}, offset=0) to read page by page.`
          );
        }
        const cachePath = fullTextCachePath;
        // Reuse the shared disk cache first (the Python side may have prefetched).
        try {
          if (existsSync(cachePath)) {
            const cache = JSON.parse(readFileSync(cachePath, "utf8"));
            const cached = cache[p.url];
            if (typeof cached === "string" && cached.length > 0) {
              p.full_text = cached;
              return textResult(
                `Loaded ${cached.length} chars for #${params.index} from cache. Use inspect_paper(index=${params.index}, offset=0) to read page by page.`
              );
            }
          }
        } catch {
          // cache read failure is non-fatal — fetch fresh below
        }
        const { execFile } = await import("node:child_process");
        const { promisify } = await import("node:util");
        const execFileP = promisify(execFile);
        try {
          const { stdout, stderr } = await execFileP(
            "uv",
            [
              "run",
              "python",
              path.join(AGENT_DIR, "fetch_text.py"),
              p.url,
              p.pdf_url || "",
              p.source_url || "",
            ],
            {
              cwd: path.join(AGENT_DIR, ".."),
              timeout: 240000,
              maxBuffer: 64 * 1024 * 1024,
            }
          );
          if (stdout && stdout.trim()) {
            p.full_text = stdout;
            try {
              const cache = existsSync(cachePath)
                ? JSON.parse(readFileSync(cachePath, "utf8"))
                : {};
              cache[p.url] = stdout;
              // Bound the cache like the Python side (full_text_cache_max)
              // so it cannot grow without limit across many runs.
              const keys = Object.keys(cache);
              if (keys.length > fullTextCacheMax) {
                for (const k of keys.slice(0, keys.length - fullTextCacheMax)) {
                  delete cache[k];
                }
              }
              // Atomic write (tmp + rename) so a crash mid-write cannot
              // corrupt the shared cache the Python side also reads.
              const tmp = `${cachePath}.${process.pid}.tmp`;
              writeFileSync(tmp, JSON.stringify(cache), "utf8");
              const { renameSync } = await import("node:fs");
              renameSync(tmp, cachePath);
            } catch {
              // cache write failure is non-fatal
            }
            return textResult(
              `Fetched ${stdout.length} chars for #${params.index}. Use inspect_paper(index=${params.index}, offset=0) to read page by page.`
            );
          }
          return textResult(
            `No full text available for #${params.index} (${p.title}). stderr: ${(stderr || "").slice(0, 200)}. You may judge it from the abstract with lower confidence.`
          );
        } catch (err) {
          return textResult(
            `Fetch failed for #${params.index}: ${String(err.message || err).slice(0, 300)}. You may judge it from the abstract with lower confidence.`
          );
        }
      },
    },
    {
      name: "inspect_paper",
      label: "Inspect paper",
      description:
        "Read one candidate paper by its index: authors, affiliations when available, abstract, and a WINDOW of the full text. The full text is long — this returns one page (8000 chars from offset) with a progress note. Keep calling with a larger offset to read the next page until you understand the methods, experiments and results. Reading only the first page is not enough to judge a paper's quality. For VERY long papers you can delegate reading to a sub-agent via summarize_paper instead of paging through everything.",
      parameters: Type.Object({
        index: Type.Integer({ description: "candidate index" }),
        offset: Type.Integer({
          default: 0,
          description: "character offset into the full text (0 = start, 4000 = next page, ...)",
        }),
      }),
      execute: async (_toolCallId, params) => {
        toolLog("TOOL", params);
        if (budgetExceeded()) return textResult(stepMessage);
        stepsUsed++;
        const p = candidates[params.index];
        if (!p) return textResult(`No candidate at index ${params.index}`);
        if (!p.full_text) {
          return textResult(
            `#${params.index} (${p.title}) has no full text loaded yet. ` +
              `Call fetch_full_text(index=${params.index}) first to download and ` +
              `extract it (or use bash yourself), then inspect_paper again.`
          );
        }
        inspected.add(params.index);
        const full = p.full_text || "";
        const pageSize = 8000;
        // Clamp the offset: negative offsets (JS slice counts from the end)
        // and offsets past the end would otherwise silently return garbage or
        // an empty page while still marking the paper as "read to the end" —
        // which would let the agent fake full reads. Reject them instead.
        const offset = Number.isFinite(params.offset) && params.offset > 0 ? Math.floor(params.offset) : 0;
        if (offset >= full.length) {
          return textResult(
            `offset ${offset} is past the end of #${params.index} (${full.length} chars). ` +
              `Valid offsets: 0..${Math.max(0, full.length - 1)}.`
          );
        }
        const page = full.slice(offset, offset + pageSize);
        const total = full.length;
        const readThrough = Math.min(offset + pageSize, total);
        readDepth.set(
          params.index,
          Math.max(readDepth.get(params.index) || 0, readThrough)
        );
        // Abstract + metadata only on the first page — repeating them every
        // page wastes tokens on a long multi-page read.
        const meta = [
          `#${params.index} ${p.title}`,
          `Authors: ${(p.authors || []).join(", ")}`,
          `Score: ${p.score ?? "?"} | Source: ${p.source || "?"}`,
          `URL: ${p.url}`,
        ];
        if (offset === 0) {
          meta.push(`Abstract: ${p.abstract || "(none)"}`);
        }
        meta.push(
          "",
          `--- full text (chars ${offset}-${Math.min(offset + pageSize, total)} of ${total}) ---`,
          page || "(no full text available)"
        );
        const more =
          offset + pageSize < total
            ? `\n\nMORE available: call inspect_paper(index=${params.index}, offset=${offset + pageSize}) for the next page`
            : "";
        return textResult(meta.join("\n") + more);
      },
    },
    {
      name: "search_web",
      label: "Search the web",
      description:
        "Search the web (AnySearch API) for background on a paper, authors, institution, venue, or any claim you want to verify before judging work quality. Useful to check provenance, citations, or whether a result is well known. Budget note: FREE tier = 1,000 requests/day (20 QPS) with an API key, lower limits anonymously; each digest run should use at most a handful of searches, and only when it genuinely changes your judgement.",
      parameters: Type.Object({
        query: Type.String({ description: "what to search for, e.g. the paper title, an author, or a claim to verify" }),
        max_results: Type.Optional(
          Type.Integer({ default: 5, minimum: 1, maximum: 10, description: "how many results to return" })
        ),
      }),
      execute: async (_toolCallId, params) => {
        toolLog("TOOL", params);
        if (budgetExceeded()) return textResult(stepMessage);
        stepsUsed++;
        const q = String(params.query || "").trim();
        if (!q) return textResult("Empty query.");
        if (webSearchesUsed >= webSearchBudget) {
          return textResult(
            `search_web quota exhausted (${webSearchBudget} searches this run). ` +
              `Stop searching — judge from the papers themselves.`
          );
        }
        webSearchesUsed++;
        const maxResults = Math.min(Math.max(params.max_results ?? 5, 1), 10);
        const apiKey = process.env.ANYSEARCH_API_KEY || "";
        try {
          const controller = new AbortController();
          const timer = setTimeout(() => controller.abort(), 30000);
          const resp = await fetch("https://api.anysearch.com/v1/search", {
            method: "POST",
            headers: {
              "Content-Type": "application/json",
              ...(apiKey ? { Authorization: `Bearer ${apiKey}` } : {}),
            },
            body: JSON.stringify({ query: q, max_results: maxResults, content_types: ["web"] }),
            signal: controller.signal,
          });
          clearTimeout(timer);
          if (!resp.ok) {
            return textResult(`search_web failed: HTTP ${resp.status} ${(await resp.text()).slice(0, 200)}`);
          }
          const data = await resp.json();
          const items = (data.data || data.results || []).slice(0, maxResults);
          if (!items.length) {
            return textResult(`No web results for "${q}".`);
          }
          const lines = items.map((r, i) => {
            const title = r.title || r.name || "(no title)";
            const url = r.url || r.link || "";
            const snippet = (r.snippet || r.content || "").slice(0, 300);
            return `${i + 1}. ${title}\n   ${url}\n   ${snippet}`;
          });
          return textResult(`Web results for "${q}":\n\n` + lines.join("\n\n"));
        } catch (err) {
          return textResult(
            `search_web error: ${String(err.message || err).slice(0, 300)}`
          );
        }
      },
    },
    {
      name: "search_candidates",
      label: "Search candidates",
      description:
        "Filter the candidate list by keywords (title + abstract substring match, case-insensitive). Returns only the matching papers. Use this to focus on a topic.",
      parameters: Type.Object({
        query: Type.String({ description: "keyword(s) to match" }),
      }),
      execute: async (_toolCallId, params) => {
        toolLog("TOOL", params);
        if (budgetExceeded()) return textResult(stepMessage);
        stepsUsed++;
        const q = (params.query || "").toLowerCase().trim();
        if (!q) return textResult("Empty query.");
        const hits = [];
        for (let i = 0; i < candidates.length; i++) {
          const p = candidates[i];
          const hay = `${p.title} ${p.abstract || ""}`.toLowerCase();
          if (hay.includes(q)) hits.push(i);
        }
        if (!hits.length) return textResult(`No candidates match "${params.query}".`);
        const lines = hits.map((i) => {
          const p = candidates[i];
          return `${i}. [score ${p.score ?? "?"}] ${p.title}`;
        });
        return textResult(`Matches for "${params.query}":\n` + lines.join("\n"));
      },
    },
    {
      name: "compare_papers",
      label: "Compare papers",
      description:
        "Side-by-side view (title, score, abstract) of two candidates so you can weigh which one to recommend.",
      parameters: Type.Object({
        index_a: Type.Integer(),
        index_b: Type.Integer(),
      }),
      execute: async (_toolCallId, params) => {
        toolLog("TOOL", params);
        if (budgetExceeded()) return textResult(stepMessage);
        stepsUsed++;
        const a = candidates[params.index_a];
        const b = candidates[params.index_b];
        if (!a || !b) return textResult("One of the indexes is out of range.");
        const fmt = (p, i) =>
          `#${i} ${p.title}\n   score ${p.score ?? "?"} | ${(p.authors || []).slice(0, 6).join(", ")}\n   ${(p.abstract || "").slice(0, 700)}`;
        return textResult(`--- Candidate ${params.index_a} ---\n${fmt(a, params.index_a)}\n\n--- Candidate ${params.index_b} ---\n${fmt(b, params.index_b)}`);
      },
    },
    {
      name: "summarize_paper",
      label: "Summarize a long paper (sub-agent)",
      description:
        "For a LONG paper, delegate reading to a sub-agent: it chunks the full text, summarizes each chunk (methods / experiments / results / limitations), and returns consolidated notes — without flooding your context with the whole text. Use this when a paper is very long and you need the gist before deciding; then inspect_paper specific offsets for details you want verbatim.",
      parameters: Type.Object({
        index: Type.Integer({ description: "candidate index" }),
        focus: Type.Optional(
          Type.String({ description: "optional focus, e.g. 'the nonadiabatic method' or 'the main numerical result'" })
        ),
      }),
      execute: async (_toolCallId, params) => {
        toolLog("TOOL", params);
        if (budgetExceeded()) return textResult(stepMessage);
        stepsUsed++;
        const p = candidates[params.index];
        if (!p) return textResult(`No candidate at index ${params.index}`);
        const full = p.full_text || "";
        if (!full) {
          return textResult(
            `#${params.index} has no full text loaded yet. Call fetch_full_text(index=${params.index}) first.`
          );
        }
        const apiKey = process.env.OPENAI_API_KEY;
        if (!apiKey) return textResult("OPENAI_API_KEY not set; cannot run sub-agent. Use inspect_paper instead.");
        const CHUNK = 8000;
        const chunks = [];
        for (let i = 0; i < full.length; i += CHUNK) chunks.push(full.slice(i, i + CHUNK));
        const baseUrl = (process.env.OPENAI_API_BASE || "https://openrouter.ai/api/v1").replace(/\/$/, "");
        const notes = [];
        for (let ci = 0; ci < chunks.length; ci++) {
          const sys =
            "You are a meticulous research librarian. Given a chunk of a paper's full text, extract: METHODS (specific techniques), EXPERIMENTS/RESULTS (specific numbers/systems/findings), LIMITATIONS, and a ONE-LINE takeaway. Be concrete and grounded in the text; do not invent. Reply in the digest language.";
          const usr = `Paper: ${p.title}\n${params.focus ? `Focus: ${params.focus}\n` : ""}Chunk ${ci + 1}/${chunks.length}\n${chunks[ci]}`;
          const resp = await fetch(`${baseUrl}/chat/completions`, {
            method: "POST",
            headers: { "Content-Type": "application/json", Authorization: `Bearer ${apiKey}` },
            body: JSON.stringify({
              model,
              messages: [
                { role: "system", content: sys },
                { role: "user", content: usr },
              ],
              max_tokens: 700,
            }),
            signal: AbortSignal.timeout(60000),
          });
          if (!resp.ok) {
            return textResult(
              `Sub-agent summary failed at chunk ${ci + 1}: HTTP ${resp.status} ${(await resp.text()).slice(0, 150)}. Use inspect_paper instead.`
            );
          }
          const data = await resp.json();
          const text = data.choices?.[0]?.message?.content || "(empty)";
          notes.push(`--- chunk ${ci + 1}/${chunks.length} ---\n${text}`);
        }
        return textResult(
          `Sub-agent reading of #${params.index} (${p.title}, ${full.length} chars → ${chunks.length} chunks):\n\n` +
            notes.join("\n\n")
        );
      },
    },
    {
      name: "finish_reading",
      label: "Finish reading (record notes)",
      description:
        "After you have READ a paper (via inspect_paper or summarize_paper), record your structured reading notes: what methods it uses, what experiments/results it reports, its limitations, and how confident you are in your assessment. This anchors your work_score and recommendation reason in what you actually read. Call it once per paper you seriously consider — it is strongly recommended, though the digest contract itself does not require it (some papers may have no accessible full text).",
      parameters: Type.Object({
        index: Type.Integer({ description: "candidate index" }),
        methods: Type.String({ description: "the methods/techniques the paper uses (from the full text, be specific)" }),
        experiments: Type.String({ description: "key experiments/results/evidence the paper reports (specific numbers, systems, findings)" }),
        limitations: Type.String({ description: "limitations, gaps, or weaknesses you noticed (or 'none obvious' if truly none)" }),
        confidence: Type.Number({
          description: "0-10: how confident you are in this assessment (10 = read the full paper carefully)",
        }),
      }),
      required: ["index", "methods", "experiments", "limitations", "confidence"],
      execute: async (_toolCallId, params) => {
        toolLog("TOOL", params);
        if (budgetExceeded()) return textResult(stepMessage);
        stepsUsed++;
        const p = candidates[params.index];
        if (!p) return textResult(`No candidate at index ${params.index}`);
        const read = readDepth.get(params.index) || 0;
        const total = (p.full_text || "").length;
        // Soft nudge, not a hard gate: a paper read via summarize_paper may have
        // a small readDepth (paged reads) but the sub-agent covered the whole
        // text — so only warn when NOTHING was read at all.
        if (total > 0 && read === 0 && !readingNotes.has(params.index)) {
          return textResult(
            `You have not read #${params.index} yet (0/${total} chars via inspect_paper). ` +
              `Read it first with inspect_paper (or summarize_paper for very long papers) — ` +
              `notes must be grounded in the actual content.`
          );
        }
        if (String(params.methods || "").trim().length < 20) {
          return textResult(
            "Your methods note is too thin — list the actual methods/techniques from the paper (at least a few words each, specific, not generic)."
          );
        }
        if (String(params.experiments || "").trim().length < 20) {
          return textResult(
            "Your experiments/results note is too thin — summarize the actual evidence, findings, or systems in the paper (specific, not generic)."
          );
        }
        readingNotes.set(params.index, {
          methods: String(params.methods).trim(),
          experiments: String(params.experiments).trim(),
          limitations: String(params.limitations || "none obvious").trim(),
          confidence: Math.min(Math.max(Number(params.confidence) || 0, 0), 10),
        });
        return textResult(
          `Notes recorded for #${params.index} (${p.title}). ` +
            `You may now base your work_score and recommendation on them.`
        );
      },
    },
    {
      name: "submit_digest",
      label: "Submit digest",
      description:
        "Submit the final digest: your editorial decision about which papers to recommend and the full email content. Call this once when done. It ends the loop.",
      parameters: Type.Object({
        subject: Type.String({ description: "email subject line" }),
        intro: Type.String({ description: "opening paragraph" }),
        papers: Type.Array(
          Type.Object({
            index: Type.Integer(),
            reason: Type.String({ description: "why this paper matters to the user" }),
            tldr: Type.Optional(Type.String({ description: "optional one-line takeaway" })),
            work_score: Type.Number({
              description:
                "your quality judgement of the paper's work itself, 0-10: how rigorous, novel and trustworthy it is (method soundness, experimental completeness, author/institution provenance, venue). High embedding relevance does NOT mean high work quality.",
            }),
          }),
          { minItems: 0 }
        ),
        outro: Type.String({ description: "closing paragraph" }),
        others_summary: Type.Optional(
          Type.String({ description: "overall comment on the unpicked candidates (2-4 sentences)" })
        ),
        others: Type.Optional(
          Type.Array(
            Type.Object({
              index: Type.Integer(),
              work_score: Type.Number({ description: "quality judgement 0-10, same scale" }),
              note: Type.Optional(Type.String({ description: "optional one-line why-not note" })),
            })
          )
        ),
      }),
      required: ["subject", "intro", "papers", "outro"],
      execute: async (_toolCallId, params) => {
        toolLog("TOOL", params);
        // Data-contract checks only (the email renderer needs these fields);
        // how the agent got there is its own business.
        const picked = new Set((params.papers || []).map((p) => p.index));
        // Every referenced index must be a real candidate (the renderer maps
        // index -> paper; an out-of-range index would render as "Paper 999").
        const badPicked = (params.papers || []).filter(
          (p) => p.index < 0 || p.index >= candidates.length
        );
        if (badPicked.length) {
          return textResult(
            `Invalid index in papers: ${badPicked.map((p) => p.index).join(", ")} — indexes must be 0..${candidates.length - 1}. Fix and resubmit.`
          );
        }
        const badOthers = (params.others || []).filter(
          (o) => o.index < 0 || o.index >= candidates.length
        );
        if (badOthers.length) {
          return textResult(
            `Invalid index in others: ${badOthers.map((o) => o.index).join(", ")} — indexes must be 0..${candidates.length - 1}. Fix and resubmit.`
          );
        }
        const unpicked = [];
        for (let i = 0; i < candidates.length; i++) {
          if (!picked.has(i)) unpicked.push(i);
        }
        const scored = new Set((params.others || []).map((o) => o.index));
        const missing = unpicked.filter((i) => !scored.has(i));
        if (missing.length) {
          return textResult(
            `Every unpicked candidate needs a work_score in "others". Missing: ${missing.join(", ")}. ` +
              `Add them to others (use the abstract/title evidence you have; be honest about uncertainty) and resubmit.`
          );
        }
        const digest = {
          subject: params.subject,
          intro: params.intro,
          papers: (params.papers || []).map((p) => ({
            index: p.index,
            reason: p.reason,
            tldr: p.tldr || "",
            work_score: p.work_score,
          })),
          outro: params.outro,
          others_summary: params.others_summary || "",
          others: (params.others || []).map((o) => ({
            index: o.index,
            work_score: o.work_score,
            note: o.note || "",
          })),
        };
        writeFileSync(digestPath, JSON.stringify(digest, null, 2), "utf8");
        return {
          content: [{ type: "text", text: "Digest received and saved." }],
          details: {},
          terminate: true,
        };
      },
    },
  ];
  return tools.map(timed);
}

// ---------------------------------------------------------------------------
// Main
// ---------------------------------------------------------------------------

async function main() {
  const args = parseArgs(process.argv);
  const input = JSON.parse(readFileSync(args.input, "utf8"));

  const candidates = input.candidates || [];
  const profile = input.profile || {};
  const language = input.language || "English";
  const modelId = input.model || DEFAULT_MODEL;
  const maxSteps = input.max_steps ?? 300;
  const cacheDir = input.cache_dir || path.join(AGENT_DIR, "..", ".cache");
  const fullTextCacheMax = input.full_text_cache_max ?? 200;
  const webSearchBudget = input.web_search_budget ?? 15;
  const digestPath = args.output;

  if (!candidates.length) {
    console.error("run.mjs: empty candidates");
    process.exit(1);
  }
  if (!process.env.OPENAI_API_KEY) {
    console.error("run.mjs: OPENAI_API_KEY is not set");
    process.exit(1);
  }

  // Custom provider lives in models.json (apiKey=$OPENAI_API_KEY, baseUrl=OpenRouter).
  const runtime = await ModelRuntime.create({
    modelsPath: path.join(AGENT_DIR, "models.json"),
  });
  const model = runtime.getModel("openrouter", modelId);
  if (!model) {
    console.error(`run.mjs: model ${modelId} not found in custom provider`);
    process.exit(1);
  }

  const profileText = [
    profile.topics?.length ? `Topics: ${profile.topics.join(", ")}` : "",
    profile.keywords?.length ? `Keywords: ${profile.keywords.join(", ")}` : "",
    profile.methods?.length ? `Methods: ${profile.methods.join(", ")}` : "",
    profile.summary ? `Summary: ${profile.summary}` : "",
    profile.taste
      ? `Taste / quality bar: ${profile.taste}`
      : "Taste / quality bar: careful researcher: values rigorous, well-sourced work",
  ]
    .filter(Boolean)
    .join("\n");

  // Raw Zotero library (recent papers, newest first) — the agent sees the
  // researcher's actual library, not just the distilled profile, so it can
  // judge interests and taste itself.
  const corpusText = (input.corpus || [])
    .map(
      (c, i) =>
        `${i + 1}. [${c.added || "?"}] ${c.title}\n` +
        `   Paths: ${(c.paths || []).join(", ") || "-"}\n` +
        `   Abstract: ${(c.abstract || "").slice(0, 300)}`
    )
    .join("\n");

  const tools = buildTools({
    candidates,
    profile,
    language,
    digestPath,
    cacheDir,
    maxSteps,
    fullTextCacheMax,
    webSearchBudget,
    model,
  });

  const role = readFileSync(path.join(AGENT_DIR, "ROLE.md"), "utf8");

  const thinkingLevel = input.thinking_level || "max";
  const { session } = await createAgentSession({
    cwd: path.join(AGENT_DIR, ".."),
    agentDir: AGENT_DIR,
    modelRuntime: runtime,
    model,
    thinkingLevel,
    // The agent is a REAL coding agent: it keeps the built-in bash/read
    // tools so it can inspect the repo, check caches, and fetch/read
    // papers itself with the command line. Only destructive write tools
    // are disabled.
    excludeTools: ["edit", "write"],
    customTools: tools,
    sessionManager: SessionManager.inMemory(),
  });

  const initialPrompt = [
    role,
    "",
    "## Inputs",
    `Research profile:\n${profileText}`,
    corpusText
      ? `Zotero library (recent ${(input.corpus || []).length} papers, newest first — this is the researcher's actual library; use it to calibrate what they care about):\n${corpusText}`
      : "",
    `Language: write the digest in ${language}.`,
    `Candidates: ${candidates.length} paper(s) available. The full texts are NOT preloaded — you fetch what you want to read, with fetch_full_text or bash.`,
    "",
    "## Constraints",
    `Never refer to papers by candidate index numbers in the intro/reasons/outro — use titles.`,
    `The papers array order IS the email card order — stronger work first.`,
    `You have up to ${maxSteps} tool-call steps. When done, call submit_digest.`,
  ]
    .filter(Boolean)
    .join("\n\n");

  await session.prompt(initialPrompt);
  // prompt() resolves once the message is accepted, not when the agent
  // finishes its tool loop — wait for the agent to go idle (submit_digest
  // writes the digest file and terminates the loop).
  await session.waitForIdle();

  // The submit_digest tool wrote the digest file; verify before exiting 0.
  // Note: subject may be empty — ROLE.md says the pipeline fixes the subject,
  // so the agent is instructed not to invent one.
  const digest = JSON.parse(readFileSync(digestPath, "utf8"));
  if (!Array.isArray(digest.papers) || typeof digest.intro !== "string" || typeof digest.outro !== "string") {
    console.error("run.mjs: digest file incomplete after agent run");
    process.exit(1);
  }
  console.log(
    `run.mjs: done — ${digest.papers.length} recommended, ${(digest.others || []).length} others scored`
  );
}

main().catch((err) => {
  console.error("run.mjs failed:", err);
  process.exit(1);
});
