# Test every invoice

From the repository directory:

```sh
python3 main.py
```

This runs automated tests, then audits every hospital and writes a fresh dated
output folder. Default mode uses cached AI responses only. To permit new source
or description-agent requests under the existing shared USD 2 budget:

```sh
python3 main.py --execute-ai
```

The existing ledger in `audit_output/all_hospitals_ai_2usd/ai` is reused. Earlier
charges and reservations remain part of the allowance. Do not create a new ledger
to bypass that limit. An offline cache miss remains unanswered; it never triggers
a paid call. Source contracts and prior result folders are not overwritten.

The runner also reuses individual task answers from the prior verified
`audit_output/four_blockers_test_all_verified` folder when present. Reuse requires
matching source/contract evidence, unchanged task input and reference groups,
and verification against the original API response cache. Every raw proposal
is validated again; no prior invoice verdict is imported. This prevents new
task batches from needlessly invalidating unchanged cached evidence. New or
changed tasks still need their own cached response or an allowed paid call.
Use `--agent-replay-dir /absolute/path/to/prior/results` for a different verified
run. Provenance mismatches fail loudly. After intentionally changing contracts,
use `--no-agent-replay` instead of bypassing that validation.

## Output files

- `submit_all_data.json`: internal review records from all hospitals, including H1.
  `status` explicitly says `answered` or `not answered`. For an unanswered or
  duplicate-ID record, `flagged` also says `not answered`. Reasons, record index
  and conditional assessment notes are retained. This is the user's complete
  review file, NOT a submission file.
- `submission.csv`: the only CSV output; exact official six-column template, H2–H5 opinions only.
- `all_results.json`: compact audit records, including evidence scores and their reasons.
- `unanswered.json`: eligible abstentions and separately explained duplicate-ID
  records, without silently dropping either.
- `summary.json`: per-hospital counts and shared budget accounting.
- `remaining_blockers.json`: overlapping blocker counts, single-blocker counts,
  and non-overlapping reason combinations that reconcile to eligible unanswered
  invoices. Duplicate-ID records are counted separately.
- `H*.unit_review.json`: source-clause agent decisions and evidence IDs.
- `H*.unit_clarification_requests.json`: unresolved services, possible units,
  exact source clauses and hashes, and the clarification needed.
- `H*.clause_graph.json`: hospital-isolated source nodes, service links and
  local sparse TF-IDF vectors. These are lexical vectors, not neural embeddings
  or a learned graph-embedding model. No embedding API cost is incurred.
- `H*.candidate_scopes.json`: exhaustive catalogue candidates under validated
  abbreviation assumptions, without claiming a single service identity.
- `H*.agent_routing_feedback.json`: why a description could not pass the
  independent-reference gate (too few invoices, competing support, or conflicting
  exact wording). `H*.agent_decisions.json` also retains validation failures.
- `H*.agent_replay_context.json`: source/cache provenance and prior task reuse.

CSV output uses Python's standard library; Node.js and a spreadsheet runtime
are not required. `--json-only` skips CSV export. `main.py` is the only normal
runner; the old `run_audit.py` and `test all.py` wrappers have been archived.
The final console output prints answered, unanswered and duplicate counts for
each hospital and reconciles every raw record.

## Tests, comparison and verification

```sh
python3 test.py
python3 main.py --compare
python3 main.py --compare --predictions /absolute/path/to/all_results.json
python3 main.py --verify /absolute/path/to/results --baseline /absolute/path/to/previous/results
```

Tests run locally with network requests blocked. Comparison reports H1's correct,
incorrect and unanswered counts; it excludes other hospitals. Verification checks
record reconciliation, sources, reference isolation and budget, and writes
`verification.json` inside the selected result directory. It does not run an audit
or make AI calls. Always select the intended baseline when comparing two runs.

## How the four blocker types are handled

1. Description validation repairs word-level abbreviations without importing
   unsupported extra words. For example, DISP can support “dispensing” without
   adding “pharmaceutical.” Existing meanings remain immutable. A unique match
   across the entire catalogue can recover a model abstention; multiple matches
   still require the standard reference gate. These are explicitly conditional
   abbreviation interpretations, not confirmed clinical service identities.
2. Unresolved descriptions can have exhaustive candidate sets after validated
   expansions. These restrict which historical checks they can affect; fuzzy
   top-three lists are never treated as complete. Newly scoped missing-detail
   tasks can use the existing 3-invoice/80%/20% outside-group price gate.
3. Same-day dependency sets derive from actual duplicate, cap, premium and bundle
   rules. H2 no longer inherits an automatic own-service duplicate dependency
   when its contract does not state that rule. Exclusion windows remain scoped
   to the related service, patient and date window. Confirmed service presence
   can establish a bundle/exclusion even when its quantity or unit is uncertain.
   Uncertainty blocks a check only when its outcome can change: an already
   exceeded threshold or an already present same-rate bundle is not invalidated
   by optional extra activity.
   Explicit zero/negative quantities cannot establish service presence. Missing
   quantities establish occurrence only for a positive billed service record,
   under an explicit assumption recorded on dependent checks; their
   quantity-dependent effects still remain uncertain. Duplicate-identity records
   are possible events, not confirmed independent activity.
4. Discount calculations keep lower/upper prior-usage bounds. Quantities in
   incompatible billing units do not count as confirmed contractual units;
   an unknown conversion leaves the upper bound unbounded. If both bounds select
   the same discount, the pricing check can proceed. This can also withdraw an
   unsupported earlier opinion rather than manufacture an answer. Confirmed
   usage counts even if patient metadata is missing; the current line and known
   out-of-term events do not count as prior usage. Tied or missing same-day
   ordering identifiers are bounded, never ordered by converting null to text.
   Duplicate-identity records can increase possible usage but cannot earn a
   discount as confirmed independent units. Consequently, a previous unsupported
   answer can return to review; coverage is not guaranteed to rise on every case.
   If every feasible final discounted price disagrees with the bill, an error
   can be reported while the exact corrected total remains blank. Any feasible
   matching price or another unresolved pricing dependency prevents that proof.
5. The unit agent retrieves actual service clauses and unit definitions through
   graph links and lexical similarity. Text documents are reread and hash-checked;
   existing extracted PDF text retains source provenance. An unconditional unit
   is accepted only with valid citations and unambiguous supporting rate clauses.
   Multiple-unit or conditional wording remains unresolved. The four currently
   ambiguous telemetry services literally say “per hour, per item”; the agent
   must not choose a unit just because it matches an invoice. Conclusively
   compound, conflicting or conditional rate clauses skip the AI call entirely
   and create a clarification request. This avoids paying to repeat a known
   source ambiguity.

## Optional source-backed unit clarification

The four current compound clauses cannot be repaired by configuration alone.
They require authoritative clarification of the billing dimension, quantity
definition, conditions and effective dates. A combined hour-item quantity must
not be silently converted into a single scalar unit.

For an extraction omission where the existing original source already states
one unconditional unit, a reviewer can supply:

```json
{
  "schema_version": 1,
  "clarifications": [
    {
      "hospital_id": "H2",
      "contract_number": "COPY_FROM_OWN_CONTRACT",
      "service_name": "EXACT_CATALOGUE_SERVICE_NAME",
      "unit_basis": "per_hour",
      "source_ids": ["contract.txt:123"],
      "source_document_sha256": {"contract.txt": "EXACT_SOURCE_SHA256"},
      "explanation": "The cited rate clause unambiguously states this unit.",
      "reviewed_by": "REVIEWER_NAME"
    }
  ]
}
```

This is a format example, not a valid selection for the current telemetry
clauses. Use IDs and hashes from the source graph. Pass the saved manifest using
`--unit-clarifications /absolute/path/to/clarifications.json`. All entries are
validated before any paid request. Foreign-hospital sources, changed hashes,
already established units, duplicate choices, and compound/conditional clauses
are rejected. H1's frozen pipeline cannot be overridden. New amendments must
first be ingested into the appropriate hospital's source bundle; this manifest
does not implement precedence or effective-dated unit amendments.

Each execution records `four_blockers_v2` and implementation hashes. Runtime
execution bugs now stop the audit instead of silently replacing a hospital's
full results with a basic-only fallback.

H1's previously verified pipeline is replayed unchanged. Quarantine and the
explicit H2 service-date-as-billing-day assumption remain enabled. H2–H5 have no
labels: more answers measure coverage, not proven correctness. The confidence
policy below is not a calibrated probability.

## Confidence policy

`confidence.py` computes `evidence_score_v1_uncalibrated` from rich audit traces
before the pipeline drops line details. It uses no labels or API requests and
does not change verdicts, corrected amounts, categories or abstentions. Both
exports reuse the same stored row score instead of inventing scores separately.

The numeric tiers are **declared judgments, not measured accuracy**:

- Catalogue constraints/reviewed wording: 0.90.
- Accepted fuzzy wording: 0.84; a narrow or missing match margin: 0.76.
- Validated AI wording interpretation: 0.78.
- Price-supported identity: 0.68 for at least three recorded distinct references,
  0.72 for ten or more, otherwise at most 0.50. This never becomes a confirmed
  semantic match. Reference isolation/validation remains the agent's job.
- Unresolved identity: 0.35; missing detailed line evidence: 0.40; an unknown
  matching method: 0.50. Missing evidence never earns a high score.

Recorded pricing/presence assumptions and actual invalid-date quarantine each
subtract 0.05 on affected lines. Uncertain history/pricing dependencies cap the
line at 0.50. Uncertain usage contributors subtract 0.02 when all bounds select
the same discount, otherwise 0.08. The H2 billing-day assumption subtracts 0.05
from invoice pricing confidence. These penalties do not estimate their real-world
error probabilities. Merely enabling an agent or quarantine policy is not a penalty.

Internal JSON records retain these fields, with readable `confidence_reasons`;
none are added as extra columns to the official CSV:

- `verdict_confidence`: support for erroneous/correct. An independent arithmetic,
  date or contract-number finding can support an error verdict at 0.95 even when
  unrelated services remain uncertain. A correct verdict uses the weakest line.
- `total_confidence`: confidence in the available corrected total. It is blank
  when that total is unresolved; missing line-calculation evidence caps it at 0.40.
- `category_confidence`: support for the **asserted** error categories, using the
  weakest supported category. It does not establish that no additional errors
  exist. Interpretation-dependent findings are capped at 0.65.
- `confidence`: the minimum of the available component scores, capped at 0.50
  when the corrected total is absent. This minimum is a conservative scoring
  rule, **not a calculated probability that the whole row is correct**.
- `confidence_method`: explicitly identifies the uncalibrated scoring policy.

All numeric confidence fields are blank for unanswered and duplicate-ID records.
The official `submission.csv` retains the original six-column schema and uses
only the row score. Detailed scores and explanations remain in internal JSON.
Exporting old compact rows without confidence leaves scores blank:
rerun `main.py` to score them using their complete evidence.

The exercise measures calibration. This change does **not** claim to satisfy
that with invented probabilities: 0.78 is an evidence score, not demonstrated
78% accuracy. H1 has already informed development, and five reference groups do
not erase that exposure. An independent labelled review set, with both correct
and incorrect predictions, is needed to validate or fit probability estimates.
H1 can provide development diagnostics only; transfer to H2–H5 remains unmeasured.

## Manual review after an unresolved AI answer

The exercise asks for uncertainty to be made visible and allows incomplete
coverage; it does not require an automatic human-review service. No reviewer is
assigned or notified by this code. Failed or rejected AI proposals remain
unresolved, with reasons in `unanswered.json` and agent feedback files. A known
error with an unresolved total can still be submitted; its remaining limitations
are retained in `all_results.json` and should be explained in the write-up.

Ambiguous units produce `H*.unit_clarification_requests.json`, including cited
source clauses. A reviewer may provide the source-backed clarification manifest
described above; contradictory or compound clauses cannot be overridden by
guessing. Other manual decisions belong in the required decision log and would
need an explicit, tested rule or evidence update before rerunning the audit.

Only `submission.csv` is exported as CSV. The older `submit_all.csv` and
`unanswered.csv` files may remain in historical result folders, but normal runs
no longer create them. The template's `flagged` values remain 0/1; unanswered
invoices stay in JSON instead of adding a non-template value or review column.
