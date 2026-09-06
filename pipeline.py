"""The hospital-isolated invoice pipeline.

Load each hospital's JSON, review source-unit uncertainty, validate any fallback
service proposals, run deterministic checks, and retain every raw invoice.
"""
import argparse
from collections import Counter
import csv
import gzip
import hashlib
import json
from pathlib import Path
import sys

sys.dont_write_bytecode = True

import rules as engine
import matching
from contracts import compile_contract, load_and_preflight, review_units, refine_dependencies
from agents import audit_with_agent, replay_h1
from ai_client import BudgetedAI, load_keys, save
from confidence import FIELDS as CONFIDENCE_FIELDS, METHOD as CONFIDENCE_METHOD, score_invoice
from reporting import metrics, submission_confidence, submission_rows, portable_csv_export, clarification_packets

ROOT = Path(__file__).resolve().parent



POLICY = {"allocate_later_duplicates": True, "classify_unsupported_services": True, "quarantine_invalid_history_dates": True}


H1_BASELINE_INPUTS = (
    'structured_contracts/hospital_1.json',
    'invoices/hospital_1_invoices.jsonl',
    'config/fallback_v2_policy.json',
    'audit_output/all_h1_standard_v2/results.jsonl',
)


def verify_h1_baseline_inputs(recorded):
    """Verify the frozen inputs in THIS checkout, preserving historical hashes.

    Old provenance stores absolute capture-time paths. Match only the four
    expected repository-relative suffixes; never read another checkout or
    relax content verification just because the repository moved.
    """
    resolved = {}
    for source, digest in recorded.items():
        matches = [name for name in H1_BASELINE_INPUTS
                   if source == name or source.endswith('/' + name)]
        if len(matches) != 1 or matches[0] in resolved:
            raise ValueError('Unexpected or repeated H1 baseline input')
        resolved[matches[0]] = digest
    if set(resolved) != set(H1_BASELINE_INPUTS):
        raise ValueError('Missing H1 baseline inputs')
    for name, digest in resolved.items():
        path = (ROOT / name).resolve()
        if not path.is_relative_to(ROOT.resolve()):
            raise ValueError('H1 baseline input escapes this checkout')
        if hashlib.sha256(path.read_bytes()).hexdigest() != digest:
            raise ValueError('H1 baseline inputs changed')


def basic_audit(draft, invoices, aliases, blocker):
    """Independently decidable errors survive unsupported pricing semantics."""
    guidance = {"token_aliases": aliases, "reviewed_descriptions": []}
    matcher = matching.Matcher(draft["services"], guidance=guidance)
    catalog = {s["service_name"]: s for s in draft["services"]}
    results = []
    for index, inv in enumerate(invoices):
        findings = []
        def flag(category, line_id=None, evidence=None):
            findings.append({"category": category, "line_id": line_id, "evidence": evidence})
        details = draft["contract_details"]
        if details.get("contract_number") and inv.get("contract_number") != details["contract_number"]:
            flag("contract_number_mismatch")
        lines = inv.get("line_items", [])
        if lines and all(matching.integer(l.get("line_total_cents")) for l in lines) and matching.integer(inv.get("invoice_total_cents")):
            if sum(l["line_total_cents"] for l in lines) != inv["invoice_total_cents"]:
                flag("invoice_total_mismatch")
        for line in lines:
            q, price, total = (line.get(k) for k in ("quantity", "unit_price_cents", "line_total_cents"))
            lid = line.get("line_id")
            if all(matching.integer(v) for v in (q, price, total)) and q*price != total:
                flag("line_total_arithmetic", lid)
            day, issued = matching.parse_date(line.get("service_date")), matching.parse_date(inv.get("invoice_date"))
            if day is None:
                flag("malformed_service_date", lid)
            else:
                start, end = matching.parse_date(details.get("effective_from")), matching.parse_date(details.get("effective_to"))
                if start and end and not start <= day <= end:
                    flag("service_date_out_of_window", lid)
                if issued and day > issued:
                    flag("service_date_after_invoice_date", lid)
            match = matcher.match(line.get("description", ""))
            # Fail closed for fuzzy or incomplete identity, even for unit errors.
            if match.get("service_name") and match.get("method") == "full_catalogue_token_constraints":
                unit = catalog[match["service_name"]].get("unit_basis")
                if unit in {"per_hour", "per_day", "per_night", "per_item", "per_visit", "per_test", "per_procedure", "per_unit_dispensed"} and unit != line.get("unit_basis_as_billed"):
                    flag("wrong_unit_basis", lid, {"expected": unit})
        categories = sorted({f["category"] for f in findings})
        results.append({"record_index": index, "invoice_id": inv["invoice_id"], "flagged": 1 if findings else None,
            "findings": findings, "error_category": "|".join(categories), "expected_total_cents": None,
            "billed_total_cents": inv["invoice_total_cents"], "confidence": None, "status": "needs_review",
            "review_reasons": [blocker], "audit_policy": POLICY,
            "assessment_basis": "Only independent data/contract-identity/unit checks; pricing not assessed"})
    return results


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--contracts-dir", type=Path, default=ROOT / "contract_agent_final_v4")
    parser.add_argument("--output-dir", type=Path, default=ROOT / "audit_output/all_hospitals_quarantine")
    parser.add_argument("--export-csv", action="store_true", help="Export only submission.csv in the exact supplied template format")
    parser.add_argument("--h2-service-date-as-billing-day", action="store_true",
                        help="Opt-in conditional H2 pricing using unchanged service_date as billing-day label; no inferred times")
    parser.add_argument("--invoice-ai", action="store_true", help="Run hospital-isolated description agents and full cross-fitted history audits")
    parser.add_argument("--four-blocker-fixes", action="store_true", help="Source-clause unit review, safer abbreviation validation and precise dependency/usage bounds")
    parser.add_argument("--budget-usd", type=float, default=2, help="Shared run safeguard, positive and at most USD 2")
    parser.add_argument("--offline-ai", action="store_true", help="Use cached AI responses only")
    parser.add_argument("--ai-cache-dir", type=Path, help="Reuse a prior run's persistent budget ledger and API cache")
    parser.add_argument("--unit-clarifications", type=Path, help="Optional source-cited unit clarification manifest; preflight checked before any paid call")
    parser.add_argument("--agent-replay-dir", type=Path, help="Verified prior run for revalidating unchanged task answers; no stored verdicts are reused")
    args = parser.parse_args(argv)
    if not 0 < args.budget_usd <= 2:
        parser.error("Budget must be positive and no more than USD 2")
    if args.four_blocker_fixes and not args.invoice_ai:
        parser.error("--four-blocker-fixes requires --invoice-ai")
    if args.unit_clarifications and not args.four_blocker_fixes:
        parser.error("--unit-clarifications requires --four-blocker-fixes")
    if args.agent_replay_dir and not args.four_blocker_fixes:
        parser.error("--agent-replay-dir requires --four-blocker-fixes")
    if args.agent_replay_dir and not args.agent_replay_dir.is_dir():
        parser.error("--agent-replay-dir must name an existing prior result directory")
    clarifications = load_and_preflight(args.unit_clarifications, args.contracts_dir)
    out = args.output_dir.resolve()
    if out.exists() and any(out.iterdir()):
        parser.error("Use a fresh output directory")
    out.mkdir(parents=True, exist_ok=True)
    api = None
    initial_api_calls = 0
    if args.invoice_ai:
        api = BudgetedAI(args.ai_cache_dir or out / "ai", budget=args.budget_usd,
                        offline=args.offline_ai, key=None if args.offline_ai else load_keys()["openrouter_api_key"])
        initial_api_calls = len(api.ledger)
    # Linguistic expansions only: no H1 rates, candidate mappings or examples.
    aliases = json.loads((ROOT / "config/hospital_1_matching.json").read_text())["token_aliases"]
    aliases.update(json.loads((ROOT / "config/fallback_v2_policy.json").read_text())["token_aliases"])
    summary, all_rows, submission, unanswered = [], [], [], []
    hashes = {}
    if args.unit_clarifications:
        hashes[str(args.unit_clarifications.resolve())] = hashlib.sha256(args.unit_clarifications.read_bytes()).hexdigest()
    for path in sorted((ROOT / "invoices").glob("hospital_*_invoices.jsonl")):
        name = path.stem.replace("_invoices", "")
        hospital = "H" + name.split("_")[1]
        raw = [json.loads(s) for s in path.read_text().splitlines()]
        draft_path = args.contracts_dir / (name + ".json")
        draft = json.loads(draft_path.read_text())
        if draft["hospital_id"] != hospital or any(i["hospital_id"] != hospital for i in raw):
            raise ValueError("Hospital isolation check failed")
        hashes[str(path.relative_to(ROOT))] = hashlib.sha256(path.read_bytes()).hexdigest()
        hashes[str(draft_path.resolve())] = hashlib.sha256(draft_path.read_bytes()).hexdigest()
        compiled, blocker = None, None
        if hospital == "H1":
            prior_dir = ROOT / "audit_output/all_h1_policy_v3_verified"
            prior = prior_dir / "with_date_quarantine.jsonl"
            context = json.loads((prior_dir / "context.json").read_text())
            if hashlib.sha256(prior.read_bytes()).hexdigest() != context["predictions_sha256"]["with_date_quarantine"]:
                raise ValueError("H1 verified results changed")
            verify_h1_baseline_inputs(context["baseline_context"]["source_inputs"])
            rows = [json.loads(s) for s in prior.read_text().splitlines()]
            if [r["invoice_id"] for r in rows] != [r["invoice_id"] for r in raw]:
                raise ValueError("H1 result alignment changed")
            method = "Preserved, hash-verified H1 pipeline with cached cross-fitted mappings and quarantine"
            if api:
                recalculated = replay_h1(ROOT, raw, POLICY)
                for old_row, new_row in zip(rows, recalculated):
                    if any(old_row[k] != new_row[k] for k in ("invoice_id", "flagged", "error_category", "expected_total_cents", "review_reasons")):
                        raise ValueError("H1 cached-agent replay regression")
                rows = recalculated
                method = "Full H1 re-audit using its frozen AI/cross-fitted evidence; no new H1 API calls needed"
        else:
            try:
                if args.four_blocker_fixes:
                    draft = review_units(draft, api, out, clarifications=clarifications)
                compiled = compile_contract(draft, aliases,
                    service_date_as_billing_day=hospital == "H2" and args.h2_service_date_as_billing_day)
                if args.four_blocker_fixes:
                    compiled = refine_dependencies(compiled)
                    compiled['uncertainty_policy']['improved_usage_bounds'] = True
                    # Source-reviewed service-specific volume bounds.
                    if hospital in {'H2', 'H3', 'H4', 'H5'}:
                        compiled['uncertainty_policy']['volume_family_bounds'] = True
                    if hospital == 'H4':
                        compiled['uncertainty_policy']['single_word_volume_families'] = True
            except (ValueError, KeyError, TypeError) as error:
                blocker = str(error)
                rows = basic_audit(draft, raw, aliases, blocker)
                method = "Partial independent checks only; contract pricing unsupported"
            else:
                # Execution defects must surface instead of silently replacing
                # the full hospital with a basic-only fallback and a success file.
                rows = (audit_with_agent(compiled, draft, raw, api, out, POLICY, improved=args.four_blocker_fixes,
                                         replay_dir=args.agent_replay_dir) if api else
                        engine.audit(compiled, raw, policy=POLICY, extended_contract=draft))
                save(out / (name + ".runtime.json"), compiled)
                method = "Source-checked contract, deterministic matching and extended pricing; no paid invoice-agent fallback"
                if api:
                    method = "Hospital-isolated AI description fallback, validated proposals, deterministic full-history five-fold audit"
        counts = Counter(i["invoice_id"] for i in raw)
        duplicates = sum(counts[r["invoice_id"]] > 1 for r in raw)
        for row in rows:
            if compiled and compiled.get("uncertainty_policy", {}).get("service_day_assumption"):
                row["service_day_assumption"] = compiled["uncertainty_policy"]["service_day_assumption"]
                row["assessment_basis"] = (row.get("assessment_basis", "") +
                    "; Conditional pricing/data audit using service_date as billing-day label; not full contractual compliance").lstrip("; ")
                row["date_order_check_basis"] = compiled["uncertainty_policy"]["date_order_check_basis"]
            row["hospital_id"] = hospital
            row["record_identity_ambiguous"] = counts[row["invoice_id"]] > 1
            if row["record_identity_ambiguous"]:
                row["review_reasons"] = list(row["review_reasons"]) + ["Duplicate invoice ID: cannot assign one submission row to this raw record"]
            # Score the rich evidence before the compact export drops line traces.
            # This annotates an existing opinion; it never changes that opinion.
            row.update(score_invoice(row))
            if row["flagged"] is None or row["record_identity_ambiguous"]:
                unanswered.append({"hospital_id": hospital, "record_index": row["record_index"],
                    "invoice_id": row["invoice_id"], "reason": " | ".join(row["review_reasons"])})
            elif hospital != "H1":
                submission.append({"invoice_id": row["invoice_id"], "flagged": row["flagged"],
                    "error_category": row["error_category"], "expected_total_cents": row["expected_total_cents"],
                    "billed_total_cents": row["billed_total_cents"], "confidence": submission_confidence(row)})
            all_rows.append({k: row.get(k) for k in ("hospital_id", "record_index", "invoice_id", "flagged", "error_category",
                "expected_total_cents", "billed_total_cents", "review_reasons", "record_identity_ambiguous", "assessment_basis",
                "service_day_assumption", "date_order_check_basis", "agent_enabled", "reference_fold") + CONFIDENCE_FIELDS})
        if compiled:
            save(out / (hospital + '.clarification_packets.json'), clarification_packets(rows, compiled))
        with gzip.open(out / (name + ".details.jsonl.gz"), "wt", encoding="utf-8") as f:
            for row in rows:
                f.write(json.dumps(row, ensure_ascii=False) + "\n")
        eligible = [r for r in rows if not r["record_identity_ambiguous"]]
        report = {"hospital_id": hospital, "raw_records": len(raw), "eligible_unique_records": len(eligible),
                  "excluded_duplicate_id_records": duplicates, "answered": sum(r["flagged"] is not None for r in eligible),
                  "unanswered": sum(r["flagged"] is None for r in eligible),
                  "flagged": sum(r["flagged"] == 1 for r in eligible),
                  "predicted_correct": sum(r["flagged"] == 0 for r in eligible),
                  "totals_provided": sum(r["expected_total_cents"] is not None for r in eligible),
                  "method": method, "blocker": blocker,
                  "service_day_assumption": compiled.get("uncertainty_policy", {}).get("service_day_assumption") if compiled else None,
                  "unanswered_reasons": dict(Counter(reason for r in eligible if r["flagged"] is None for reason in r["review_reasons"]))}
        if hospital == "H1":
            report["label_evaluation"] = metrics(rows, ROOT / "labels/hospital_1_labels.csv", raw)
        summary.append(report)
        print(json.dumps({k: v for k, v in report.items() if k not in ("unanswered_reasons", "label_evaluation")}), flush=True)
    with (ROOT / "submission_template.csv").open() as f:
        header = next(csv.reader(f))
    submission = submission_rows(all_rows, header, root=ROOT)
    save(out / "submission_data.json", {"columns": header, "rows": submission})
    save(out / "unanswered.json", unanswered)
    save(out / "all_results.json", all_rows)
    save(out / "summary.json", {"hospitals": summary, "submission_rows": len(submission),
        "unanswered_unique_invoices": sum(s["unanswered"] for s in summary),
        "excluded_duplicate_id_records": sum(s["excluded_duplicate_id_records"] for s in summary),
        "quarantine_enabled": True, "invoice_api_calls": len(api.ledger) if api else 0,
        "new_invoice_api_calls": len(api.ledger) - initial_api_calls if api else 0,
        "invoice_ai_enabled": args.invoice_ai, "api_budget": api.summary() if api else None,
        "four_blocker_fixes": args.four_blocker_fixes,
        "policy_revision": "four_blockers_v2" if args.four_blocker_fixes else "preserved_baseline",
        "h2_service_date_as_billing_day": args.h2_service_date_as_billing_day,
        "accuracy_note": "Only H1 has labels; no correctness claim for H2–H5.", "source_hashes": hashes,
        "confidence_method": CONFIDENCE_METHOD,
        "confidence_calibration": "Not calibrated. H1 was used for development; no independent probability validation is available.",
        "assumptions": ["Same-patient exclusion interpretation; exact boundary stays uncertain.",
            "Cumulative usage across the contract, ordered by service date then line ID.",
            "H5 invoice-header facility used when line facility is absent.",
            "H3 amended rates apply by service date; earlier settlement cases remain review.",
            "Quarantine invalid-date history only for dated patient-history checks; uncertain volume units retained.",
            "Confidence values are explained evidence scores, not calibrated probabilities; unresolved totals cap row confidence."]})
    if args.export_csv:
        portable_csv_export(out)
    if api:
        save(out / "cost.json", api.summary())
        save(out / "implementation_hashes.json", {str(p.relative_to(ROOT)): hashlib.sha256(p.read_bytes()).hexdigest()
            for p in (Path(__file__), ROOT / "agents.py", ROOT / "ai_client.py",
                      ROOT / "contracts.py", ROOT / "rules.py", ROOT / "matching.py", ROOT / "confidence.py")})
        api.close()


if __name__ == '__main__':
    main()
