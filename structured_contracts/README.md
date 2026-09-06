# Hospital 1 contract conversion

From the repository root, using Python 3.9 or later (standard library only):

```sh
python3 scripts/convert_hospital_1_contract.py contracts/hospital_1/provider_services_agreement.txt structured_contracts/hospital_1.json
python3 -m unittest discover -s tests -v
```

The converter reads the text contract and `config/hospital_1_matching.json`, a
separately versioned interpretation-aid profile. It does not read invoices or labels,
call an AI API, or change the raw files. The output is reproducible with the same
input path and contents. There are no external dependencies to install or pin.

## Output fields

- `contract_details`: parties, identifier, dates, currency, facility and tiers.
- `definitions`: service day, business days and billing units.
- `calculation_rules`: cent rounding, adjustment order and total formulas.
- `services`: all base rates, billing units and caps.
- `threshold_premiums`: daily aggregate quantity thresholds and uplifts.
- `non_business_day_uplifts`: weekend uplifts by service.
- `volume_discounts`: thresholds, discounts, scope and ordering.
- `daily_caps`: per-patient, per-service-day limits, reconciled with the rate table.
- `bundles`: paired services and replacement rates.
- `exclusion_windows`: excluded/related services and time windows.
- `invoice_requirements`: identifiers, contract term, dates and duplicate rules.
- `review_notes`: unresolved interpretations and missing correction policies.
- `source`, `source_sections`, `source_clauses`: provenance, complete section text
  and numbered clauses. Every extracted table row also has its original text and
  one-based source line number.

## Scope and limits

This is a Hospital 1 format-specific parser, not a general legal-document parser.
Numeric table values are extracted from the input. Prose meanings were reviewed
and encoded in a Python profile. A digest of all numbered clauses prevents a
different or amended contract from silently receiving the old interpretations.
If prose changes, conversion stops and the profile must be reviewed before its
digest is updated. Table formatting, service references, units and caps are
validated; unknown rows cause an error instead of being skipped.

Percentages are integer percent values; money is integer cents; identity
multipliers use integer numerator/denominator pairs. `null` is deliberately used
for unresolved exclusion boundaries. No final correction policy for invalid
charges is invented. Invoice-description matching and label evaluation are later
steps. The JSON does not by itself audit invoices or guarantee label accuracy.

AI assisted the implementation and review; see `prompts/001_contract_conversion.md`
and `prompts/005_contract_dependencies.md`.

## Schema 1.1 additions

- `matching_guidance`: reviewed abbreviations and candidate services, explicitly
  identified as interpretation aids rather than extracted contract clauses.
- `uncertainty_policy`: the auditor's dependency/bounds policy and assumptions.
- `services[].rule_dependencies`: required fields, same-day related services,
  exclusion counterparts/windows, and cumulative usage scope. These are generated
  from the extracted rules; the auditor rejects stale/inconsistent dependencies.

## Recheck the same 100 invoices

```sh
python3 scripts/compare_contract_update.py
```

This makes no API calls. It uses cached AI proposals and the regenerated JSON,
writes `audit_output/contract_update_100/results.csv` and detailed JSONL, then
compares against labels. Original pilot files are retained unchanged. Reviewed
ambiguity takes precedence over cached AI choices. This is a development
regression comparison, not independent validation.

## Second review: 20 unanswered invoices

Matching profile revision 2 adds reviewed abbreviations (`beds`, `psych`, `rehab`,
`cont`, `emer`, `ortho`, `anaes`, `admin`, `vasc`) and
`history_only_service_families`. These are interpretation aids, not new contract
rates. An anaesthesia-administration description with conflicting modifiers stays
unresolved, even if only one catalogue service belongs to that family. Under the
explicit assumption that the service-family words are truthful, it need not block
unrelated radiotherapy or recovery-room calculations. This is not a general
permission to ignore unknown charges or to treat retrieval shortlists as complete.

The converter validates and includes this profile, its digest, and the missing
identity policy. Ambiguous descriptions still require a full provider description
or a verified service-code crosswalk. Matching the billed price or billed unit is
not sufficient identity evidence because those fields may themselves be wrong.

From the repository root, reproduce the full H1 replay without paid calls:

```sh
python3 scripts/convert_hospital_1_contract.py contracts/hospital_1/provider_services_agreement.txt structured_contracts/hospital_1.json
python3 -m unittest discover -s tests -v
python3 scripts/compare_contract_update.py --all --baseline audit_output/contract_update_all/results.jsonl --output-dir audit_output/review_20_update_all --manual-review audit_output/manual_review_20/predictions.json
```

The previous full-run files are preserved. The new directory contains invoice
results, aggregate comparisons, and `reviewed_20.json` with a reason for every
manual decision and its later label comparison. The manual predictions were
frozen before the new label comparison; this remains a label-aware development
exercise, not a blind test. The fixed manual-review script is a review record,
not a production solver. See `prompts/006_review_20_improvement.md`.
