# Evaluation report

## How I measured the result

I compared predictions with H1 labels by invoice ID. All 10 records sharing five
duplicate IDs were excluded, leaving **908 eligible invoices**. H1 labels and
previous mistakes informed development: these results are not an independent
test. Reference groups prevent using an invoice's own price to infer its service;
they do not undo that development exposure.

The first full-H1 baseline answered **40/908 (4.4%): 37 correct, 3 incorrect**,
with 868 unanswered. The final results below include rule improvements and
validated, cached AI evidence; they are not a JSON-only comparison.

**Error/no-error decisions: 908/908 correct**, including all 53 erroneous invoices.
**Corrected totals: 876 supplied and correct; 32 unanswered.** Error-category sets
matched exactly on 885/908 invoices, but only **30/53 erroneous invoices**.
Getting the verdict right is therefore not the same as completing the audit.

Across all hospitals, **3,651 of 4,824 eligible invoices were answered**; 1,173
remain unanswered, plus 62 duplicate-ID records excluded from submission. The
H2-H5 submission contains **2,743 opinions out of 3,916 eligible invoices**.
H2-H5 have no labels, so these are coverage figures, not measured accuracy.

## Category results, simplified

**12 of 17 error categories had no missed or extra tags.** These cover arithmetic
and totals, contract numbers, duplicate charges, malformed/out-of-window dates,
bundles, daily caps, premiums and volume discounts. The five needing work are:

1. **Wrong price:** found 10/10 labelled cases, but added 17 extra tags.
2. **Unknown service:** found 8/12 cases; missed 4, with no extra tags.
3. **Wrong billing unit:** found 10/10 cases, with 1 extra tag.
4. **Service after invoice date:** found 5/5 cases, with 2 extra tags.
5. **Exclusion window:** found 0/3 cases; the boundary interpretation remained
   unresolved.

These count error tags, not distinct invoices; several tags can apply to one
invoice. An extra tag is a mismatch with the labels, not necessarily a wrong
error/no-error decision. Full counts, precision and recall for every original
category remain in the evaluation JSON referenced below.

## Four ways the approach still falls short

1. **The right verdict, but an over-broad explanation.** INV-H1-000186 correctly
   identifies an unearned discount and arithmetic error, but also adds a generic
   wrong-price tag. Its corrected total is right. This pattern explains all 17
   extra wrong-price tags. Next: define when a specific explanation should replace
   a generic one, without hiding separately supported errors.

2. **Missing words can lead to the wrong service match.** INV-H1-000236 says
   "Fract Outpatient Radiotherapy"; the checker assumes the missing modifier
   "Metabolic". The label instead treats it as an unknown service. A short list
   of possible matches is not proof. Next: require stronger evidence for clinical
   modifiers or request a provider service-code crosswalk.

3. **A clause can leave a boundary unclear.** INV-H1-000211 has relevant services
   exactly 10 days apart. The contract says "within" 10 days without explicitly
   defining that endpoint. Labels include it; the checker withholds that category.
   All three missed exclusions have this pattern. Next: obtain or clearly declare
   a boundary interpretation, rather than present it as a contract fact.

4. **Finding an error does not reveal the correct amount.** INV-H1-000049 bills
   15 hours against a 12-hour cap. The cap proves excess, but not the intended
   quantity; simply changing 15 to 12 does not recover the labelled total.
   Next: seek the underlying service record. All 32 withheld totals belong to
   erroneous invoices; none of the supplied H1 totals is wrong.

## Confidence and next steps

Confidence reflects evidence strength, **not a calibrated probability**. Weaker
matches, assumptions and missing totals lower it. The development-only confidence
diagnostic is retained in the detailed results; it does not establish calibration
on unseen hospitals. Date quarantine isolates malformed dates from dated history
checks, not from every possible effect; uncertainty remains where it matters.

With another week, I would prioritize authoritative unit and date-boundary
clarifications, stronger service matching, and independently reviewed examples
for testing and confidence calibration. I would not force unresolved invoices
into 0/1 answers merely to increase coverage.

## Evidence and reproduction

Run `python3 main.py` for a cached-only audit and the six-column submission.
Full per-category results for this snapshot:
[audit_output/test_all_20260905_submission_final/evaluation.json](../audit_output/test_all_20260905_submission_final/evaluation.json).
New runs also produce `evaluation.json`. See [docs/DECISION_LOG.md](DECISION_LOG.md),
[prompts/README.md](../prompts/README.md) and [AI_USAGE.md](../AI_USAGE.md) for
assumptions and development provenance.
