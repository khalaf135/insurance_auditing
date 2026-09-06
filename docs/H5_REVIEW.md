# H5 contract-first review

Starting snapshot: 702 answered and 341 eligible unanswered, plus 14 duplicate-ID
records. The requested groups were 229 volume-only, 73 unit-only, and 32 combined.

## Contract decisions

- Section 8.1 aggregates usage across all patients, facilities and plan tiers
  throughout the agreement. Service date, then line identifier, determines order.
  Deeper discounts take precedence. Existing cross-facility aggregation remains.
- Sections 3.1-3.3 require bundle substitution, facility multiplier, plan multiplier,
  premiums, then discounts, with half-up rounding after each step.
- Table 1 lists Routine Psychiatric Telemetry Monitoring as "per hour, per item"
  at 2950 cents. Tables 2 and 3 give it multipliers of 1 for every facility/tier.
  It has no listed cap, premium, bundle, volume discount or exclusion dependency.
  Section 3.1 permits a conditional calculation against the supplied billed
  quantity. It does not establish actual hours/items or physical quantity accuracy.
- Missing line facilities continue to inherit the invoice header as the existing
  disclosed assumption; this is not a new contractual definition.

## Historical description limitation

`INTERM ENDO NURS OBS` contributes uncertainty to all 263 invoices with volume
blockers. The shared history-only phrase recognizer now interprets the paired
abbreviation NURS OBS as nursing observation when that family exists in the
hospital's catalogue. OBS alone does not qualify. Conflicting explicit family
words still prevent narrowing. This changes possible historical service scope,
not identity, billed unit, price, or the original invoice's error findings.

The assumption is that the nursing-observation phrase is truthful. "ENDO" and
the exact clinical service remain unresolved. A nursing service outside the
catalogue remains possible. The line may still affect nursing-observation usage,
but not unrelated sterilisation or endoscopy volumes under this assumption.

The paired LAB PNL abbreviation is handled the same way for laboratory panels.
Neither abbreviation is installed as an exact-service mapping. Single isolated
abbreviations and conflicting explicit family words are tested to fail closed.

All conditional evidence is retained in JSON. Billed-quantity opinions carry an
uncalibrated confidence cap of 0.50 and explicitly unverified quantity status.
No H5 labels exist, so increased coverage is not measured accuracy.

## Independent historical quantity trial

The shared H3 reference experiment now also supports H5. Only the requested
volume/unit blocker groups are targeted. Complete service wording, a compatible
contextual rate, and at least three distinct reference invoices are required.
Both the target fold and the source-event fold are excluded. Numeric quantity
must also occur in the reference group. Duplicate invoice IDs are not eligible
as corroborating events. This conditionally treats a wrong unit label as a label
error, not a verified quantity conversion, and preserves the wrong-unit finding.

Two historical events passed in at least one fold. The trial added seven
volume-only opinions and exposed one additional unit-only case. Together with
the phrase-bound trial and billed-quantity checks, the final gains are:

- Original 229 volume-only: 45 answered, 184 still unanswered.
- Original 73 unit-only: all 73 conditionally answered.
- Original 32 combined: six answered; 26 still have both blockers.

H5 is now **826 answered / 217 eligible unanswered**, plus 14 duplicate-ID records.
The seven remaining cases outside the requested groups are four description-only,
two patient-history-only, and one volume plus exclusion-boundary case. One of
those history-only cases previously also had volume uncertainty.

This is a partial improvement, not a claim that all volume blockers are fixed.
Remaining historical contributors include unresolved ward-occupancy wording,
ambiguous nursing identity/units, and competing versions of duplicate invoices
such as INV-H5-000074 and INV-H5-000076. Duplicates cannot be treated as uniquely
verified utilisation. Some remaining terminology may support further bounded
review; a hospital source record is needed to settle competing invoice versions.

## Reproduction and verification

Run `python3 main.py`. It recomputes every reviewed conditional stage, preserves
the previously accepted H1-H4 records exactly, writes the complete evidence under
the run's `final/` directory, and atomically updates root submission. The CSV uses
only the template's six columns, with 3571 H2-H5 opinions; its prior version is
backed up inside that final directory. Use `--baseline-only` to skip conditional
stages and leave root submission unchanged.

All 253 tests passed, as did full-run source, reference-isolation and budget
verification. Existing H5 verdicts did not change. No new paid API calls or push.
