# AI use and prompt history

This file preserves the original 16 prompt-iteration records in one place.
The historical bodies below are unchanged; old filenames and commands describe
the workflow at that time, not the current entrypoints.

## Current implementation

Codex assisted with extraction schemas, deterministic auditing, evaluation,
reviewed fallback policies, tests, and repository consolidation. The runtime
contract-extraction prompts are in `contract_builder.py`; description prompts
and validators are in `agents.py`; source-unit requests are in `contracts.py`.
`ai_client.py` holds model configuration, caching and spending controls.

Model proposals are inputs to validation, not final invoice verdicts. The
current invoice run uses cached responses by default. Historical manual review
and H1 label-informed development are disclosed below; H1 regression equality
is not an independent holdout evaluation.

## Original iteration records


---

### 001_contract_conversion.md

Original file SHA-256: `c4e5280607da79b349150eb645250f6af8e28c193243636fe91614df15791938`

# Contract conversion — AI assistance record

Date: 2026-09-05
Tool: Codex (GPT-6)

## User request (verbatim)

> yeah great make a json format for it first that have everything needed(write a code to do it takes the .txt file then convert it into json

## Context

The user first requested a breakdown of Hospital 1's contract fields and whether
they support the label categories. The assistant inspected the full contract and
label headers/category names before proposing the structured fields. This is a
record of the conversion step, not a complete transcript of earlier planning.

## Implementation decisions

Codex wrote a deterministic, standard-library Python parser and tests, generated
the JSON, and documented the workflow. Numeric tables are extracted from the TXT.
General prose rules use an explicitly reviewed, digest-guarded Hospital 1 profile.
Original clauses and row references are retained. Unresolved exclusion boundaries
and correction policies are marked rather than silently decided. No labels or
invoice values are inputs to the converter; the Markdown rate schedule is used
only as an independent test reference. No embedding model or external AI API is
called by the generated code.


---

### 002_invoice_audit.md

Original file SHA-256: `815ae804a67d392185eeb778fba4168ecfb2022ab8f06e7ec108fa72a35e0b8a`

# Invoice audit implementation — AI assistance record

Date: 2026-09-05
Tool: Codex (GPT-6)

## User request (verbatim, excluding pasted label example)

> thats a sample for the label file
> do a code read each invoice and compare to the json file  and check
> the code should be univeral for all contract because at the end all contarc will be in the same form as this
> then the code do a loop for all the contract it find then see the name of it then go to invoice and create a sample

## Work and limitations

Codex implemented the contract-discovery runner, matching baseline, schema-driven
checks, pricing traces, sample generation, label evaluation and tests. Contract
values come from the structured JSON. Matching uses offline abbreviation and
token similarity rules, without embedding/API calls or matching by billed price.

The conversation already included Hospital 1 labels and one worked invoice; this
is development-set work, not a blind evaluation. The program itself reads labels
only after creating predictions. No labels are used as invoice-auditor inputs.

Missing matches and unresolved history/correction policies result in abstention
or review rather than confident invented totals. Confidence is not calibrated and
is left blank. Universal discovery is implemented, but additional semantic forms
(dated amendments, non-calendar service days, service-specific multiplier tables)
require extending the schema and engine. The engine rejects unsupported shapes.


---

### 003_voyage_gemini_pilot.md

Original file SHA-256: `bbeb4c7205ac3ce1e205587f0f0dc1cc3b892df88f0cb16348bdcb32b499b622`

# First 100 invoice pilot

Date: 2026-09-05
Models: voyage-law-2, rerank-2.5, google/gemini-3.5-flash-lite via OpenRouter.

## User authorization/context

The user requested the first 100 Hospital 1 invoices to be tested with the AI
fallback, with accuracy comparison and actual cost reporting. They then confirmed
Voyage and OpenRouter credentials were in the local .env. Credentials are omitted
from this record and ignored by Git. The initial live smoke test used one
description, then the remaining descriptions were processed with cached results.

## Versioned prompts

The exact reasoning system prompt and response schema are constants SYSTEM and
SCHEMA in scripts/pilot_ai.py. The reranking query is constructed in resolve().
Each request contains a billing description and source-grounded service candidates
from Hospital 1's contract, but no labels, billed amounts, patient IDs or invoice
verdicts. The embedding documents include original contract rate rows and related
clauses from the JSON's preserved source text. The top 20 semantic candidates are
reranked to five for Gemini to choose from or abstain.

The mapping cache includes description, proposed canonical service, explanation,
candidate ranks and validation limits. Catalogue membership is validated in code;
this is not independent proof of semantic correctness or calibrated confidence.

## Evaluation boundaries

The full Hospital 1 history is used locally for rule calculations. Only unresolved
descriptions in the first 100 invoice records receive new AI calls. Reused mappings
can resolve matching descriptions in that history. No other hospital is processed.
The existing labels and prior development results have been seen in this Codex
conversation, so this is a development pilot, not an untouched holdout evaluation.
No label data is included in API prompts. Predictions are generated before the
comparison stage reads labels. The labelled first 100 records are a chronological
prefix, not a random sample; results should not be extrapolated to all hospitals.

Four stages are saved separately to avoid attributing code changes to AI:
original baseline, bounded uncertainty only, AI mappings only, and both together.
The bounded mode uses lower/upper cumulative usage bounds and date-local patient
uncertainty; it never assumes an unknown service is irrelevant merely because it
was absent from a retrieval shortlist. Unresolved boundary/correction policies
continue to require review. The original audit runner's default behavior is kept.

Costs: OpenRouter usage.cost is provider-reported; Voyage dollar amounts are
estimates from measured tokens at published list prices, before any free credits.
This is a finite mapping workflow, not an unbounded autonomous invoice agent.


---

### 004_manual_review_10.md

Original file SHA-256: `a6010172efd9f5e57129a929685a5994aead7b38f33bd4000b2ac67694a0511e`

# Codex review of ten unanswered invoices

User request: Review ten invoices directly against the contract, save a result,
then check the actual labels and report correctness.

Selection: first ten unanswered records in pilot_100/after.jsonl, fixed before
individual label lookup: 1, 3, 6, 12, 17, 20, 21, 22, 24, 26.

Codex inspected all selected invoice lines, existing candidate mappings and
calculation traces, and relevant full-corpus historical charges. It resolved
specific ENT/otolaryngologic and other catalogue abbreviations and reviewed
whether uncertain historical service identities could actually affect each
selected invoice. Six invoices were judged correct. Four remained ambiguous;
billed unit/price agreement was not accepted as independent service identity
evidence. The exact decisions and reasoning are versioned in manual_review_10.py.

The decisions and recalculated totals were saved to predictions.json before a
separate --compare invocation loaded labels. The comparison records a SHA256 of
the frozen predictions. Earlier aggregate label results and some individual
examples were already discussed in this conversation. This is NOT a blind or
independent evaluation, and the reviewed sample contains no labelled errors.
The replay script encodes a fixed manual adjudication; it is not a general solver.
No new Voyage or OpenRouter calls were made for this review.


---

### 005_contract_dependencies.md

Original file SHA-256: `69ba5dca34dfa4d5815eedf404fe586e486539a50aa2523557aa1c707492f19f`

# Contract JSON 1.1 and offline regression comparison

User request (verbatim):

> so do it in the code that create the json and after that run it ang get the new json after that run the code that do the test and compare and show me

Codex updated the TXT converter to attach rule dependencies derived from the
extracted rules and a separately sourced reviewed terminology profile. The profile
contains abbreviations and description candidate sets, not hidden invoice labels,
rates inferred from billed prices, or overrides for invoice verdicts. The profile
records its non-contract provenance and is hashed into the generated JSON.

The auditor validates dependency consistency and uses actual related services and
date windows to limit uncertainty. Reviewed ambiguity and full-catalogue token
candidates take precedence over AI proposals under the stated description
assumption. Top-k retrieval candidates alone never justify narrowing unknown
utilisation bounds.

The same 100-invoice pilot was replayed locally with cached mappings; no API calls
were made. Predictions were written before the comparison stage loaded labels.
The earlier pilot and manual review informed these changes, including ambiguity
cases that produced false positives. This is development-set iteration, not a
blind test or evidence of accuracy on unseen hospitals. The original pilot outputs
were retained so before/after denominators and selection can be verified.


---

### 006_review_20_improvement.md

Original file SHA-256: `6defe9ee2ee04e3312f3aa47eeaba3b8e7faeed1a5d37befaa03669186b8d73a`

# Review of 20 remaining H1 abstentions

User requested: manually review 20 unanswered invoices, identify missing JSON or
code functionality, implement supported fixes, and rerun every invoice.

Scope: all 918 H1 records. H2–5 do not yet have supported structured contracts.
Codex reviewed the first 20 abstentions from the preserved `contract_update_all`
run, their source rate schedule and clauses, existing calculation traces, and
relevant historical invoice lines. No fresh embedding or reasoning API calls.

Before the new label comparison, `manual_review_20.py` froze four definite
no-error decisions (IDs 38, 60, 62, 70) and sixteen abstentions, including reasons
and expected totals for the four. Arithmetic traces were reused after review;
this is assisted manual adjudication, not an independent implementation. Some
individual labels and aggregate results had already been discussed previously:
neither this sample nor the full H1 evaluation is blind or held out.

Changes are reusable rather than invoice-ID exceptions:

- Reviewed abbreviation expansions in the matching profile, carried into JSON by
  the converter, with explicit provenance and validation.
- History-only family constraints for conflicting anaesthesia-administration
  descriptions. The service remains unidentified and possibly outside the
  catalogue, but cannot affect unrelated service families under the explicit
  assumption that the family words describe the service truthfully.
- Discount uncertainty explanations with prior-unit bounds and the historical
  line IDs contributing to the upper bound.
- Regression tests for preserved ambiguity, family scoping and related history,
  multiple-family candidate unions, and independence from billed rates/units.
- Comparison CLI supports separate output and baseline paths, preserving earlier
  experiments and freezing full predictions before loading labels.

No rate values, threshold semantics, exclusion policy or labels were changed.
Sixteen reviewed descriptions genuinely omit distinguishing attributes. An
authoritative provider service code/crosswalk or full description is needed;
choosing whichever service makes the billed amount match would conceal errors.

Post-freeze evaluation: manual sample 4 correct, 0 incorrect, 16 unanswered;
automated full H1 eligible records 545 correct, 4 incorrect, 359 unanswered
(previously 508 / 4 / 396). All 510 provided totals exactly match the labels.
Accuracy on answered records is 99.27%, coverage 60.46%. Ten duplicate-ID raw
records are excluded from the 908-record denominator. Extra API cost: zero.


---

### 007_reason_routed_fallback.md

Original file SHA-256: `29caf842e598dec5823747d93c2485c9c8b2f4a2ab295977d1cb8679f51d42a6`

# Reason-routed fallback request and AI assistance

User requested a working pipeline that returns a reason when deterministic JSON
auditing cannot answer, chooses a relevant AI agent from that reason, and tries
to answer ONLY the 190 H1 invoices blocked by unclear service descriptions alone.
Other unanswered categories were explicitly out of scope. The discussion allowed
testing price-supported service inference while retaining its uncertainty.

Codex implemented `scripts/agent_fallback.py`, two specialised prompt routes,
scoped engine resolutions, response validation, an offline replay option,
separate strict/price-assisted metrics, and tests. Full actual provider system
prompts are versioned in the script's `COMMON` and `PROMPTS`; the structured
response contract is its `SCHEMA`. Input evidence and agent explanations are
recorded in `audit_output/agent_190/tasks.json` and `decisions.json`. API secrets
were loaded locally and never included in prompts, saved headers, or outputs.

The original 190 records are fixed by a hashed prior-run baseline. Eleven
normalised-description tasks replace 212 per-line requests: two abbreviation
tasks, nine missing-detail tasks. All use the previously selected Gemini 3.5
Flash Lite through OpenRouter. No Voyage calls were made in this experiment.

Price evidence excludes ALL target invoice IDs, not just the current line.
Reference records are not assumed correct, and no labels select references.
At least 3 reference invoice IDs, 80% proposed-candidate rate compatibility and
at most 20% competing compatibility were required before label evaluation.
Rates are fingerprints only: the auditor recomputes actual entitlement afterward.
Equivalent text variants may mix service identities; the agent abstained on the
mixed psychiatric rehabilitation pattern and the insufficient orthopaedic sample.
It also abstained on a GI abbreviation pattern. No labels were used to override
those decisions or force a better score.

The first run produced 130 correct decisions, no incorrect decisions, and 60
abstentions. A valid abbreviation response was rejected for title case and for
repeating known aliases. After seeing the aggregate evaluation, Codex fixed that
response-format bug, added regression tests, and replayed the same responses
offline. Original outputs are retained in `initial_validation/`. This is an
explicitly label-aware development iteration, not a fresh blind test.

Final output: 132 correct, 0 incorrect, 58 unanswered; only 2 decisions are from
description matching alone, the other 130 rely on unconfirmed price-pattern
inferences. All 131 calculated totals match labels. The target contains one
labelled erroneous invoice (wrong unit basis), which was detected; its corrected
total remains blank. Other 169 unanswered cases and all non-target result records
are unchanged. Total provider-reported cost $0.0153943 for 11 calls; replay cost
zero. H1 evaluation does not demonstrate generalisation or calibration.


---

### 008_versioned_problem_type_fixes.md

Original file SHA-256: `c4f3095251923e7d58663e59fc68f8cb257e4b8e8cd7b10dd3821346a9e76318`

# Versioned fixes for the four remaining problem types

User requested the previously recommended fixes, preservation of old code and
results, general problem-type rules rather than invoice-specific exceptions,
and another test. Codex implemented new files only for this iteration.

New files: `scripts/agent_fallback_v2.py`, `config/fallback_v2_policy.json`,
`tests/test_agent_fallback_v2.py`, an evaluation-only feedback script and docs.
No new external model or embedding calls were made. Prior cached AI proposals
remain inputs only to reproduce the old experiment and the abbreviation stage;
later stages apply explicit, general evidence checks.

The policy expands reviewed abbreviations, preserves original description
variants for price evidence, uses family-scoped history as a fallback, and tests
a separate two-reference/unanimous/zero-competing-support gate. The standard gate
retains three references and 80%/20% support. All price-based and history-based
assumptions remain visible, with no invented numeric confidence. Reference
evidence excludes the original target invoices, duplicated IDs, and a historical
line's own invoice when inferring history. None of the rules read labels or use
invoice IDs as service/price exceptions.

Initial v2 output preserved in `agent_190_v2` exposed a regression: adding history
families before the original fuzzy matcher caused resolved descriptions to become
ambiguous. The fix changes fallback ordering generally in an isolated matcher;
the old engine file/module remains unchanged. New tests cover this ordering and
module isolation. The verified run is `agent_190_v2_verified`.

The 58 previously unanswered cases were already development-reviewed. Predictions
for all stages were frozen before the new run parsed labels; this is not a blind
test. Stage improvements: 13 abbreviation/history-abbreviation answers, 25 from
preserving original wording variants, five from additional history handling, and
15 from the separate lower-assurance experiment. All 58 new verdicts and totals
matched labels, and every original 132 verdict/total remained unchanged. Other
169 unanswered cases were not exported as solved. Protected-file digests verify
old code/results and raw data stayed unchanged. The target has only one labelled
error, already detected by v1; the 58 new cases are all no-error invoices.


---

### 009_crossfit_standard_references.md

Original file SHA-256: `5a03af975ef1b06b96916c8cf1b1c71aefde41a88cb37fb699d8ab70e77409fa`

# AI assistance disclosure: full-scope standard and cross-fitted references

The user requested a full Hospital 1 rerun without the weaker experimental
two-reference policy, then authorized trying five reference groups to resolve
remaining orthopaedic ambiguity and comparing the results with the old run.

Codex implemented `scripts/audit_all_standard.py` for the full-scope baseline and
`scripts/audit_crossfit_standard.py` for this separate experiment, plus tests and
documentation. The existing auditor, original structured contract, and old
results were preserved. No new model/API calls were made by the audit runner.

Implementation requirements: fixed groups based on patient/invoice IDs; exclude
the entire target group from price evidence for target and historical mappings;
exclude each historical invoice's own price; retain the standard minimum of
three references and existing compatibility gates; never use labels to select a
mapping or split; save predictions before evaluating labels; report regressions,
remaining uncertainties, and costs honestly. No invoice-specific resolution rules
or alternative split seeds were selected using evaluation scores.

Result: 900 correct, four incorrect, four unanswered among 908 eligible H1
records, compared with 884/four/20. Sixteen new verdicts and their totals matched
labels. This is development-set evidence, not an independent held-out evaluation;
the earlier matching policy and cached mappings were reused.


---

### 010_duplicate_unknown_date_policies.md

Original file SHA-256: `ba9ad4bc6d07d07461df68713fd313d7a13af55a267eb9cae1fb616fa0dbed59`

# AI assistance disclosure: three separately tested auditing policies

User request: implement and test the proposed fixes for four incorrect duplicate
verdicts and four unanswered Hospital 1 invoices. Preserve prior work and test
invalid-date quarantine separately from duplicate/unsupported-service changes.

Codex inspected contract clauses 4 and 11.4, prior predictions, raw duplicate
pairs, malformed-date history and H1 labels; wrote a new versioned engine, general
policy helpers, a frozen-crossfit replay/evaluation runner, tests and documentation.
No new Voyage/OpenRouter calls were made. The already-developed matching policy
and cached mappings were retained; this remains label-aware development.

General rules implemented: unique earliest invoice-date occurrence is retained
and later duplicates flagged; sufficiently recognised literal description tokens
with no compatible service in a full-catalogue scan produce an evidenced
unknown-service flag; optional quarantine excludes invalid dates only from dated
patient-history checks while retaining uncertain utilisation bounds. No service
or corrected date is fabricated, no ID exceptions added, and no corrected total
is invented for unsupported services or later duplicate charges.

Observed eligible verdict counts: baseline 900 correct/four wrong/four unanswered;
duplicate allocation 904/zero/four; plus unsupported-service classification
905/zero/three; with conditional date quarantine 908/zero/zero. All 876 totals
provided in the conditional stage match labels; 32 remain unresolved. Categories
still have omissions and extra flags. A perfect binary score here is not a claim
of complete audit/submission correctness or generalisation to Hospitals 2–5.


---

### 011_general_contract_agent.md

Original file SHA-256: `a0bf231500161da23ab8de196b20e7556a1b718a02e68185a0091e00cf5ec103`

# AI assistance disclosure: general contract extraction and all-hospital testing

The user requested one AI extraction agent that reads every hospital's own
contract documents and produces a comprehensive JSON in the familiar H1 format,
including multi-document hospitals. They then requested an all-invoice test with
quarantine enabled, template-format predictions and unanswered counts.

Codex implemented the generic extractor and its source-table reconciliation,
normalised provider representation errors using original cited text, and added
separate extended auditing support for service-date amendments and facility/tier
multipliers. Original H1 code, contract, sources and results were preserved.
The source-level SYSTEM prompt and JSON response schema are versioned in
`scripts/contract_agent.py`; `make_request` supplies hospital-only document context
and numbered target lines. There are no labels or invoice prices in these calls.

43 calls to google/gemini-3.5-flash-lite via OpenRouter cost $0.3998886. No Voyage
or paid invoice mapping calls were used in this iteration. Response cache,
raw AI facts, source-linked normalised contracts and stage outputs preserve the
history. Prompt-following failures included wrong field keys, malformed encoded
strings, omitted chunk-edge table rows and malformed multiplier rationals. A
deterministic source-table pass checks/recovers those rows instead of silently
trusting the model. Unknown or contradictory semantics remain review drafts.

All 4,886 raw invoices are represented in detailed outputs; 62 duplicate-ID records
are excluded from unique-invoice submissions. The scored H2–H5 submission has
850 answered opinions and 3,066 unique invoices remain unanswered. H1's existing
hash-verified 908 verdicts are included only in the development report, not the
scored submission. H2 pricing is not implemented for the 07:00 Service Day; its
37 answers are independent errors only. H3–H5 use deterministic mapping without
the H1-only cached price-reference fallback. No unlabelled accuracy is claimed.

Quarantine excludes invalid historical dates from same-day and exclusion-window
checks, retaining original bad-date flags and uncertain volume units. Additional
assumptions include invoice-header facility fallback for H5, service-date amendment
application, same-patient exclusions and conservative uncalibrated confidence.
Partial totals and missing categories are not disguised as complete audits.


---

### 012_h2_service_day_policy.md

Original file SHA-256: `1d368c326319efa4cc5daef7b09bf5d8280054de20fd600c07bb096dd2b9eb12`

# H2 date-label policy change

User requested applying the recommended Hospital 2 date-only interpretation
before extending the invoice AI fallback to other hospitals.

Codex inspected the contract text, adapted the runtime compiler and extended
engine, added tests, and reran all hospitals locally. No invoice or contract
API calls were made. No label data was used to choose the H2 policy or rules.

The user-approved assumption treats the supplied service_date as a billing-day
label. It is opt-in, preserves the source definition, and does not reconstruct
times. Source-specific invoice-rule differences and unassessed administrative
requirements are disclosed in the README and H2 output metadata.

Verification: 89 tests passed, old non-H2 outcomes unchanged, all 37 previously
flagged eligible H2 invoices still flagged, 200 additional H2 opinions. There
are no H2 labels; coverage improvement is not a measured accuracy improvement.
Old outputs and source contracts were not overwritten.


---

### 013_all_hospital_invoice_agents.md

Original file SHA-256: `fcd1b7ee58af97b9d5810a54ddec820eb2c4dece3094434caf455c3bd6bfbb42`

# Generalized invoice description fallback

User asked to connect the existing AI fallback to all hospitals, test every
invoice and enforce a USD 2 safeguard for the run.

Codex implemented and tested shared budget reservations, hospital-isolated
description routing, batched abbreviation proposals, standard-evidence
price-supported proposals and cross-fitted full-history recalculation. H1's
existing AI evidence is replayed; no H1 labels enter the new routing or prompts.
Only the final H1 regression/evaluation uses labels. H2–H5 have no labels.

Prompts are in hospital_invoice_agent.py and reuse the existing bounded routing
prompts and validators from agent_fallback.py. Responses are cached and retain
decisions, explanations and reference provenance. No model-generated monetary
verdict is accepted. No embedding/reranking API is called by this runner.

During the first live pass, Codex tightened batching so different excluded
reference-group pools never share an AI request. The partial run was stopped,
its budget ledger and cache retained, and a fresh run used the same allowance.
Any interrupted request's charge remains conservatively reserved. The partial
run's mixed-pool price proposals are not used in final audit results.

Results remain conditional on documented description/price inferences,
date quarantine and H2's service-date-as-billing-day assumption. This is not
independent validation of H1 or a correctness measurement for H2–H5.


---

### 014_four_blocker_fixes.md

Original file SHA-256: `f6d87accb4a72a28553904240af3cf99fe5a224775ac0fc6dac687530475df66`

# Four-blocker improvements and complete export

User requested source retrieval for the unit agent, improvements to unclear
descriptions, patient-history checks and volume discounts, and a script named
`test all.py` that includes explicit unanswered rows and prints counts.

Codex implemented a hospital-isolated service/clause graph with local TF-IDF
vectors, citation-checked unit review using Gemini, harmless expansion cleanup,
exhaustive candidate scopes for unresolved descriptions, precise same-day
dependencies and unit-aware utilisation bounds. No embedding API is used.
Neural graph embeddings were not implemented; the lexical graph/vector choice
was disclosed before implementation. Invoices and labels are absent from unit
agent prompts. Description agents never return monetary invoice decisions.

The prior shared USD 2 ledger is reused, so this run's new requests do not reset
the existing spending allowance. The official submission schema is preserved
separately from the complete review CSV that explicitly marks unanswered rows.
Source data and all previous result folders remain unchanged. Genuine compound
contract units are not silently resolved to match bills.


---

### 015_four_blocker_refinement.md

Original file SHA-256: `85b6d1bb60bd45b3da89b43500549a91d3141abf0d7c56d4a5e42bcfbe9c738a`

# Further blocker refinement, implementation only

The user requested fixes to remaining volume-discount, service-description,
patient-history and contract-unit uncertainty, and said they would run the full
invoice test themselves. Codex implemented and tested general problem-type rules
using local synthetic regression tests. No paid model calls or full invoice audit
were authorized or performed in this implementation turn.

Description proposals are repaired at the word level and validated against the
whole hospital catalogue. Unsupported specificity is discarded, not introduced.
Conditional lexical uniqueness can recover model abstentions. Outside-group
price evidence retains the minimum three independent invoices, 80% support and
20% competitor gates; neither target prices nor labels supply service identity.

Historical checks distinguish confirmed presence from uncertain units/quantity
and block only when rule outcomes can change. Prior usage excludes the current
line and known out-of-term activity. Tied ordering and uncertain conversion stay
bounded rather than receiving fabricated values. Discount-only uncertainty can
prove a wrong price if every feasible rate disagrees, without claiming the exact
corrected total. Other pricing uncertainties disable that proof.

Independent synthetic review additionally caught and corrected missing-ID
ordering, zero/negative-quantity presence and duplicated-record usage issues.
Same-day ordering with missing identifiers stays bounded. Duplicate identities
contribute possible upper usage only. Missing quantity establishes occurrence
only for a positive billed event and this assumption is retained in evidence.
These safety corrections may withdraw previously unsupported answers.

Raw cached agent responses can be revalidated independently of batch order only
when the original API cache, source/contract evidence, exact task request,
reference observations and excluded groups agree. Stored invoice verdicts and
stored accepted resolutions are never imported for H2–H5.

Source review produces structured clarification requests and verifies source
identity, hashes and cited unconditional rate clauses. The original four
telemetry rates still say “per hour, per item”; no unit is chosen from the bill.
Conclusive source ambiguities bypass further AI calls. A source-bound reviewer
selection can repair an extraction omission but cannot override contradictory
source text or establish amendment precedence.

H1's frozen pipeline, quarantine assumptions, H2's explicitly conditional billing
day policy, prior result folders and shared USD 2 ledger remain preserved.
The full runner records the revised policy and remaining overlapping/disjoint
blocker counts. Regression tests do not establish new real-invoice coverage or
H2–H5 correctness; the latter hospitals have no provided labels.


---

### 016_repository_cleanup.md

Original file SHA-256: `d9d13c8e4931850754491a3834b15206fc8f5ebe515acfc57d80fe0b04bf26e6`

# Repository cleanup

The user requested clearer code and removal of unnecessary files. Codex mapped
transitive imports, dynamic file dependencies, source hashes, cached AI responses,
and historical baseline references before removing anything from the active tree.

Unused experimental scripts, superseded draft/result generations, historical
guides and optional export tooling were moved into a recoverable archive outside
the repository with a checksum manifest. Original contracts, invoice formats,
labels, prompts, current drafts, caches, spending records and required evidence
were preserved. This cleanup deliberately did not merge legacy audit engines or
change matching/pricing policies.

The new run_audit.py separates orchestration into named functions; test all.py
remains compatible. CSV generation now uses the standard library. H1 comparison
defaults to the newest complete audit export, ignores other hospitals and
recognizes explicit unanswered values. Documentation distinguishes active code
from frozen historical dependencies and preserves the original exercise brief.

Validation uses synthetic regression tests, protected-content checksum checks,
and a full cache-only re-audit compared with the user's latest result. No new
paid model requests are part of cleanup verification. Coverage equality is a
regression check, not independent accuracy evidence for unlabelled hospitals.


---

## 017 — Responsibility-based code consolidation

At the user's request, Codex consolidated the active implementation into ten
root Python files with one main runner and one regression-suite file. A shared
audit engine now has explicit behavior profiles instead of separate versioned
copies. Contract creation, source-clause handling, fallback agents, reporting
and API spending each have named modules. Three parallel assistants helped
consolidate rules, contract handling and regression tests.

All original prompt records are preserved above. The old scripts, tests and
prompt directory were moved to a recoverable backup outside the repository.
No audit-policy change or paid model call was intended in this refactor.
Validation includes the consolidated local suite, extraction-request parity,
synthetic old/new engine comparisons and a full cached invoice replay against
the prior result. See docs/CLEANUP.md for verification and recovery details.

## 018 — Explainable confidence annotation

The user requested a small replacement for the two fixed confidence values and
reiterated the exercise's warning about overconfidence and calibration. Codex
implemented a label-free, deterministic evidence-score module, with separate
verdict, asserted-category and corrected-total scores and readable reasons.
The pipeline scores rich traces before compacting them, and both exports reuse
that score. It does not alter audit decisions or ask models to rate themselves.
Tests cover uncertainty, reference evidence, quarantine, immutable scoring and
consistent exports. Verification uses a fresh offline full audit with unchanged
verdicts/categories/amounts and no new API spending.

The tiers are explicitly provisional and uncalibrated, not an empirical claim
about correctness. H1's development exposure prevents treating its current
perfect verdict result as independent calibration evidence. The score policy
and limitations are documented in docs/RUNNING.md.

## 019 — Strict template-only CSV output

The user requested that outputs follow only the supplied template and asked
about manual review when AI cannot solve a case. Codex removed extra CSV exports
from normal runs: only submission.csv is written, with the supplied header and
no additional columns. Evidence, confidence explanations and unanswered records
remain in internal JSON, and H1 comparison reads that JSON directly. No audit
verdicts, confidence policy, or API spending policy were changed. The exercise's
human-review language and the existing source-backed unit clarification files
are documented without claiming that the code assigns or notifies a reviewer.

## 020 — Submission evaluation, source review and decision log

The user requested manual handling of unanswered cases and the exercise's three
written deliverables. Codex reviewed 12 representative H2-H5 blockers against
original source clauses: none justified a new complete verdict, and all 12
remained unanswered. This was AI-assisted source review, not independent human
review or examination of every abstention. The journal records exact clauses,
input hashes, conditional possibilities and why they were not submitted.

H1 evaluation was recomputed independently and added to normal output generation.
It separates correct binary verdicts from missing totals and category-set errors.
A strict complete-row confidence proxy is explicitly a development diagnostic,
not held-out calibration or a claim about the evaluator's unpublished scoring.
No labels or review conclusions were fed back into the audit in this step.

Five numbered snapshots recover actual historical/current system prompts and
schemas with hashes; their creation dates are not represented as past Git commits.
The evaluation report and one-page decision log are available as Markdown and
render-checked PDFs. No new Voyage/OpenRouter requests were made in this step.
An account-limit warning was checked before resuming; the current account status
reported available capacity. No alternate credentials or reset were used.

## 021 - Applicant methodology and clearer report

The applicant supplied a first-person account of the staged approach and asked
for a clearer, less category-heavy report. Codex edited the report and decision
log to reflect that account, distinguishing source-grounded contract rules from
matching assumptions and AI-assisted review from independent expert review.
Saved evidence replaces approximate recollections: the earliest full-H1 run
answered 40/908 (4.4%), and later rule/schema results also reused pilot AI matches.
The first H1 converter was AI-assisted deterministic code, not an API extractor;
the general API-based contract extractor was added later. Reason routing uses
AI proposals for descriptions/units and deterministic history/volume checks,
not a separate model for every blocker type. The 6-8-hour cap is the applicant's
stated stopping rationale, not an independently timed elapsed-hours claim.

The report summarizes the 12 exactly matched categories together and explains
the five with misses or extra tags in plain language. Original per-category
metrics remain unchanged in the run's evaluation JSON. The four failure types
retain examples, limitations and next steps. No audit rules, predictions,
confidence scores or prompt snapshots changed, and no new Voyage/OpenRouter
requests were made for this documentation revision.

## 022 - Separate evaluation from the decision log

At the applicant's request, Codex moved a condensed development story into the
decision log. Most of that page remains devoted to assumptions, alternative
readings and decisions, including unresolved compound units and exclusion
endpoints. The evaluation now focuses on measured results, category performance
and four systematic failure types, retaining development/calibration caveats.
Only documentation and PDF layout changed; no predictions, audit rules, prompts
or API spending changed. Both PDFs were regenerated and visually checked.

## 023 - Explain the prompting workflow

The applicant asked the prompts folder to show how AI was used, not merely list
five reusable prompts or reproduce every conversation message. Codex expanded
`prompts/README.md` with the development stages, representative requests explicitly
labelled as paraphrases, and a guide to actual prompt evolution. It distinguishes
interactive Codex assistance from runtime API requests, source facts from matching
assumptions, and prompt changes from local validation/code changes. The five exact
snapshots remain unchanged. No new runtime prompt version, API call, prediction
change or Git commit was made by this documentation update.

## 024 - Expand the applicant's decision-log story

At the applicant's request, Codex expanded the decision-log introduction to cover
the JSON-versus-retrieval choice, initial H1 baseline, sample-based source review,
general problem-type fixes, targeted fallback and cross-hospital expansion.
The existing assumptions and unresolved decisions were preserved. The PDF was
checked to remain one page; evaluation results, predictions, runtime prompts
and API spending were unchanged.

## 025 - Prepare the repository for pushing

Codex expanded `.gitignore`, added an empty `.env.example` and publishing
instructions, and preserved/extended the original Git attributes. Redundant runs remain local; required
frozen evidence, original extraction responses, invoice caches, spending ledger,
final results and submission deliverables remain eligible for Git. No original
data or local results were deleted and no remote was changed.

A portability issue in H1 provenance validation was fixed: capture-time absolute
paths now resolve only to the four expected inputs in the current checkout,
retaining exact content-hash checks. Four regression tests cover relocation,
changed bytes, missing/extra/duplicate provenance and escaping symlinks. All 228
tests passed. An isolated Git-visible copy reproduced every one of the 4,886
record decisions and the byte-identical 2,743-row submission, with unchanged
confidence/review reasons and spending. Detailed source/reference verification
also passed. No API requests, staging, commit or push was performed.
