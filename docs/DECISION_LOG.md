# Decision log

## Approach and priorities

I first cloned the repository and worked through the contracts, invoice fields
and submission requirements. I started with H1 because its labels let me measure
results. I considered embedding the contracts and retrieving evidence for invoice
reasoning, but chose reusable JSON and deterministic checks as the foundation:
extract rules once, make calculations traceable, and limit repeated AI reasoning.

With Codex assistance, I built the first H1 TXT-to-JSON parser and invoice checker.
It answered only 40 of 908 eligible invoices, so I examined unanswered samples
with Codex against the original clauses. I expanded both the JSON and checking
code with supported rules for discounts, limits and related services, keeping
terminology assumptions separate from contract facts. I also tried a small
Voyage/Gemini retrieval pilot; later improvements reused some of its saved matches.

Next, I grouped remaining blockers into unclear descriptions, ambiguous units,
patient history and volume discounts. I introduced targeted fallback: AI proposes
description or clause interpretations, while code validates them and calculates
the result. History and volume use possible usage ranges. I asked for general
fixes to problem types, not exceptions for individual invoices, preserved earlier
results, and compared again with H1 labels. Unsupported cases stayed unanswered.

After improving H1, I used a general AI extractor for each hospital's own source
bundle, including amendments, and applied the shared workflow across hospitals.
I reused cached responses and worked through blockers one type at a time. While
improving one hospital, I found recurring patterns in others, such as compound
units, abbreviated service families and uncertain historical quantities. I then
re-read each hospital's own clauses before reusing a general solution; rules and
rates were never transferred between hospitals. After the initial time-boxed
approach, I requested further H2-H5 experiments, outside the stated 6-8-hour cap.

## Decisions and unresolved alternatives

1. **Service identity can be incomplete.** H2 "ELECT CARD SVC" could mean Nursing
   Observation or Transport. Later outside-group references supported Transport,
   conditionally: at least three reference invoices, >=80% support and <=20%
   competing support, excluding the target's own price. This is not confirmed identity.

2. **A rate does not settle an ambiguous unit.** Four telemetry services say
   "per hour, per item." Later H2-H5 pilots used supplied numeric quantities under
   each contract's multiplication clause, with explicit assumptions and confidence
   capped at 0.50. Pricing opinions do not verify hours, items or physical quantities.

3. **Dates need explicit assumptions.** H2's day runs 07:00-06:59, but invoices
   lack timestamps. I conditionally treated service dates as billing-day labels.
   Malformed dates are quarantined from dated history; possible volume effects remain.

4. **An exclusion boundary has two readings.** "Within 10 days" could include or
   exclude day 10. I left exact-boundary exclusions unresolved, instead of treating
   the labels' inclusive reading as an explicit contract definition. The rule
   applies to the same patient; authoritative clarification remains needed.

5. **Uncertain history must not become confirmed usage.** I tracked minimum and
   maximum possible prior usage. Later family-based bounds assume family wording
   is truthful; H3/H5 reference-supported experiments retain numeric historical
   quantities despite wrong unit labels, conditionally, preserving those errors.
   Ordering uses service date then line ID; money uses contract-ordered half-up cents.

6. **Amendments and allocation need consistent policies.** I applied H3 repricing
   by service date and retained uncertain retrospective settlements. For H5,
   missing line facilities inherit the header facility. Repeated charges across
   distinct invoices are assigned to the earliest dated bill as a stated policy,
   not an explicit allocation clause. Reused invoice IDs are proven errors. H1
   consistently supports the uniquely latest-dated occurrence as the submission
   record, so I use that policy for 26 scored duplicate IDs, submit only
   `duplicate_invoice_id`, leave the corrected total blank and cap confidence at 0.50.

7. **An error need not reveal its correction.** A cap proves excess, not the true
   quantity. I flag proven errors without guessing totals. Unresolved verdicts
   stay outside the six-column submission; only 0/1 opinions are submitted.
   Confidence is an uncalibrated evidence score, not a measured probability;
   missing totals cap it at 0.50.

8. **H4's missing service mapping remains unresolved.** `VST foc NEURO /CW-8682`
   occurs once; other contracts provide no reliable equivalent. Its price matches
   Urologic Imaging Interpretation but conflicts with the neurological-visit wording.
   I did not transfer rates or guess from price. All 69 remaining H4 volume-only
   cases depend on it. Request H4's code crosswalk; retain uncertainty meanwhile.

## Review and disclosure

Codex assisted development and review; the pilot used Voyage and Gemini.
Current AI stages use Gemini. H1 results are development evidence. In my view,
authorized humans should decide exceptions for exceptional circumstances; this
assessment applies no discretionary exceptions. I left invoices with insufficient
or ambiguous evidence unanswered and documented their reasons for human review.

Sources: contracts; H2-H5 review notes; `EVALUATION.md`; `prompts/README.md`; `AI_USAGE.md`.
