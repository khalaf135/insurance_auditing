# How I used AI and evolved the prompts

The five snapshot files below are **not the whole AI-assisted workflow**. They
preserve reusable model instructions. I also used Codex interactively to understand
the data, write code, examine unanswered cases and refine the approach. This guide
summarizes that process without reproducing the full conversation.

There were two kinds of AI use: **development assistance in Codex** and **model
requests made by the program**. The example requests below are paraphrases of my
development requests, not exact historical prompts or new runtime instructions.

## How I worked with AI

1. **Understand before building.** I asked Codex to explain the repository, the
   invoice fields, the H1 labels and the first contract's rules. This helped me
   choose H1 as the development starting point and identify what the JSON needed.
   Example request: *Explain the contract fields and whether they are enough to
   check the invoices against the labels.*

2. **Build a small, testable baseline.** I asked for code to convert H1's TXT
   contract into JSON, then code to check invoices and compare predictions with
   labels. The first converter was a Codex-written parser, not an API call per
   invoice. Example request: *Read the contract TXT, preserve the rules in JSON,
   and make the checker explain when it cannot answer.*
   See [conversion and baseline records](../AI_USAGE.md#001_contract_conversionmd).

3. **Use unanswered cases as feedback.** I asked Codex to inspect small samples
   of unanswered invoices, return to the original clauses, identify missing
   information and propose reusable fixes. I refined both the JSON and the code,
   rather than assuming a larger JSON alone would solve everything. Example
   request: *Review 20 unanswered cases. What is missing in the JSON or checker?
   Add contract-supported rules, preserve the previous results, and compare again.*
   Rates and contractual rules stayed source-grounded; terminology expansions
   were separately recorded assumptions. This was AI-assisted review, not an
   independent expert assessment. See [sample-driven refinement](../AI_USAGE.md#006_review_20_improvementmd).

4. **Add focused fallback instead of asking AI to decide every invoice.** I
   tested an early H1 pilot using Voyage embeddings/reranking and Gemini. I then
   asked for unanswered cases to be grouped by reason and addressed selectively.
   Example request: *If the checker cannot answer, explain why. Route unclear
   descriptions to the appropriate AI task; keep other unsupported cases unanswered.*
   The description prompts evolved from a candidate matcher to separate
   abbreviation and missing-detail routes, then hospital-isolated batches.
   AI proposes a service match; code validates it and calculates the invoice.
   Patient-history and volume uncertainty are handled by deterministic bounds,
   not by giving every blocker its own model. See snapshots [001](001_pilot.md),
   [002](002_reason_routed.md) and [004](004_batched_descriptions.md).

5. **Generalize without sharing contract facts between hospitals.** I asked for
   one extractor that reads each hospital's own document bundle, including
   amendments, into a common JSON structure. Example request: *Use the same
   extraction code for all hospitals, but preserve each hospital's own rates,
   dates, definitions and unresolved wording, with source references.* When a
   unit was unclear, the targeted review returned to original clauses rather
   than guessed from the billed amount. See [003](003_contract_extraction.md)
   and [005](005_source_unit_review.md).

6. **Check the changes and make the work reviewable.** I asked for repeatable
   tests, H1 comparisons, a spending safeguard, cached-answer reuse, clearer code
   organization, evidence-based confidence and the exact submission format.
   Example request: *Keep general fixes, rerun the checks, report answered and
   unanswered counts, and do not hide missing answers.* Codex also helped prepare
   the evaluation and decision log. These tasks often changed code or validation,
   not the model's prompt text; they do not justify inventing extra prompt versions.

## What changed between the saved prompt versions

- **Pilot to reason routes (001 to 002):** moved from selecting a retrieved
  candidate to distinguishing abbreviation expansion from conditional inference
  about missing details, with explicit evidence requirements and abstention.
- **Reason routes to batches (002 to 004):** reused the base instructions and
  added task IDs and a batched response format. Hospital separation and reference
  validation are also enforced by code, not merely requested in a prompt.
- **Extraction and source review (003 and 005):** separate tasks. Extraction
  records source-linked contract facts; unit review checks whether original
  wording can actually resolve an omission. Neither may invent missing terms.

H1 labels and earlier results informed development, so repeated H1 improvement
is not independent validation. Labels are not sent in the runtime model requests.
Normal audit runs replay saved evidence without new API calls. Current extraction
and fallback requests use Gemini through OpenRouter; Voyage was used in the early
pilot, not by the current all-hospital clause-retrieval path.

## Exact prompt snapshots

These five numbered files make the actual prompt evolution reviewable without
restoring obsolete Python implementations. They are documentation snapshots,
not prompt files dynamically loaded by the audit. Runtime Python definitions
remain authoritative.

## Read in order

1. [001 — Pilot](001_pilot.md): historical Voyage retrieval/reranking plus a
   single description-matching prompt and schema.
2. [002 — Reason routes](002_reason_routed.md): abbreviation versus conditional
   price-supported service inference, with explicit abstention and evidence gates.
3. [003 — Contract extraction](003_contract_extraction.md): active multi-document,
   hospital-isolated conversion to evidence-linked facts.
4. [004 — Batched descriptions](004_batched_descriptions.md): active generalization
   of 002 to task batches and separated reference groups.
5. [005 — Source-unit review](005_source_unit_review.md): active source-cited
   resolution of billing-unit extraction uncertainty.

The [AI use and iteration history](../AI_USAGE.md) preserves the original records of
Codex-assisted development, manual reviews, validation fixes, experiments and
limitations. Not every code iteration changed the prompt text. In particular,
later reference gates, cross-fitting and token-validation changes are linked
from the snapshots rather than represented as fabricated prompt versions.

## Provenance and exactness

Snapshots were assembled on 2026-09-05 from current source and the recoverable
pre-refactor backup `insurance_auditing_refactor_QYqZPzk4/before/scripts`.
Numbering follows the documented development sequence; it does not claim that
these Markdown files existed earlier or that separate historical Git commits
were recovered. They are ready to include in version control with the rest of
the work; creating files is not itself a Git commit.

Each snapshot records full source-file SHA-256 values, exact system text,
response schema, request settings and context shaping. Prompt hashes use the
runtime string's UTF-8 bytes, including its stated trailing-newline behavior.
Schema hashes use UTF-8 canonical JSON with sorted keys, no insignificant spaces
and `ensure_ascii=False`. Markdown fences are presentation only. Source hashes
are capture-time identifiers, not claims that runtime files can never change.

The recovered and active reason-route constants, extraction constants and
source-unit system/schema were compared exactly. Batched messages are the same
reason-route strings with the exact batch suffix appended; the response schema
adds `task_id` and the `results` envelope. No full API payloads, invoice records,
credentials or labels were copied into this folder. No API calls were made to
assemble the snapshots.

## Future prompt changes

Keep these numbered snapshots unchanged as historical versions. Add a new
numbered snapshot when actual system text, response schema or input evidence
shape changes; update this index and the AI-use history with the reason. For a
validation-only change, record the changed local gate and link to the existing
prompt snapshot instead of claiming a new prompt was used.
