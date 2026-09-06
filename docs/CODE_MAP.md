# Code map

## Start with these files

Read `main.py` first: it has argument parsing, the pipeline command, source hashes,
and the final run sequence. `pipeline.py` then shows how each hospital is loaded,
audited and exported without mixing its contract with another hospital's.

The other files have focused responsibilities:

- `contract_builder.py`: discover document bundles, extract source facts, reconcile
  literal rate tables, and assemble JSON drafts. It also contains the original
  deterministic H1 text converter as `convert_h1`.
- `contracts.py`: compile drafts into supported rules; determine rule dependencies;
  retrieve original clauses using source links and local lexical similarity;
  validate source-cited billing-unit clarifications.
- `matching.py`: catalogue tokens, aliases, matching, dates, integer arithmetic,
  and validated history-mapping support.
- `rules.py`: one audit implementation, with explicit current, frozen-H1 and
  baseline behavior profiles. Shared calculations are not copied into separate
  versioned engines. Normal entrypoints are `audit` and `audit_h1`.
- `agents.py`: description prompts and gates; validated abbreviation expansion;
  outside-group reference-price support; hospital-isolated batching; safe cached
  task reuse; H1 evidence replay. Numbered sections organize these responsibilities.
- `ai_client.py`: the invoice client's durable shared budget and response cache,
  plus the separate contract-extraction client. Credentials load only when needed.
- `reporting.py`: coverage, H1 label comparison, complete-review/official exports,
  blocker summaries, and source/reference verification of completed runs.
- `confidence.py`: a pure, label-free scoring function that explains the strength
  of recorded evidence. The pipeline calls it before compacting rich line traces;
  both exports reuse the same stored row score. It cannot change a verdict.
- `test.py`: one standard-library test suite, grouped by behavior, with shared
  synthetic fixtures and network requests blocked.

## The normal invoice workflow

1. `main.py` runs the local tests and starts `pipeline.py`.
2. The pipeline loads each hospital's JSON and invoices. `contracts.py` prepares
   executable rules and checks source-backed unit interpretations.
3. `rules.py` audits with catalogue matching from `matching.py`.
4. `agents.py` routes supported description problems to a proposed mapping. It
   independently validates wording, source evidence and reference support. Cached
   responses are used by default; paid calls require an explicit command flag.
5. The deterministic engine reruns with validated mappings and bounded history
   uncertainty. The agent never directly sets the invoice verdict or amount.
6. `reporting.py` retains every raw record in internal JSON, including unanswered
   and duplicate-ID records, and exports only the template-format `submission.csv`.
   H1 labels are for evaluation, not matching input.

Hospital 1's frozen contract, mappings and reference groups remain protected.
They now run through the shared engine's explicit H1 profile. The five reference
groups prevent an assessed invoice's own price from acting as its identity label.

## Contract creation is separate

Run `contract_builder.py` only when inspecting or rebuilding contract drafts.
Multiple source documents stay in the same hospital bundle, with provenance and
amendments retained. Extracted drafts require review before audit use.
See [CONTRACT_EXTRACTION.md](CONTRACT_EXTRACTION.md).

## Evidence is not disposable code

The old Python modules and test files were consolidated and moved out of the
repository. Some versioned **data paths** remain because frozen hashes, reference
traces and cached answers depend on them. Do not delete those paths based on their
names. Prompt iterations are preserved in the single root `AI_USAGE.md`.
See [CLEANUP.md](CLEANUP.md) for the recoverable pre-refactor backup.
