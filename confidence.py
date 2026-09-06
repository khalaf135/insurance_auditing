"""Explainable evidence scores, NOT calibrated probabilities or new audit verdicts.

Scores are declared policy tiers, never AI self-ratings or similarity probabilities.
The row score is the weakest component, capped at .50 when its total is unresolved.
Taking a minimum is a conservative heuristic, not a statistical joint probability.
No labels, API calls, invoice prices or hospital-specific weights are used here.
"""

METHOD = 'evidence_score_v1_uncalibrated'
NOTE = ('Provisional evidence score, not measured accuracy or a calibrated probability. '
        'Verdict, asserted categories and corrected total are assessed separately; '
        'category confidence does not prove that no other errors exist. '
        'the submission score reflects the weakest submitted component.')
FIELDS = ('confidence', 'verdict_confidence', 'total_confidence', 'category_confidence',
          'confidence_reasons', 'confidence_method', 'confidence_note')
INDEPENDENT_ERRORS = frozenset({
    'invoice_total_mismatch', 'line_total_arithmetic', 'malformed_service_date',
    'service_date_after_invoice_date', 'service_date_out_of_window',
    'contract_number_mismatch',
})


def _line_evidence(line):
    """Assess recorded matching/dependency evidence without selecting a service."""
    match = line.get('match') or {}
    method = match.get('method')
    reasons = set()
    if match.get('status') != 'accepted' or not match.get('service_name'):
        score = .35
        reasons.add('Service identity remains unresolved.')
    elif match.get('inferred') or method == 'price_pattern_inference':
        evidence = match.get('resolution_evidence') or {}
        count = len(set(evidence.get('reference_invoice_ids') or []))
        score = .72 if count >= 10 else (.68 if count >= 3 else .50)
        reasons.add('Service identity depends on reference prices, not confirmed wording.')
        if count < 3:
            reasons.add('Fewer than three independent references are recorded.')
    elif method in {'full_catalogue_token_constraints', 'reviewed_description'}:
        score = .90
        reasons.add('Service identified through catalogue constraints or reviewed wording.')
    elif method in {'ai_description_mapping', 'ai_proposed_catalogue_mapping'}:
        score = .78
        reasons.add('Service depends on a validated AI wording interpretation.')
    elif method is None:
        # The existing fuzzy matcher omits method; margin measures competition,
        # not accuracy. Do not count its top-three shortlist as three live matches.
        score = .84
        reasons.add('Service identified by accepted fuzzy wording match.')
        margin = match.get('margin')
        if not isinstance(margin, (int, float)) or margin < .20:
            score = .76
            reasons.add('Wording match has a narrow or unrecorded candidate margin.')
    else:
        score = .50
        reasons.add('Matching method has no defined evidence tier.')

    context = line.get('pricing_context') or {}
    if context.get('assumptions') or line.get('history_presence_assumptions') or line.get('presence_assumption'):
        score -= .05
        reasons.add('Pricing or historical service presence depends on an assumption.')
    if context.get('review') or line.get('history_dependency_reasons'):
        score = min(score, .50)
        reasons.add('A pricing or patient-history dependency remains uncertain.')
    if line.get('quarantined_history'):
        score -= .05
        reasons.add('Historical checks exclude invalid dates under the quarantine assumption.')
    bounds = line.get('utilisation_bounds') or {}
    if bounds.get('uncertain_contributors'):
        same_discount = bounds.get('discount_percent_lower') == bounds.get('discount_percent_upper')
        score -= .02 if same_discount else .08
        reasons.add('Earlier usage is bounded rather than fully known.')
    return round(max(.10, score), 2), reasons


def score_invoice(row):
    """Return confidence fields for a rich audit row without changing the row."""
    result = dict.fromkeys(('confidence', 'verdict_confidence', 'total_confidence', 'category_confidence'))
    result.update(confidence_method=METHOD, confidence_note=NOTE, confidence_reasons=[])
    if row.get('record_identity_ambiguous') or row.get('flagged') not in (0, 1):
        result['confidence_reasons'] = [
            'Duplicate invoice identity; no opinion submitted.' if row.get('record_identity_ambiguous')
            else 'Not answered; no numeric confidence assigned.'
        ]
        return result

    reasons = set()
    line_scores = []
    for line in row.get('lines') or []:
        score, evidence_reasons = _line_evidence(line)
        line_scores.append(score)
        reasons.update(evidence_reasons)
    pricing = min(line_scores, default=.40)
    if not line_scores:
        reasons.add('Detailed line evidence is unavailable.')
    if row.get('service_day_assumption'):
        pricing = max(.10, pricing - .05)
        reasons.add('Pricing assumes service_date is the contractual billing-day label.')
    if row.get('review_reasons'):
        pricing = min(pricing, .50)
        reasons.add('The audit retains unresolved review reasons.')

    # One independently proven error supports an erroneous verdict, but does not
    # establish every other asserted category or the complete corrected total.
    categories = {c for c in (row.get('error_category') or '').split('|') if c}
    finding_scores = {}
    for finding in row.get('findings') or []:
        category = finding.get('category')
        score = .95 if category in INDEPENDENT_ERRORS else pricing
        evidence = finding.get('evidence')
        if isinstance(evidence, dict) and evidence.get('assumption'):
            score = min(score, .65)
            reasons.add('An error finding depends on a documented interpretation.')
        finding_scores[category] = max(finding_scores.get(category, 0), score)
    if row['flagged'] == 1:
        supports = [finding_scores.get(c, .40) for c in categories] or [.40]
        verdict, category_score = max(supports), min(supports)
        if categories & INDEPENDENT_ERRORS & finding_scores.keys():
            reasons.add('An independent arithmetic, date or contract-ID check supports the error verdict.')
        if not categories or categories - finding_scores.keys():
            reasons.add('A claimed error lacks a recorded finding.')
    else:
        verdict = category_score = pricing
        reasons.add('A correct verdict depends on every assessed line and pricing dependency.')

    total = pricing if row.get('expected_total_cents') is not None else None
    if total is None:
        reasons.add('Corrected total is unresolved; row confidence is capped at 0.50.')
    elif not line_scores or any(line.get('expected_line_total_cents') is None for line in row.get('lines', [])):
        total = min(total, .40)
        reasons.add('The supplied total lacks complete line-calculation evidence.')
    # Interpretation-dependent findings also constrain the corrected total.
    if total is not None and row['flagged'] == 1:
        total = min(total, category_score)
    result.update(
        verdict_confidence=round(verdict, 2), category_confidence=round(category_score, 2),
        total_confidence=round(total, 2) if total is not None else None,
        confidence=round(min(verdict, category_score, total if total is not None else .50), 2),
        confidence_reasons=sorted(reasons),
    )
    return result
