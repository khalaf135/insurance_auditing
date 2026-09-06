# H4 contract-first review

Read the complete 281-line Conditional Reimbursement Agreement. Section 1.4
defines an instance as one unit, not one invoice or line. Sections 4.1–4.4
require bundle substitution, multipliers, premiums, then discount, with rounding
after each step. Sections 8.3–8.5 require prior usage strictly above the threshold,
exclude the threshold-crossing line, and select only the deepest discount.
Existing engine rules were retained.

Enabled source-reviewed service-family history bounds for H4. An unresolved
sterilisation description no longer contributes possible usage to unrelated
ventilation or occupancy services. This assumes truthful service-family wording;
it does not identify the historical service. Assumptions and confidence penalties
remain visible. The full cached run `audit_output/h4_family_trial` increased
answered invoices from 636 to 665 without changing any existing verdict.

Section 4.4 also supports the separate billed-quantity pricing check.
Intermittent Urologic Telemetry Monitoring has a rate of 7275 cents and the
unresolved compound wording `per hour, per item`. It is a related service for
an exclusion affecting laboratory panels, but not the excluded service itself.
The pilot now permits this presence-only dependency without changing the
existing audit's occurrence/exclusion findings. Quantity-dependent rules or
an exclusion of the priced service itself still prevent this shortcut.

Run `python3 unit_quantity_experiment.py --hospital H4 --baseline audit_output/h4_family_trial`.
All 89 unit-only invoices in that run have conditional pricing verdict 0 (85
original unit-only plus four previously combined cases). This does not verify
the physical quantity or resolve the contractual compound unit.

The complete `python3 main.py` workflow now recomputes the pilot and merge.
H2/H3's accepted records are preserved, and root submission is updated only
after validation with the previous CSV backed up in the run's final directory.

## Results

H4: **754 answered / 76 eligible unanswered**, plus 10 duplicate-ID records.
Gain: 29 family-bound opinions and 89 billed-quantity conditional opinions.
Remaining groups: volume only 69; volume + unit 3; history only 2;
description + history 2. `VST foc NEURO` is a major remaining historical blocker:
visit wording alone does not establish a specific clinical service family, so
it was not silently mapped to home visits.

All other hospitals' merged records are identical to the prior accepted snapshot.
Existing H4 verdicts are unchanged; two totals and one category set changed as
pricing dependencies became calculable, and evidence scores/reasons changed.
The new answers are conditional coverage, not labelled H4 accuracy.

All 249 tests and full-run source/reference/budget verification passed. Root
submission bytes and the prior backup were verified. No paid calls, labels or push.
