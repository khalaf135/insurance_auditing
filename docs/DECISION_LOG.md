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
I reused cached responses and worked through the remaining blockers one type at
a time. With the exercise's 6-8-hour cap in mind, I stopped extending coverage
and documented unresolved cases rather than forcing answers.

## Decisions and unresolved alternatives

1. **Service identity can be incomplete.** H2 "ELECT CARD SVC" could mean Nursing
   Observation or Transport (clauses 14.6/18.5). I left it unresolved. Abbreviations
   are matching assumptions, not contract facts. Price-supported matches require
   at least three outside-group reference invoices, >=80% support and <=20%
   competing support, never the target's own price; even then identity is conditional.

2. **A rate does not settle an ambiguous unit.** Four telemetry services say
   "per hour, per item" without clearly explaining the compound quantity. I kept
   those calculations unresolved and recorded the need for clarification, rather than
   select the unit that makes the bill match.

3. **Dates need explicit assumptions.** H2's day runs 07:00-06:59, but invoices
   lack timestamps. I treated service dates as billing-day labels, conditionally.
   Malformed dates are quarantined from dated patient-history checks, while their
   possible volume effects remain. This is not permission to ignore all date risks.

4. **An exclusion boundary has two readings.** "Within 10 days" could include or
   exclude day 10. I left exact-boundary exclusions unresolved, instead of treating
   the labels' inclusive reading as an explicit contract definition. The rule
   applies to the same patient; authoritative clarification remains needed.

5. **Uncertain history must not become confirmed usage.** I tracked minimum and
   maximum possible prior usage and proceeded only when uncertainty could not
   change the result. Valid events use service date then line ID; tied/missing
   ordering is bounded. Unknown quantities or incompatible units cannot confirm
   a discount. Money stays in integer cents with contract-ordered, half-up rounding.

6. **Amendments and allocation need consistent policies.** I applied H3 repricing
   by service date and retained uncertain retrospective settlements. For H5,
   missing line facilities inherit the header facility. Repeated charges across
   distinct invoices are assigned to the earliest dated bill as a stated policy,
   not an explicit allocation clause. All 62 duplicate-ID records stay internal.

7. **An error need not reveal its correction.** A cap proves excess, not the true
   quantity. I flag proven errors without guessing totals. Unresolved verdicts
   stay outside the six-column submission; only 0/1 opinions are submitted.
   Confidence is an uncalibrated evidence score, not a measured probability;
   missing totals cap it at 0.50.

## Review and disclosure

Codex assisted code, source review and writing; these were not independent expert
reviews. All 12 sampled H2-H5 blockers stayed unanswered. The early pilot used
Voyage retrieval/reranking and Gemini via OpenRouter; the current extractor and AI
fallback use Gemini. H1 labels informed development, not independent validation.
Next I would seek unit/boundary clarification and independently reviewed examples.

Sources: original contracts; `audit_output/manual_review_submission/source_review_12.json`;
`EVALUATION.md`; `prompts/README.md`; `AI_USAGE.md`.
