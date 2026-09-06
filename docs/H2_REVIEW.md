# H2 contract-first review

H2's service day runs 07:00-06:59. Because invoice lines contain dates but no
times, the audit conditionally treats each service date as the stated billing-day
label and never shifts it. Malformed dates remain quarantined from dated history.

The contract's cumulative discounts use prior utilisation of the same service,
exclude the current line, use strict thresholds and apply only the deepest
eligible discount. History-only service-family bounds therefore exclude clearly
unrelated abbreviated families while retaining all related possibilities. Exact
identity is not inferred. Unique catalogue-prefix expansion is allowed only for
tokens of at least four letters with one completion in the hospital catalogue.

Two incomplete description patterns were separately tested using other invoice
and patient folds. A candidate required at least three independent reference
invoices, clear full-word anchors, at least 80% support and no more than 20%
competing support. Target billed price never selected its own identity. This
conditionally resolved 57 description-only invoices; it is billing-pattern
evidence, not clinical confirmation or labelled accuracy.

Assisted Infectious Telemetry Monitoring is literally priced at 10625 cents
"per hour, per item." The contract does not define the compound measurement.
The unit stage follows the contract's effective-rate-times-billed-quantity clause
and conditionally prices the supplied numeric quantity. It does not verify actual
hours, items or delivery and caps confidence at 0.50. The normal unit finding is
not rewritten as a clarified contract definition.

Final accepted H2 result: **1093 answered / 25 eligible unanswered**, plus 14
duplicate-ID records. All 57 description-reference and 137 unit-only conditional
opinions were added without changing an existing verdict. H2 has no labels, so
this measures coverage only. The complete workflow now reproduces these stages
from a fresh run with `python3 main.py`; no separate commands are required.
