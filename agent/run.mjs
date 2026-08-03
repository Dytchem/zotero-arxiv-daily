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

import { readFileSync, writeFileSync } from "node:fs";
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

// ---------------------------------------------------------------------------
// Custom tools (closure over the loaded candidates/profile)
// ---------------------------------------------------------------------------

function buildTools(ctx) {
  const { candidates, profile, language, minInspections, digestPath } = ctx;
  const inspected = new Set();

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
          default: 10,
          minimum: 1,
          maximum: 20,
          description: "how many to show (max 20)",
        }),
      }),
      execute: async (_toolCallId, params) => {
        const start = params.start ?? 0;
        const count = Math.min(params.count ?? 10, 20);
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
      name: "inspect_paper",
      label: "Inspect paper",
      description:
        "Read one candidate paper by its index: authors, affiliations when available, abstract, and a WINDOW of the full text. The full text is long — this returns one page (4000 chars from offset) with a progress note. Keep calling with a larger offset to read the next page until you understand the methods, experiments and results. Reading only the first page is not enough to judge a paper's quality.",
      parameters: Type.Object({
        index: Type.Integer({ description: "candidate index" }),
        offset: Type.Integer({
          default: 0,
          description: "character offset into the full text (0 = start, 4000 = next page, ...)",
        }),
      }),
      execute: async (_toolCallId, params) => {
        const p = candidates[params.index];
        if (!p) return textResult(`No candidate at index ${params.index}`);
        inspected.add(params.index);
        const full = p.full_text || "";
        const pageSize = 4000;
        const offset = params.offset ?? 0;
        const page = full.slice(offset, offset + pageSize);
        const total = full.length;
        const meta = [
          `#${params.index} ${p.title}`,
          `Authors: ${(p.authors || []).join(", ")}`,
          `Score: ${p.score ?? "?"} | Source: ${p.source || "?"}`,
          `URL: ${p.url}`,
          `Abstract: ${p.abstract || "(none)"}`,
          "",
          `--- full text (chars ${offset}-${Math.min(offset + pageSize, total)} of ${total}) ---`,
          page || "(no full text available)",
        ].join("\n");
        const more =
          offset + pageSize < total
            ? `\n\nMORE available: call inspect_paper(index=${params.index}, offset=${offset + pageSize}) for the next page`
            : "";
        return textResult(meta + more);
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
        const a = candidates[params.index_a];
        const b = candidates[params.index_b];
        if (!a || !b) return textResult("One of the indexes is out of range.");
        const fmt = (p, i) =>
          `#${i} ${p.title}\n   score ${p.score ?? "?"} | ${(p.authors || []).slice(0, 6).join(", ")}\n   ${(p.abstract || "").slice(0, 700)}`;
        return textResult(`--- Candidate ${params.index_a} ---\n${fmt(a, params.index_a)}\n\n--- Candidate ${params.index_b} ---\n${fmt(b, params.index_b)}`);
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
        if (inspected.size < minInspections) {
          return textResult(
            `You have only inspected ${inspected.size} paper(s) with inspect_paper; ` +
              `the minimum is ${minInspections}. Keep working and call submit_digest again.`
          );
        }
        const picked = new Set((params.papers || []).map((p) => p.index));
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
  return tools;
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
  const minInspections = input.min_inspections ?? 3;
  const maxSteps = input.max_steps ?? 12;
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

  const tools = buildTools({
    candidates,
    profile,
    language,
    minInspections,
    digestPath,
  });

  const role = readFileSync(path.join(AGENT_DIR, "ROLE.md"), "utf8");

  const { session } = await createAgentSession({
    cwd: path.join(AGENT_DIR, ".."),
    agentDir: AGENT_DIR,
    modelRuntime: runtime,
    model,
    thinkingLevel: "medium",
    noTools: "builtin",
    customTools: tools,
    sessionManager: SessionManager.inMemory(),
  });

  const initialPrompt = [
    role,
    "",
    "## Inputs",
    `Research profile:\n${profileText}`,
    `Language: write the digest in ${language}.`,
    `Candidates: ${candidates.length} paper(s) available. Inspect them with the provided tools.`,
    `Quality bar: your recommendation reasons MUST be grounded in the full text you actually read (specific methods, experiments, results) — never a generic abstract paraphrase. Do not recommend a paper you have not inspected with inspect_paper.`,
    `Every recommended paper needs a work_score (0-10); every unpicked candidate needs a work_score in the others array (no exceptions).`,
    `Papers order in the papers array IS the email card order: sort primarily by work_score DESCENDING (ties by relevance, then taste).`,
    `Never refer to papers by candidate index numbers in the intro/reasons/outro — use titles.`,
    `You have up to ${maxSteps} tool-call steps. When done, call submit_digest.`,
  ].join("\n\n");

  await session.prompt(initialPrompt);

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
