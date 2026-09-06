"""Deterministic invoice rules, shared by current and frozen audit profiles.

This module performs no file discovery, label loading or external API calls.
Description matching lives in matching.py; contracts.py supplies dependency and
runtime pricing interpretation through local imports to avoid module cycles.
"""
from bisect import bisect_left, bisect_right
from collections import Counter, defaultdict
from dataclasses import dataclass
from datetime import date
import re

from matching import (
    ALIASES, Matcher, integer, parse_date, rounded_ratio, tokens, usable_line_id, history_family_scope,
)


COLUMNS = ["invoice_id", "flagged", "error_category", "expected_total_cents", "billed_total_cents", "confidence"]
ORDER = ["bundle_substitution", "facility_multiplier", "plan_tier_multiplier", "premium_or_uplift", "volume_discount"]


@dataclass(frozen=True)
class AuditProfile:
    """The only differences between the three supported audit contracts."""

    name: str
    current_contract_rules: bool
    policy_evidence: bool


CURRENT_PROFILE = AuditProfile("current", True, True)
H1_PROFILE = AuditProfile("h1", False, True)
BASELINE_PROFILE = AuditProfile("baseline", False, False)
_PROFILES = {profile.name: profile for profile in (CURRENT_PROFILE, H1_PROFILE, BASELINE_PROFILE)}


def _resolve_profile(profile):
    if isinstance(profile, AuditProfile) and profile in _PROFILES.values():
        return profile
    if isinstance(profile, str) and profile in _PROFILES:
        return _PROFILES[profile]
    raise ValueError(f"Unknown audit profile: {profile!r}")


def validate_contract(c, *, profile="current"):
    """Fail closed for semantic shapes unsupported by the selected profile."""
    from contracts import dependencies_for, precise_dependencies

    profile = _resolve_profile(profile)
    def need(ok, message):
        if not ok:
            raise ValueError(message)
    need(c.get("schema_version") in ("1.0", "1.1"), "Unsupported schema_version")
    allowed = {"schema_version", "source", "contract_details", "definitions", "calculation_rules", "services",
               "threshold_premiums", "non_business_day_uplifts", "volume_discounts", "daily_caps", "bundles",
               "exclusion_windows", "invoice_requirements", "review_notes", "source_sections", "source_clauses", "matching_guidance", "uncertainty_policy"}
    need(not (set(c) - allowed), f"Unsupported contract sections: {sorted(set(c) - allowed)}")
    calc = c["calculation_rules"]
    need(calc["adjustment_order"] == ORDER and calc["rounding"] == "half_up_cent" and calc["round_after_each_adjustment"] is True,
         "Unsupported calculation/rounding order")
    need(calc["line_total"] == "effective_unit_rate_cents * billed_quantity" and calc["invoice_total"] == "sum(line_totals_cents)", "Unsupported total formulas")
    need(c["definitions"]["service_day"] == "calendar_date_of_line_service_date", "Non-calendar service days require an engine extension")
    details = c["contract_details"]
    need(parse_date(details["effective_from"]) is not None and parse_date(details["effective_to"]) is not None, "Invalid contract dates")
    need(details["effective_from"] <= details["effective_to"], "Reversed contract dates")
    for key in ("facility_multiplier", "plan_tier_multiplier"):
        m = details[key]
        need(set(m) == {"numerator", "denominator"} and all(integer(x) and x > 0 for x in m.values()),
             f"Unsupported {key}; this schema supports a single rational multiplier only")
    need(set(details) <= {"contract_number", "provider", "payer", "effective_from", "effective_to", "currency", "facilities", "plan_tiers", "facility_multiplier", "plan_tier_multiplier", "source_clauses"}, "Unsupported contract metadata/rate versioning")
    services = c["services"]
    names = {s["service_name"] for s in services}
    need(bool(names) and len(names) == len(services), "Missing/duplicate services")
    for s in services:
        need(set(s) <= {"service_name", "unit_basis", "unit_basis_source", "base_rate_cents", "daily_cap", "source", "rule_dependencies"}, "Unsupported service field (e.g. amendments require an extension)")
        need(integer(s["base_rate_cents"]) and s["base_rate_cents"] >= 0, "Non-integer/negative rate")
    scope = ["patient_id", "service", "service_date"]
    for key in ("threshold_premiums", "daily_caps"):
        need(c[key]["group_by"] == scope, f"Unsupported {key} scope")
    v = c["volume_discounts"]
    need(v["scope"] == "whole_contract_term_all_patients" and v["group_by"] == ["service"] and
         v["sort_by"] == ["service_date", "line_id"] and v["include_current_line_in_threshold"] is False and
         v["multiple_thresholds"] == "deepest_discount_only", "Unsupported utilisation semantics")
    need(c["bundles"]["group_by"] == ["patient_id", "service_date"] and c["bundles"]["condition"] == "both_services_present" and c["bundles"]["replaces"] == "base_unit_rates", "Unsupported bundle semantics")
    need(c["exclusion_windows"]["direction"] == "both" and c["exclusion_windows"]["patient_scope"]["value"] == "same_patient", "Unsupported exclusion semantics")
    need(c["exclusion_windows"]["boundary_inclusive"] in (None, True, False), "Invalid exclusion boundary")
    r = c["invoice_requirements"]
    need(all(r[k] is True for k in ("contract_number_must_match", "invoice_id_must_be_unique", "service_date_within_contract_term", "service_date_not_after_invoice_date")), "Unsupported invoice requirements")
    need(r["duplicate_service_key"] == scope and (r["duplicate_scope"] == "within_and_across_invoices" or (profile.current_contract_rules and r["duplicate_scope"] == "not_stated")) and r["service_date_within_admission_discharge"] == "not_stated", "Unsupported duplicate/stay rules")
    for key in ("threshold_premiums", "non_business_day_uplifts", "volume_discounts", "daily_caps", "bundles", "exclusion_windows"):
        fields = {
            "threshold_premiums": {"service_name", "quantity", "unit_basis", "source", "uplift_percent", "comparison"},
            "non_business_day_uplifts": {"service_name", "uplift_percent", "source"},
            "volume_discounts": {"service_name", "quantity", "unit_basis", "source", "discount_percent", "comparison"},
            "daily_caps": {"service_name", "quantity", "unit_basis", "source"},
            "bundles": {"service_a", "service_b", "bundled_rate_a_cents", "bundled_rate_b_cents", "source"},
            "exclusion_windows": {"excluded_service", "related_service", "window_days", "source"},
        }[key]
        for rule in c[key]["rules"]:
            need(set(rule) <= fields, f"Unsupported fields in {key}: {sorted(set(rule) - fields)}")
            for field in ("service_name", "service_a", "service_b", "excluded_service", "related_service"):
                if field in rule:
                    need(rule[field] in names, f"Unknown service reference: {rule[field]}")
            for field in ("quantity", "window_days", "bundled_rate_a_cents", "bundled_rate_b_cents", "uplift_percent", "discount_percent"):
                if field in rule:
                    need(integer(rule[field]) and rule[field] >= 0, f"Invalid {field}")
            if "discount_percent" in rule:
                need(rule["discount_percent"] <= 100, "Discount exceeds 100 percent")
            if key in ("threshold_premiums", "volume_discounts"):
                need(rule["comparison"] == "greater_than", "Unsupported threshold comparison")
    if c["schema_version"] == "1.1":
        need(c["uncertainty_policy"]["mode"] == "rule_dependency_bounds", "Unsupported uncertainty policy")
        for s in services:
            derive = dependencies_for
            if profile.current_contract_rules and c['uncertainty_policy'].get('precise_dependency_scopes'):
                derive = precise_dependencies
            need(s.get("rule_dependencies") == derive(c, s["service_name"]), "Stale or inconsistent rule dependencies; regenerate contract JSON")
        for entry in c["matching_guidance"]["reviewed_descriptions"]:
            need(entry["status"] in ("confirmed", "ambiguous") and bool(entry["candidates"]), "Invalid reviewed mapping")
            need(all(name in names for name in entry["candidates"]), "Reviewed mapping references unknown service")
            need((len(entry["candidates"]) == 1) == (entry["status"] == "confirmed"), "Inconsistent mapping status")
        for family in c["matching_guidance"].get("history_only_service_families", []):
            need(len(set(family["tokens"])) >= 2 and bool(family.get("evidence")), "Invalid history-only family")
            need(all(isinstance(t, str) and re.fullmatch(r"[a-z]+", t) for t in family["tokens"]), "Invalid family token")
            need(any(set(family["tokens"]) <= tokens(n) for n in names), "Unknown history-only family")
    return c


def duplicate_allocation(group):
    """Allocate only when the earliest invoice is unique and has one occurrence."""
    if any(r["invoice_date"] is None or not r["invoice_identity_unique"] for r in group):
        return {"status": "unresolved", "reason": "Missing valid date or unique invoice identity"}
    earliest = min(r["invoice_date"] for r in group)
    first = [r for r in group if r["invoice_date"] == earliest]
    if len(first) != 1:
        return {"status": "unresolved", "reason": "Earliest invoice date ties or contains repeated service lines"}
    return {"status": "allocated", "first_record": first[0]["record"],
            "first_invoice_id": first[0]["raw"]["invoice_id"],
            "first_invoice_date": earliest.isoformat(),
            "source_clause": "11.4",
            "assumption": "Preserve earliest dated billing; flag later repeats. Contract prohibits duplication but does not state allocation order."}


def unsupported_service_evidence(description, contract, match):
    """Recognised words in an impossible catalogue combination, not failed search.

    Literal wording and catalogue completeness are explicit assumptions. Existing
    accepted identities and compatible exhaustive candidate sets are preserved.
    Unknown tokens, short descriptions, abbreviations not in our dictionary, and
    approximate prefix matches remain uncertain rather than becoming errors.
    """
    if match.get("service_name") is not None:
        return None
    aliases = contract.get("matching_guidance", {}).get("token_aliases", {})
    query = tokens(description, aliases)
    catalogue = [(s["service_name"], tokens(s["service_name"], aliases)) for s in contract["services"]]
    vocabulary = set().union(*(words for _, words in catalogue))
    if len(query) < 3 or not query <= vocabulary:
        return None
    # Exhaustive catalogue scan; no price, label, top-k or model confidence gate.
    compatible = [name for name, words in catalogue
                  if all(any(Matcher.token_score(q, w) >= .94 for w in words) for q in query)]
    if compatible:
        return None
    if (match.get("candidate_scope_complete_under_description_assumption")
            and match.get("method") != "history_only_service_family" and match.get("candidates")):
        return None
    return {"method": "recognised_tokens_conflict_with_full_catalogue",
            "normalised_description_tokens": sorted(query),
            "catalogue_services_checked": len(catalogue), "compatible_services": [],
            "source_section": "4",
            "assumption": "Recognised description words are truthful and this contract catalogue is complete; a transcription error could invalidate this inference.",
            "correction_policy": "Flag unsupported service; do not invent a replacement service or a corrected charge."}


def audit(contract, invoices, service_overrides=None, bounded_history=False,
          line_service_resolutions=None, *, policy=None, extended_contract=None,
          line_candidate_scopes=None, matcher_factory=Matcher, profile="current", historical_unit_estimates=None):
    """Audit invoice records using one engine and an explicit compatibility profile.

    The current profile supports runtime contract pricing and refined historical
    bounds. H1 keeps its frozen historical semantics; baseline also omits policy
    evidence. A matcher factory is injected per call, without mutating globals.
    """
    profile = _resolve_profile(profile)
    if not profile.current_contract_rules and (extended_contract is not None or line_candidate_scopes):
        raise ValueError("Runtime pricing and candidate scopes require the current profile")
    if not profile.policy_evidence and policy:
        raise ValueError("The baseline profile does not support audit policies")
    if extended_contract:
        from contracts import pricing_context
    policy = policy or {}
    if set(policy) - {"allocate_later_duplicates", "classify_unsupported_services", "quarantine_invalid_history_dates"}:
        raise ValueError("Unknown audit policy")
    validate_contract(contract, profile=profile)
    catalog = {s["service_name"]: s for s in contract["services"]}
    if historical_unit_estimates and not profile.current_contract_rules:
        raise ValueError('Historical quantity experiments require current profile')
    matcher = matcher_factory(contract["services"], service_overrides, contract.get("matching_guidance"))
    resolutions = line_service_resolutions or {}
    seen_resolutions = set()
    dependency_mode = contract.get("uncertainty_policy", {}).get("mode") == "rule_dependency_bounds"
    improved_usage = profile.current_contract_rules and contract.get('uncertainty_policy', {}).get('improved_usage_bounds', False)
    if historical_unit_estimates and not improved_usage:
        raise ValueError('Historical quantity estimates require bounded usage mode')
    precise_history = profile.current_contract_rules and contract.get('uncertainty_policy', {}).get('precise_dependency_scopes', False)
    seen_candidate_scopes = set()
    bounded_history = bounded_history or dependency_mode
    results, lines = [], []
    id_counts = Counter(i.get("invoice_id") for i in invoices)
    day_lines, patient_days = defaultdict(list), defaultdict(list)
    uncertain_patients, uncertain_services = set(), set()
    global_unmapped = False
    details = contract["contract_details"]
    term_start, term_end = parse_date(details["effective_from"]), parse_date(details["effective_to"])

    def finding(result, category, line_id=None, evidence=None):
        result["findings"].append({"category": category, "line_id": line_id, "evidence": evidence})

    def review(result, reason):
        if reason not in result["review_reasons"]:
            result["review_reasons"].append(reason)

    for index, inv in enumerate(invoices):
        result = {"record_index": index, "invoice_id": inv.get("invoice_id"), "billed_total_cents": inv.get("invoice_total_cents"),
                  "findings": [], "review_reasons": [], "lines": []}
        results.append(result)
        patient = inv.get("patient_id")
        if precise_history and not patient:
            patient = None
        inv_date = parse_date(inv.get("invoice_date"))
        if not patient or not inv.get("invoice_id"):
            review(result, "Missing patient_id or invoice_id")
        if inv_date is None:
            review(result, "Invalid invoice_date")
        if inv.get("contract_number") != details["contract_number"]:
            finding(result, "contract_number_mismatch", evidence=inv.get("contract_number"))
        if id_counts[inv.get("invoice_id")] > 1:
            finding(result, "duplicate_invoice_id")
            review(result, "Duplicate invoice correction/record identity requires review")
        if inv.get("facility_code") not in {f["code"] for f in details["facilities"]} or inv.get("plan_tier") not in details["plan_tiers"]:
            review(result, "Unknown facility or plan tier")
        items = inv.get("line_items")
        if not isinstance(items, list) or not items:
            review(result, "Missing or empty line_items")
            continue
        if integer(inv.get("invoice_total_cents")) and all(integer(x.get("line_total_cents")) for x in items):
            if sum(x["line_total_cents"] for x in items) != inv["invoice_total_cents"]:
                finding(result, "invoice_total_mismatch", evidence={"sum_line_totals_cents": sum(x["line_total_cents"] for x in items)})
        else:
            review(result, "Missing/non-integer monetary fields")
        for raw in items:
            line_id = raw.get("line_id")
            valid_line_id = usable_line_id(line_id) if profile.current_contract_rules else bool(line_id)
            match = matcher.match(str(raw.get("description", "")))
            resolution_key = (index, line_id) if valid_line_id or not profile.current_contract_rules else None
            if valid_line_id and resolution_key in (line_candidate_scopes or {}):
                seen_candidate_scopes.add(resolution_key)
                scope = line_candidate_scopes[resolution_key]
                names = scope['candidate_services']
                if match['service_name'] is not None or not names or not set(names) <= set(catalog) or scope.get('basis') != 'exhaustive_catalogue_after_validated_AI_expansions':
                    raise ValueError('Invalid historical candidate scope')
                match = {**match, 'candidates': [{'service_name': n, 'similarity': None} for n in names],
                    'candidate_scope_complete_under_description_assumption': True,
                    'method': 'ai_expanded_candidate_scope', 'scope_evidence': scope}
            if (valid_line_id or not profile.current_contract_rules) and resolution_key in resolutions:
                proposal = resolutions[resolution_key]
                name = proposal.get("service_name")
                if (match["service_name"] is not None or name not in catalog or
                    proposal.get("basis") not in ("ai_description_mapping", "price_pattern_inference") or
                    not proposal.get("explanation")):
                    raise ValueError("Invalid scoped service resolution")
                if (match.get("candidate_scope_complete_under_description_assumption") and
                    name not in {x["service_name"] for x in match["candidates"]}):
                    raise ValueError("Scoped resolution contradicts exhaustive description candidates")
                if resolution_key in seen_resolutions:
                    raise ValueError("Scoped resolution has a duplicated line identifier")
                seen_resolutions.add(resolution_key)
                match = {**match, "service_name": name, "status": "accepted",
                         "original_method": match.get("method"), "method": proposal["basis"],
                         "resolution_evidence": proposal, "inferred": proposal["basis"] == "price_pattern_inference"}
            service = match["service_name"]
            d = parse_date(raw.get("service_date"))
            valid_qty = integer(raw.get("quantity")) and raw["quantity"] > 0
            entry = {"line_id": line_id, "description": raw.get("description"), "match": match,
                     "expected_line_total_cents": None, "calculation": []}
            result["lines"].append(entry)
            row = {"raw": raw, "out": entry, "result": result, "patient": patient, "service": service, "date": d,
                   "valid_qty": valid_qty, "record": index, "invoice_date": inv_date,
                   "invoice_identity_unique": id_counts[inv.get("invoice_id")] == 1}
            row['valid_line_id'] = valid_line_id
            # A positive service charge whose quantity is absent asserts an
            # occurrence, conditionally on the invoice describing an actual
            # delivery. Zero/negative/otherwise-invalid quantities do not.
            row['presence_known'] = valid_qty or (raw.get('quantity') is None and
                integer(raw.get('line_total_cents')) and raw['line_total_cents'] > 0)
            if precise_history and not row['invoice_identity_unique']:
                row['presence_known'] = False
            if precise_history and row['presence_known'] and not valid_qty:
                entry['presence_assumption'] = 'Positive billed service record with missing quantity is evidence of occurrence only; no quantity or unit conversion is inferred.'
            row['units_reliable'] = bool(service and catalog[service]['unit_basis'] is not None and
                                         catalog[service]['unit_basis'] == raw.get('unit_basis_as_billed'))
            lines.append(row)
            if not valid_line_id or raw.get("invoice_id") != inv.get("invoice_id"):
                review(result, "Missing line_id or inconsistent parent invoice_id")
            if not service:
                if policy.get("classify_unsupported_services"):
                    evidence = unsupported_service_evidence(raw.get("description", ""), contract, match)
                    if evidence:
                        finding(result, "unknown_service", line_id, evidence)
                review(result, f"Unresolved service description on {line_id}")
                uncertain_patients.add(patient)
                global_unmapped = True
            if d is None:
                finding(result, "malformed_service_date", line_id, raw.get("service_date"))
                review(result, f"Cannot reconstruct malformed service date on {line_id}")
                uncertain_patients.add(patient)
                if service:
                    uncertain_services.add(service)
            else:
                if not term_start <= d <= term_end:
                    finding(result, "service_date_out_of_window", line_id)
                if inv_date and d > inv_date:
                    finding(result, "service_date_after_invoice_date", line_id,
                            {"service_date": d.isoformat(), "invoice_date": inv_date.isoformat()})
            if not valid_qty:
                review(result, f"Invalid quantity on {line_id}")
                uncertain_patients.add(patient)
                if service:
                    uncertain_services.add(service)
            if valid_qty and integer(raw.get("unit_price_cents")) and integer(raw.get("line_total_cents")):
                if raw["quantity"] * raw["unit_price_cents"] != raw["line_total_cents"]:
                    finding(result, "line_total_arithmetic", line_id)
            else:
                review(result, f"Invalid numeric fields on {line_id}")
            if profile.current_contract_rules and service and catalog[service]["unit_basis"] is None:
                review(result, f"Contract unit basis ambiguous on {line_id}")
            if service and (catalog[service]["unit_basis"] is not None or not profile.current_contract_rules) and raw.get("unit_basis_as_billed") != catalog[service]["unit_basis"]:
                finding(result, "wrong_unit_basis", line_id, {"expected": catalog[service]["unit_basis"]})
                review(result, f"Unit conversion/correction unresolved on {line_id}")
            # Presence is sufficient for bundles/exclusions/duplicate identity.
            # A missing quantity or unit conversion must not erase a known event.
            if service and d and (row['presence_known'] if precise_history else valid_qty) and patient:
                day_lines[(patient, service, d)].append(row)
                patient_days[(patient, service)].append(d.toordinal())

    if set(resolutions) != seen_resolutions:
        raise ValueError("Scoped resolution refers to an absent invoice record/line")
    if set(line_candidate_scopes or {}) != seen_candidate_scopes:
        raise ValueError('Candidate scope refers to an absent invoice record/line')
    for days in patient_days.values():
        days.sort()
    rule_index = {}
    for group in ("threshold_premiums", "non_business_day_uplifts", "volume_discounts", "daily_caps"):
        rule_index[group] = defaultdict(list)
        for rule in contract[group]["rules"]:
            rule_index[group][rule["service_name"]].append(rule)
    # No arbitrary allocation of duplicate/capped charges into corrected totals.
    for (patient, service, d), group in day_lines.items():
        total = sum(r["raw"]["quantity"] for r in group if r['valid_qty'] and
                    (not improved_usage or r['units_reliable']))
        if len(group) > 1 and contract["invoice_requirements"]["duplicate_scope"] != "not_stated":
            cross_invoice = len({r["record"] for r in group}) > 1
            allocation = duplicate_allocation(group) if policy.get("allocate_later_duplicates") and cross_invoice else None
            for row in group:
                if allocation and allocation["status"] == "unresolved":
                    review(row["result"], "Duplicate-service invoice order/identity unresolved")
                    row["out"]["duplicate_allocation"] = allocation
                    continue
                if allocation:
                    row["out"]["duplicate_allocation"] = allocation
                    if row["record"] == allocation["first_record"]:
                        continue
                finding(row["result"], "cross_invoice_duplicate" if cross_invoice else "duplicate_service_same_day",
                        row["raw"].get("line_id"), allocation)
                review(row["result"], "Duplicate-service correction/allocation unresolved")
        for cap in rule_index["daily_caps"][service]:
            if total > cap["quantity"]:
                for row in group:
                    finding(row["result"], "daily_cap_exceeded", row["raw"].get("line_id"), {"aggregate_quantity": total, "cap": cap["quantity"]})
                    review(row["result"], "Excess-quantity correction/allocation unresolved")

    cumulative = Counter()
    uncertain_rows = [r for r in lines if not r["service"] or not r["date"] or not r["valid_qty"] or
                      (improved_usage and (not r['units_reliable'] or not r['invoice_identity_unique'])) or
                      (precise_history and not r['patient'])]
    def possible_services(unknown):
        if "possible_services" in unknown:
            return unknown["possible_services"]
        if unknown["service"]:
            return {unknown["service"]}
        match = unknown["out"]["match"]
        if dependency_mode and match.get("candidate_scope_complete_under_description_assumption"):
            return {x["service_name"] for x in match["candidates"]}
        return set(catalog)  # A top-k retrieval shortlist is never exhaustive.
    for unknown in uncertain_rows:
        unknown["possible_services"] = possible_services(unknown)
        if (improved_usage and contract.get('uncertainty_policy', {}).get('volume_family_bounds')
                and not unknown['service']
                and not unknown['out']['match'].get('candidate_scope_complete_under_description_assumption')):
            scope = history_family_scope(unknown['raw'].get('description', ''), contract['services'],
                                         contract.get('matching_guidance', {}).get('token_aliases', {}),
                                         single_word_families=contract.get('uncertainty_policy', {}).get('single_word_volume_families', False))
            if scope:
                unknown['volume_family_scope'] = scope
    historical_unit_estimates = historical_unit_estimates or {}
    history_lookup = {(r['record'], r['raw'].get('line_id')): r for r in uncertain_rows if r['valid_line_id']}
    for key, estimate in historical_unit_estimates.items():
        event = history_lookup.get(key)
        if (not event or not event['service'] or catalog[event['service']]['unit_basis'] is None or not event['valid_qty'] or not event['date']
                or event['units_reliable'] or not event['invoice_identity_unique']
                or estimate.get('service_name') != event['service']
                or type(estimate.get('quantity')) is not int or estimate['quantity'] != event['raw']['quantity']
                or len(set(estimate.get('reference_invoice_ids', []))) < 3
                or event['result']['invoice_id'] in estimate.get('reference_invoice_ids', [])
                or not estimate.get('assumption')):
            raise ValueError('Invalid conditional historical unit-label estimate')

    # Proven prior usage is independent of patient metadata. Build an index
    # instead of accumulating only those rows that happen to be priceable.
    # The contract's sort key is date + line ID, not input order; equal keys
    # are possible prior events, never guaranteed prior events.
    proven_keys, proven_prefix = {}, {}
    proven_days, proven_day_prefix = {}, {}
    unordered_days, unordered_prefix = {}, {}
    if improved_usage:
        proven, dated, unordered = defaultdict(list), defaultdict(list), defaultdict(list)
        for event in lines:
            if (event['service'] and event['date'] and term_start <= event['date'] <= term_end
                    and event['valid_qty'] and event['units_reliable'] and event['invoice_identity_unique']):
                dated[event['service']].append((event['date'], event['raw']['quantity']))
                if event['valid_line_id']:
                    proven[event['service']].append(((event['date'], event['raw']['line_id']), event['raw']['quantity']))
                else:
                    unordered[event['service']].append((event['date'], event['raw']['quantity']))
        for index, keys, prefixes in ((proven, proven_keys, proven_prefix),
                                      (dated, proven_days, proven_day_prefix),
                                      (unordered, unordered_days, unordered_prefix)):
            for name, events in index.items():
                events.sort()
                keys[name] = [key for key, _ in events]
                prefix = [0]
                for _, quantity in events:
                    prefix.append(prefix[-1] + quantity)
                prefixes[name] = prefix

    def quantity_upper(event, service):
        if not event['valid_qty'] or catalog[service]['unit_basis'] is None or event['raw'].get('unit_basis_as_billed') != catalog[service]['unit_basis']:
            return float('inf')
        return event['raw']['quantity']

    def possible_same_day(event, service, patient, d):
        return (event['patient'] in (None, patient) and event['date'] in (None, d)
                and service in possible_services(event))

    def present(patient, service, d):
        return bool(day_lines.get((patient, service, d)))

    def event_dependency_reasons(row):
        """Compare rule outcomes, not merely membership of a related family.

        A known event with unknown quantity still establishes presence. An
        optional extra event cannot change an already-present bundle partner
        or a threshold already exceeded by confirmed quantities.
        """
        name, patient, d = row['service'], row['patient'], row['date']
        reasons = []
        possible = [event for event in uncertain_rows if event is not row and
                    event['patient'] in (None, patient) and not (
                        policy.get('quarantine_invalid_history_dates') and event['date'] is None)]
        own_events = [event for event in possible if possible_same_day(event, name, patient, d)]
        if contract['invoice_requirements']['duplicate_scope'] != 'not_stated':
            if any(not (event['service'] == name and event['patient'] == patient and event['date'] == d and event['presence_known']) for event in own_events):
                reasons.append('An optional same-service event can change duplicate detection')
        daily_lower = sum(event['raw']['quantity'] for event in day_lines[(patient, name, d)]
                          if event['valid_qty'] and event['units_reliable'])
        daily_upper = daily_lower
        for event in own_events:
            daily_upper += quantity_upper(event, name)
        # The target's own uncertain units/quantity are a local problem, not
        # an independent historical event that could duplicate itself.
        if not row['units_reliable']:
            daily_upper = float('inf')
        for rule in rule_index['threshold_premiums'][name]:
            if (daily_lower > rule['quantity']) != (daily_upper > rule['quantity']):
                reasons.append('Daily threshold changes between quantity bounds')
        for rule in rule_index['daily_caps'][name]:
            if (daily_lower > rule['quantity']) != (daily_upper > rule['quantity']):
                reasons.append('Daily cap changes between quantity bounds')
        active_rates = set()
        possible_bundle_rates = []
        for rule in contract['bundles']['rules']:
            if name not in (rule['service_a'], rule['service_b']):
                continue
            other = rule['service_b'] if name == rule['service_a'] else rule['service_a']
            rate = rule['bundled_rate_a_cents'] if name == rule['service_a'] else rule['bundled_rate_b_cents']
            if present(patient, other, d):
                active_rates.add(rate)
            elif any(possible_same_day(event, other, patient, d) for event in possible):
                possible_bundle_rates.append(rate)
        # No identity is invented: an optional bundle is harmless only when
        # an already guaranteed bundle sets exactly the same substituted rate.
        if any(active_rates != {rate} for rate in possible_bundle_rates):
            reasons.append('An optional service can change bundle substitution')
        boundary = contract['exclusion_windows']['boundary_inclusive']
        for rule in contract['exclusion_windows']['rules']:
            if rule['excluded_service'] != name:
                continue
            related, window = rule['related_service'], rule['window_days']
            known_days = patient_days.get((patient, related), [])
            if any(abs(day - d.toordinal()) < window or (boundary is True and abs(day - d.toordinal()) == window) for day in known_days):
                continue  # The exclusion check below already has its evidence.
            for event in possible:
                if related not in possible_services(event):
                    continue
                distance = abs((event['date'] - d).days) if event['date'] else None
                if distance is None or distance < window or (distance == window and boundary is not False):
                    # Confirmed presence will be handled by the actual window
                    # check; only event identity/date/patient uncertainty adds
                    # an uncertainty about historical eligibility.
                    if not (event['service'] == related and event['patient'] == patient and event['date'] is not None and event['presence_known']):
                        reasons.append('An optional related event can change exclusion eligibility')
                        break
        return sorted(set(reasons))

    sort_counts = Counter((r["date"], r["raw"].get("line_id") if r['valid_line_id'] or not profile.current_contract_rules else None) for r in lines)
    for row in sorted(lines, key=lambda r: (r["date"] or date.max, str(r["raw"].get("line_id", "")), r["record"])):
        raw, out, result = row["raw"], row["out"], row["result"]
        service, d, patient = row["service"], row["date"], row["patient"]
        if not service or not d or not row["valid_qty"] or not patient:
            continue
        lid = raw.get("line_id")
        base = catalog[service]["base_rate_cents"]
        runtime = pricing_context(extended_contract, service, raw, invoices[row["record"]], base) if extended_contract else {}
        base = runtime.get("base_rate_cents", base)
        rate = base
        local_review = profile.current_contract_rules and catalog[service]["unit_basis"] is None
        if runtime.get("review"):
            review(result, runtime["review"])
            local_review = True
        if runtime.get("finding"):
            finding(result, runtime["finding"], lid, runtime)
            review(result, "Non-billable service correction unresolved")
            local_review = True
        non_discount_review = local_review or 'Unknown facility or plan tier' in result['review_reasons']
        if profile.current_contract_rules:
            out["pricing_context"] = runtime
        relevant_discount = rule_index["volume_discounts"][service]
        sort_count = sort_counts[(d, lid if row['valid_line_id'] or not profile.current_contract_rules else None)]
        discount_uncertain = global_unmapped or service in uncertain_services or sort_count > 1
        patient_uncertain = patient in uncertain_patients
        if bounded_history:
            # A missing service may belong to ANY service. Use a worst-case upper
            # utilisation bound, not a guessed shortlist. If both bounds select
            # the same discount, the pricing is invariant to the missing match.
            lower, upper = cumulative[service], cumulative[service]
            if improved_usage:
                if row['valid_line_id']:
                    key = (d, lid)
                    keys, prefix = proven_keys.get(service, []), proven_prefix.get(service, [0])
                    lower = prefix[bisect_left(keys, key)]
                    upper = prefix[bisect_right(keys, key)]
                    # An invalid historical identifier has no within-day
                    # position, but an earlier date still proves precedence.
                    days, day_prefix = unordered_days.get(service, []), unordered_prefix.get(service, [0])
                    lower += day_prefix[bisect_left(days, d)]
                    upper += day_prefix[bisect_right(days, d)]
                else:
                    days, day_prefix = proven_days.get(service, []), proven_day_prefix.get(service, [0])
                    lower = day_prefix[bisect_left(days, d)]
                    upper = day_prefix[bisect_right(days, d)]
                if term_start <= d <= term_end and row['units_reliable'] and row['invoice_identity_unique']:
                    upper -= raw['quantity']  # Never include the current line.
            contributors = []
            for unknown in uncertain_rows:
                if improved_usage and unknown is row:
                    continue
                if improved_usage and unknown['service'] and unknown['date'] and unknown['valid_qty'] and unknown['units_reliable'] and unknown['invoice_identity_unique']:
                    continue  # Already indexed even when patient metadata is missing.
                if service not in possible_services(unknown):
                    continue
                ud = unknown["date"]
                if improved_usage and ud is not None and not term_start <= ud <= term_end:
                    continue  # Known dates outside the term cannot earn usage.
                if improved_usage:
                    if ud and (ud > d or (ud == d and row['valid_line_id'] and unknown['valid_line_id'] and unknown['raw']['line_id'] > lid)):
                        continue
                elif ud and (ud, str(unknown["raw"].get("line_id", ""))) > (d, str(lid)):
                    continue
                family = unknown.get('volume_family_scope')
                if family and service not in family['candidate_services']:
                    if relevant_discount:
                        out.setdefault('history_family_assumptions', []).append({
                            'line_id': unknown['raw'].get('line_id'),
                            'record_index': unknown['record'], **family})
                    continue
                if relevant_discount:
                    contributors.append({"line_id": unknown["raw"].get("line_id"),
                                         "description": unknown["raw"].get("description"),
                                         "quantity": unknown["raw"].get("quantity"),
                                         "match_method": unknown["out"]["match"].get("method", "fuzzy_shortlist_not_exhaustive")})
                estimate = historical_unit_estimates.get((unknown['record'], unknown['raw'].get('line_id'))) if unknown['valid_line_id'] else None
                if estimate and unknown['service'] == service:
                    # Only discount history changes; the original wrong-unit
                    # finding, raw invoice and patient-history checks survive.
                    q = estimate['quantity']
                    upper += q
                    if ud < d or (ud == d and row['valid_line_id'] and unknown['valid_line_id']
                                  and unknown['raw']['line_id'] < lid):
                        lower += q
                    if relevant_discount:
                        out.setdefault('historical_unit_assumptions', []).append({
                            'record_index': unknown['record'], 'line_id': unknown['raw']['line_id'], **estimate})
                    continue
                if not unknown["valid_qty"] or (improved_usage and quantity_upper(unknown, service) == float('inf')):
                    upper = float("inf")
                    break
                upper += unknown["raw"]["quantity"]
            level = lambda units: max((r["discount_percent"] for r in relevant_discount if units > r["quantity"]), default=0)
            if improved_usage and not 0 <= lower <= upper:
                raise ValueError('Invalid prior usage bounds: expected 0 <= lower <= upper')
            discount_uncertain = level(lower) != level(upper) or (not improved_usage and sort_count > 1)
            if relevant_discount:
                out["utilisation_bounds"] = {"prior_units_lower": lower,
                    "prior_units_upper": upper if upper != float("inf") else None,
                    "upper_unbounded": upper == float("inf"),
                    "discount_percent_lower": level(lower), "discount_percent_upper": level(upper),
                    "uncertain_contributors": contributors,
                    "source_clauses": contract["volume_discounts"].get("source_clauses", [])}
            patient_uncertain = False
            dependencies = catalog[service].get("rule_dependencies") if dependency_mode else None
            if precise_history:
                out['history_dependency_reasons'] = event_dependency_reasons(row)
                patient_uncertain = bool(out['history_dependency_reasons'])
            for unknown in uncertain_rows:
                if policy.get("quarantine_invalid_history_dates") and unknown["date"] is None:
                    if unknown["patient"] in (None, patient):
                        out.setdefault("quarantined_history", []).append({
                            "line_id": unknown["raw"].get("line_id"),
                            "raw_service_date": unknown["raw"].get("service_date"),
                            "scope": "same-day and exclusion-window checks only; utilisation bounds retained",
                            "assumption": "Invalid dated evidence is excluded, not repaired or proven irrelevant."
                        })
                    continue
                if precise_history:
                    continue  # Detailed outcome checks above supersede broad taint.
                if unknown["patient"] not in (None, patient):
                    continue
                ud = unknown["date"]
                if dependencies:
                    possible = possible_services(unknown)
                    same_day_relevant = possible.intersection(dependencies["same_patient_same_day_services"])
                    if same_day_relevant and (ud is None or ud == d):
                        patient_uncertain = True
                    for exclusion in dependencies["exclusion_windows"]:
                        if exclusion["related_service"] in possible and (ud is None or abs((ud - d).days) <= exclusion["days"]):
                            patient_uncertain = True
                    continue
                # Unknown lines on other dates cannot change same-day rules.
                if ud is None or ud == d:
                    patient_uncertain = True
                    break
                for exclusion in contract["exclusion_windows"]["rules"]:
                    if exclusion["excluded_service"] == service and unknown["service"] in (None, exclusion["related_service"]) and abs((ud - d).days) <= exclusion["window_days"]:
                        patient_uncertain = True
        if relevant_discount and discount_uncertain:
            review(result, f"Cumulative usage uncertain for {service}: unresolved descriptions/dates/quantities/order")
            local_review = True
        if patient_uncertain:
            review(result, "Patient history contains unresolved services/dates/quantities; cross-line checks incomplete")
            local_review = True
            non_discount_review = True
        bundle_rates = []
        for rule in contract["bundles"]["rules"]:
            if service in (rule["service_a"], rule["service_b"]):
                other = rule["service_b"] if service == rule["service_a"] else rule["service_a"]
                if (patient, other, d) in day_lines:
                    bundle_rates.append(rule["bundled_rate_a_cents"] if service == rule["service_a"] else rule["bundled_rate_b_cents"])
                    for event in day_lines[(patient, other, d)]:
                        if event['out'].get('presence_assumption'):
                            out.setdefault('history_presence_assumptions', []).append({
                                'record_index': event['record'], 'line_id': event['raw'].get('line_id'),
                                'rule': 'bundle', 'assumption': event['out']['presence_assumption']})
        if len(set(bundle_rates)) > 1:
            review(result, f"Conflicting bundles on {lid}")
            local_review = True
            non_discount_review = True
        elif bundle_rates:
            rate = bundle_rates[0]
        out["calculation"].append({"step": "base_or_bundle", "rate_cents": rate})
        for key in ("facility_multiplier", "plan_tier_multiplier"):
            m = runtime.get(key, details[key])
            rate = rounded_ratio(rate, m["numerator"], m["denominator"])
            out["calculation"].append({"step": key, "rate_cents": rate})
        before_premium = rate
        qty_today = sum(r["raw"]["quantity"] for r in day_lines[(patient, service, d)] if r['valid_qty'] and
                        (not improved_usage or r['units_reliable']))
        uplifts = [r["uplift_percent"] for r in rule_index["threshold_premiums"][service] if qty_today > r["quantity"]]
        if d.strftime("%A") in contract["non_business_day_uplifts"]["eligible_days"]:
            uplifts += [r["uplift_percent"] for r in rule_index["non_business_day_uplifts"][service]]
        if len(uplifts) > 1:
            review(result, f"Multiple premium interaction unspecified on {lid}")
            local_review = True
            non_discount_review = True
        for uplift in uplifts:
            rate = rounded_ratio(rate, 100 + uplift, 100)
        after_premium = rate
        out["calculation"].append({"step": "premiums", "uplift_percents": uplifts, "rate_cents": rate})
        prior = lower if improved_usage and bounded_history else cumulative[service]
        discount = max((r["discount_percent"] for r in relevant_discount if prior > r["quantity"]), default=0)
        rate = rounded_ratio(rate, 100 - discount, 100)
        out["calculation"].append({"step": "volume_discount", "prior_units": prior, "discount_percent": discount, "rate_cents": rate})
        if not improved_usage or row['units_reliable']:
            cumulative[service] += raw["quantity"]
        for rule in contract["exclusion_windows"]["rules"]:
            if rule["excluded_service"] != service:
                continue
            days = patient_days.get((patient, rule["related_service"]), [])
            n, window = d.toordinal(), rule["window_days"]
            boundary = contract["exclusion_windows"]["boundary_inclusive"]
            interior = bisect_left(days, n + window) > bisect_right(days, n - window)
            at_edge = n - window in days or n + window in days
            if interior or (boundary is True and at_edge):
                for ordinal in set(days):
                    distance = abs(ordinal - n)
                    if distance < window or (boundary is True and distance == window):
                        for event in day_lines[(patient, rule['related_service'], date.fromordinal(ordinal))]:
                            if event['out'].get('presence_assumption'):
                                out.setdefault('history_presence_assumptions', []).append({
                                    'record_index': event['record'], 'line_id': event['raw'].get('line_id'),
                                    'rule': 'exclusion', 'assumption': event['out']['presence_assumption']})
                scope_status = contract["exclusion_windows"]["patient_scope"].get("status")
                if scope_status == "interpretation_requires_review":
                    review(result, f"Possible exclusion on {lid}; same-patient interpretation requires review")
                else:
                    finding(result, "exclusion_window_violation", lid, rule)
                    review(result, f"Excluded-charge correction unresolved on {lid}")
                local_review = True
                non_discount_review = True
            elif boundary is None and at_edge:
                review(result, f"Exact exclusion boundary unresolved on {lid}")
                local_review = True
                non_discount_review = True
        if (improved_usage and relevant_discount and discount_uncertain and not non_discount_review
                and row['units_reliable']):
            # A bounded uncertainty need not hide an independently provable
            # error. Retain every reachable discount step (not merely endpoint
            # rates); do not claim which step or corrected total is correct.
            levels = {level(lower), level(upper)}
            levels.update(level(rule['quantity'] + 1) for rule in relevant_discount
                          if lower <= rule['quantity'] < upper)
            possible_rates = sorted({rounded_ratio(after_premium, 100 - percent, 100) for percent in levels})
            out['possible_unit_prices_cents'] = possible_rates
            billed = raw.get('unit_price_cents')
            if integer(billed) and billed not in possible_rates:
                finding(result, 'unit_price_mismatch', lid, {
                    'billed_cents': billed, 'possible_expected_cents': possible_rates,
                    'basis': 'Billed price disagrees with every discount level permitted by prior-usage bounds; exact corrected price remains unresolved.'})
        if not local_review:
            out["expected_line_total_cents"] = rate * raw["quantity"]
            out["expected_unit_price_cents"] = rate
            billed = raw.get("unit_price_cents")
            if integer(billed) and billed != rate:
                finding(result, "unit_price_mismatch", lid, {"expected_cents": rate, "billed_cents": billed})
                if uplifts and billed == rounded_ratio(before_premium, 100 - discount, 100):
                    finding(result, "premium_omitted", lid)
                if discount and billed == after_premium:
                    finding(result, "volume_discount_omitted", lid)
                if not uplifts:
                    possibles = rule_index["threshold_premiums"][service] + rule_index["non_business_day_uplifts"][service]
                    if any(billed == rounded_ratio(rounded_ratio(before_premium, 100 + r["uplift_percent"], 100), 100 - discount, 100) for r in possibles):
                        finding(result, "premium_incorrectly_applied", lid)
                if any(r["discount_percent"] != discount and billed == rounded_ratio(after_premium, 100 - r["discount_percent"], 100) for r in relevant_discount):
                    finding(result, "volume_discount_incorrectly_applied", lid)
                if bundle_rates:
                    standalone = base
                    for key in ("facility_multiplier", "plan_tier_multiplier"):
                        m = runtime.get(key, details[key])
                        standalone = rounded_ratio(standalone, m["numerator"], m["denominator"])
                    for uplift in uplifts:
                        standalone = rounded_ratio(standalone, 100 + uplift, 100)
                    if billed == rounded_ratio(standalone, 100 - discount, 100):
                        finding(result, "bundle_not_applied", lid)
    for result in results:
        if profile.policy_evidence:
            result["audit_policy"] = policy.copy()
            result["policy_note"] = "Later-duplicate allocation and literal catalogue-conflict classification are documented assumptions, not new contract clauses. Invalid-date quarantine, when enabled, is conditional."
        categories = sorted({f["category"] for f in result["findings"]})
        complete = not result["review_reasons"] and bool(result["lines"]) and all(x["expected_line_total_cents"] is not None for x in result["lines"])
        result["flagged"] = 1 if categories else (0 if complete else None)
        result["error_category"] = "|".join(categories)
        result["expected_total_cents"] = sum(x["expected_line_total_cents"] for x in result["lines"]) if complete else None
        result["status"] = "assessed" if complete else "needs_review"
        inferred = any(x["match"].get("inferred") for x in result["lines"])
        if inferred:
            result["assessment_basis"] = "conditional_on_price_pattern_service_inference"
            if complete:
                result["status"] = "inferred_assessment"
        # Deliberately not presented as calibrated probabilities.
        result["confidence"] = None
        result["confidence_note"] = "Not calibrated; similarity scores are not correctness probabilities."
    return results


def audit_h1(contract, invoices, service_overrides=None, bounded_history=False,
             line_service_resolutions=None, *, policy=None, matcher_factory=Matcher):
    """Replay the frozen H1 policy without enabling newer contract semantics."""
    return audit(contract, invoices, service_overrides, bounded_history,
                 line_service_resolutions, policy=policy,
                 matcher_factory=matcher_factory, profile=H1_PROFILE)


def audit_baseline(contract, invoices, service_overrides=None, bounded_history=False,
                   line_service_resolutions=None, *, matcher_factory=Matcher):
    """Run the original conservative rules through the shared implementation."""
    return audit(contract, invoices, service_overrides, bounded_history,
                 line_service_resolutions, matcher_factory=matcher_factory,
                 profile=BASELINE_PROFILE)
