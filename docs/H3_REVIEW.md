# H3: source review and targeted changes

Read all three supplied text documents: Base Agreement (162 lines), Appendix B
(142 lines), and Amendment No. 1 (49 lines). No external rate or H2-specific
contract term was substituted.

## Contract decisions

- Base 1.3: amendment takes precedence over Appendix B, which takes precedence
  over the base. Amendment A1.1 applies by service date from 1 January 2025.
  Seven rates change and two services become contracted. Existing date-version
  logic already handles this; it was not replaced.
- Base 6.1: usage accumulates across the whole term and all patients, excluding
  the current line, ordered by service date then line ID. No January 2025 reset
  is stated. Amendment A1.4 preserves the other terms. A regression test now
  checks a January rate amendment with prior-December usage retained.
- Base 3.3: multiply effective rate by billed quantity. Appendix B lists Focused
  Urologic Telemetry Monitoring at 5225 cents `per hour, per item`. No amendment
  clarifies this unit. It has no quantity-dependent/cross-line pricing rules,
  so the bounded billed-quantity experiment is applicable, not unit verification.

## Implemented changes and results

Enabled the existing conditional history-family bounds for H3. Unknown
anaesthesia administration and diagnostic imaging descriptions no longer affect
unrelated service families. All related-family possibilities remain unresolved.
The assumption that family words are accurate is recorded with confidence
penalties. No unknown service identity is invented.

Full cached run: `audit_output/h3_family_trial`. H3 answered 633 → 726,
unanswered 292 → 199. Of the original 209 volume-only invoices, 93 gained
verdicts. Seven of the original 18 combined unit/volume cases lost their volume
blocker and became unit-only.

Run the shared unit pilot:
`python3 unit_quantity_experiment.py --hospital H3 --baseline audit_output/h3_family_trial`

It conditionally answers all 68 resulting unit-only invoices (the original 61
plus seven combined cases), each with no detected pricing error. Quantity
measurement is still unverified and the contract unit remains null. There is no
clinical accuracy claim. No billed-quantity assumption is used to force the
remaining wrong-unit historical quantities into volume counts.

The complete `python3 main.py` workflow now recomputes this stage, preserves
earlier hospitals, and updates root `submission.csv` only after validation.

Before the later history stage, H3 reached 794 answered and 131 eligible unanswered.
Original targeted cohorts: volume-only 93/209 resolved; unit-only 61/61
conditionally answered; both blockers 7/18 conditionally answered.

Remaining disjoint groups: volume only 116; unit + volume 11; description +
history 2; unit + volume + history 1; description only 1. A major remaining
cause is H3-L00029-02: the service is known physiotherapy, but its quantity 2 is
billed per_unit_dispensed whereas Appendix B specifies visits. There is no
supported conversion proving how many visits to credit towards later discounts.
Other relevant uncertain historical quantities can cross discount thresholds.

The later held-out historical-reference stage retained numeric quantities only
where complete service wording, a compatible contextual rate, at least three
distinct reference invoices and an observed matching quantity all agreed.
Target and source-event folds were excluded. Original wrong-unit findings remain.
Together with six newly eligible unit checks, this raised the accepted result to
**898 answered / 27 eligible unanswered**, plus 14 duplicate-ID records.

No existing H3 verdict changed. Five previously missing corrected totals became
available and two existing error invoices gained a volume-discount-omitted tag.
Confidence/review reasons changed where family assumptions apply. Every H1,
H2, H4 and H5 result record in the merged output is unchanged from the prior
merged snapshot. No new API calls or spending; no push was performed.
