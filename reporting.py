"""Result exports, remaining-blocker feedback, and label-based evaluation.

Evaluation reads labels only after predictions exist; it cannot change decisions.
"""
import argparse
from collections import Counter, defaultdict
import csv
import gzip
import hashlib
import json
from pathlib import Path

from ai_client import save

ROOT = Path(__file__).resolve().parent



# Coverage and evaluation, independent of inference.


def metrics(results, label_path, all_invoices):
    with label_path.open() as f:
        labels = list(csv.DictReader(f))
    lc, ic = Counter(x["invoice_id"] for x in labels), Counter(x["invoice_id"] for x in all_invoices)
    lookup = {x["invoice_id"]: x for x in labels if lc[x["invoice_id"]] == 1}
    pairs = [(x, lookup[x["invoice_id"]]) for x in results if ic[x["invoice_id"]] == 1 and x["invoice_id"] in lookup]
    decided = [(x, y) for x, y in pairs if x["flagged"] is not None]
    correct = sum(str(x["flagged"]) == y["is_erroneous"] for x, y in decided)
    predicted_positive = sum(x["flagged"] == 1 for x, y in pairs)
    actual_positive = sum(y["is_erroneous"] == "1" for x, y in pairs)
    tp = sum(x["flagged"] == 1 and y["is_erroneous"] == "1" for x, y in pairs)
    ratio = lambda a, b: round(100*a/b, 2) if b else None
    totals = [(x, y) for x, y in pairs if x["expected_total_cents"] is not None]
    return {"records": len(results), "eligible": len(pairs), "excluded_duplicate_or_missing_ids": len(results)-len(pairs),
            "correct": correct, "incorrect": len(decided)-correct, "unanswered": len(pairs)-len(decided),
            "accuracy_on_answered_percent": ratio(correct, len(decided)), "coverage_percent": ratio(len(decided),len(pairs)),
            "correct_of_all_eligible_percent": ratio(correct,len(pairs)), "true_positives": tp,
            "false_positives": predicted_positive-tp, "error_recall_including_abstentions_percent": ratio(tp,actual_positive),
            "actual_erroneous": actual_positive, "actual_correct": len(pairs)-actual_positive,
            "totals_provided": len(totals), "exact_total_matches": sum(x["expected_total_cents"]==int(y["expected_total_cents"]) for x,y in totals),
            "evaluation": "development_set_pilot_not_held_out"}


def evaluate(results, label_path):
    """Evaluation only: ambiguous identifiers are excluded, never dict-overwritten."""
    with label_path.open(newline="", encoding="utf-8") as f:
        labels = list(csv.DictReader(f))
    rc, lc = Counter(r["invoice_id"] for r in results), Counter(l["invoice_id"] for l in labels)
    lookup = {l["invoice_id"]: l for l in labels if lc[l["invoice_id"]] == 1}
    matched = [(r, lookup[r["invoice_id"]]) for r in results if rc[r["invoice_id"]] == 1 and r["invoice_id"] in lookup]
    decided = [(r, l) for r, l in matched if r["flagged"] is not None]
    tp = sum(r["flagged"] == 1 and l["is_erroneous"] == "1" for r, l in decided)
    fp = sum(r["flagged"] == 1 and l["is_erroneous"] == "0" for r, l in decided)
    fn = sum(r["flagged"] == 0 and l["is_erroneous"] == "1" for r, l in decided)
    positives = sum(l["is_erroneous"] == "1" for _, l in matched)
    ratio = lambda a, b: a / b if b else None
    categories = sorted({c for _, l in matched for c in l["error_categories"].split("|") if c} | {c for r, _ in matched for c in r["error_category"].split("|") if c})
    per_category = {}
    for category in categories:
        true_count = sum(category in l["error_categories"].split("|") for _, l in matched)
        pred_count = sum(category in r["error_category"].split("|") for r, _ in matched)
        correct = sum(category in r["error_category"].split("|") and category in l["error_categories"].split("|") for r, l in matched)
        per_category[category] = {"label_count": true_count, "predicted_count": pred_count, "true_positives": correct,
                                  "precision": ratio(correct, pred_count), "recall_including_abstentions": ratio(correct, true_count)}
    totals = [(r, l) for r, l in matched if r["expected_total_cents"] is not None]
    return {"evaluation_role": "development_only_not_held_out", "matched_unique_records": len(matched),
            "excluded_prediction_records": len(results) - len(matched), "label_records": len(labels),
            "decided_records": len(decided), "abstained_records": len(matched) - len(decided),
            "decision_coverage": ratio(len(decided), len(matched)), "true_positives": tp, "false_positives": fp,
            "false_negatives_among_decided": fn, "precision": ratio(tp, tp + fp),
            "recall_including_abstentions": ratio(tp, positives), "per_category": per_category,
            "expected_total_coverage": ratio(len(totals), len(matched)),
            "expected_total_exact_accuracy_on_covered": ratio(sum(r["expected_total_cents"] == int(l["expected_total_cents"]) for r, l in totals), len(totals)),
            "ambiguity_sensitive_records": sum(l["ambiguity_sensitive"] == "1" for _, l in matched),
            "calibration": "Not measured: no probability model has been calibrated."}


def evaluation_details(results, label_path):
    """Reproducible H1 development metrics; labels never feed back into auditing."""
    report = evaluate(results, label_path)
    with label_path.open(newline='', encoding='utf-8') as stream:
        labels = list(csv.DictReader(stream))
    rc, lc = Counter(r['invoice_id'] for r in results), Counter(r['invoice_id'] for r in labels)
    lookup = {r['invoice_id']: r for r in labels if lc[r['invoice_id']] == 1}
    pairs = [(r, lookup[r['invoice_id']]) for r in results
             if rc[r['invoice_id']] == 1 and r['invoice_id'] in lookup]
    verdict_correct = lambda r, l: str(r['flagged']) == l['is_erroneous']
    category_correct = lambda r, l: set(filter(None, r['error_category'].split('|'))) == set(filter(None, l['error_categories'].split('|')))
    total_correct = lambda r, l: r['expected_total_cents'] == int(l['expected_total_cents'])
    report.update(
        true_negatives=sum(r['flagged'] == 0 and l['is_erroneous'] == '0' for r, l in pairs),
        verdict_correct=sum(verdict_correct(r, l) for r, l in pairs),
        exact_category_sets=sum(r['flagged'] is not None and category_correct(r, l) for r, l in pairs),
        provided_totals=sum(r['expected_total_cents'] is not None for r, _ in pairs),
        exact_totals=sum(total_correct(r, l) for r, l in pairs),
        missing_totals=sum(r['expected_total_cents'] is None for r, _ in pairs),
        incorrect_provided_totals=sum(r['expected_total_cents'] is not None and not total_correct(r, l) for r, l in pairs),
    )
    for item in report['per_category'].values():
        item['false_positives'] = item['predicted_count'] - item['true_positives']
        item['false_negatives'] = item['label_count'] - item['true_positives']

    # The exercise does not publish its confidence scorer. This explicitly defined
    # strict-row proxy is diagnostic only, not an independent calibration claim.
    bins = defaultdict(list)
    for r, label in pairs:
        value = r.get('confidence')
        if type(value) not in (int, float) or not 0 <= value <= 1:
            continue
        success = int(verdict_correct(r, label) and category_correct(r, label) and total_correct(r, label))
        bins[min(int(value * 10), 9)].append((value, success))
    scored = [x for bucket in bins.values() for x in bucket]
    report['confidence_diagnostic'] = {
        'role': 'development_only_not_independent_calibration',
        'target': 'Verdict, exact category set and non-missing corrected total all match H1 labels. Missing totals fail this completeness proxy; custom category synonyms are not normalised.',
        'count': len(scored),
        'brier_on_strict_complete_row_proxy': sum((p-y)**2 for p, y in scored)/len(scored) if scored else None,
        'bins': [{'lower': k/10, 'upper': (k+1)/10, 'count': len(values),
                  'mean_score': sum(p for p, _ in values)/len(values),
                  'observed_proxy_success': sum(y for _, y in values)/len(values)} for k, values in sorted(bins.items())],
        'limitation': 'Scores are declared evidence tiers, not calibrated probabilities. H1 already informed development; reference folds do not remove that exposure. H2-H5 correctness and calibration are unmeasured.',
    }
    report['calibration'] = 'No independent calibration; the explicit H1 development proxy below is diagnostic only.'
    return report


def load(path):
    """Load H1 comparison input from internal JSON or a supplied CSV."""
    records = defaultdict(list)
    with path.open(newline="", encoding="utf-8") as file:
        if path.suffix.lower() == '.json':
            data = json.load(file)
            rows = data.get('rows') if isinstance(data, dict) else data
            if not isinstance(rows, list) or any(not isinstance(row, dict) for row in rows):
                raise ValueError('Prediction JSON must contain a list of invoice records')
            rows = [{**row, 'flagged': 'not answered' if row.get('flagged') is None or row.get('record_identity_ambiguous')
                     else str(row['flagged'])} for row in rows]
        else:
            rows = list(csv.DictReader(file))
    for row in rows:
        if row.get('hospital_id') not in (None, '', 'H1'):
            continue
        records[row["invoice_id"]].append(row)
    return records


def compare(predictions, labels):
    counts = dict(correct=0, incorrect=0, unanswered=0, duplicate_ids=0,
                  missing_predictions=0, invalid_predictions=0)
    for invoice_id, actual_rows in labels.items():
        predicted_rows = predictions.get(invoice_id, [])
        # Duplicate identifiers cannot be safely paired by ID alone.
        if len(actual_rows) != 1 or len(predicted_rows) > 1:
            counts["duplicate_ids"] += 1
            continue
        if not predicted_rows:
            counts["missing_predictions"] += 1
            continue
        predicted = predicted_rows[0]["flagged"].strip()
        actual = actual_rows[0]["is_erroneous"].strip()
        if actual not in ("0", "1"):
            raise ValueError(f"Invalid label for {invoice_id}: {actual!r}")
        if predicted in ("", "not answered"):
            counts["unanswered"] += 1
        elif predicted not in ("0", "1"):
            counts["invalid_predictions"] += 1
        else:
            counts["correct" if predicted == actual else "incorrect"] += 1
    counts["predicted_ids_without_labels"] = len(predictions.keys() - labels.keys())
    return counts


def percentage(numerator, denominator):
    return f"{100 * numerator / denominator:.2f}%" if denominator else "N/A (no eligible invoices)"


def latest_predictions(root=ROOT):
    """Compare internal H1 results without requiring an extra prediction CSV."""
    completed = [folder for folder in (root / 'audit_output').glob('test_all_*')
                 if all((folder / name).is_file() for name in
                        ('summary.json', 'test_all_context.json', 'all_results.json'))]
    if not completed:
        raise FileNotFoundError('No completed audit export found. Run main.py or supply --predictions PATH.')
    latest = max(completed, key=lambda folder: (folder / 'test_all_context.json').stat().st_mtime_ns)
    return latest / 'all_results.json'


def compare_main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--predictions', type=Path, help='Use a specific all_results.json or H1 prediction CSV')
    args = parser.parse_args(argv)
    path = args.predictions or latest_predictions()
    predictions = load(path)
    labels = load(ROOT / "labels/hospital_1_labels.csv")
    counts = compare(predictions, labels)
    answered = counts["correct"] + counts["incorrect"]
    eligible = len(labels) - counts["duplicate_ids"]
    print("Hospital 1: erroneous/correct invoice classification")
    print(f"Predictions: {path}")
    print(f"Correct predictions:   {counts['correct']}")
    print(f"Incorrect predictions: {counts['incorrect']}")
    print(f"Unanswered:           {counts['unanswered']}")
    print(f"Accuracy on answered invoices: {percentage(counts['correct'], answered)}")
    print(f"Answer coverage: {answered}/{eligible} ({percentage(answered, eligible)})")
    print(f"Correct answers across eligible invoices: {percentage(counts['correct'], eligible)}")
    print(f"Excluded duplicate invoice IDs: {counts['duplicate_ids']}")
    print(f"Missing predictions: {counts['missing_predictions']}")
    print(f"Invalid predictions: {counts['invalid_predictions']}")
    print(f"Predicted IDs without labels: {counts['predicted_ids_without_labels']}")
    print("Unanswered predictions are not correct answers. Accuracy checks verdicts only, not amounts or categories.")


# Compact remaining reasons; categories overlap.


REASON_PREFIXES = (
    ('Unresolved service description', 'unclear_description'),
    ('Patient history', 'patient_history'),
    ('Cumulative usage', 'volume_discount'),
    ('Contract unit basis ambiguous', 'contract_unit'),
    ('Duplicate-service invoice order', 'duplicate_order'),
)


def reason_codes(reasons):
    codes = set()
    for reason in reasons:
        code = next((code for prefix, code in REASON_PREFIXES if reason.startswith(prefix)), None)
        if code is None:
            code = 'exclusion_boundary' if 'boundary' in reason.lower() else 'other_review_reason'
        codes.add(code)
    return sorted(codes) or ['unexplained_abstention']


def summarize_remaining(rows):
    unresolved = [r for r in rows if r['flagged'] is None and not r['record_identity_ambiguous']]
    overlapping, only, groups, by_hospital = Counter(), Counter(), Counter(), {}
    for row in unresolved:
        codes = reason_codes(row['review_reasons'])
        overlapping.update(codes)
        groups.update([' + '.join(codes)])
        if len(codes) == 1:
            only.update(codes)
        by_hospital.setdefault(row['hospital_id'], Counter()).update(codes)
    return {'eligible_unanswered': len(unresolved),
            'duplicate_id_records_excluded_separately': sum(bool(r['record_identity_ambiguous']) for r in rows),
            'overlapping_reason_counts': dict(overlapping), 'single_reason_counts': dict(only),
            'non_overlapping_reason_groups': dict(groups),
            'multiple_reasons': sum(len(reason_codes(r['review_reasons'])) > 1 for r in unresolved),
            'by_hospital_overlapping': {h: dict(counts) for h, counts in by_hospital.items()},
            'note': 'Reason counts overlap. Correctness is not measured for unlabelled hospitals.'}


# Complete review CSV and exact official submission schema.


def submission_confidence(row):
    """Use the evidence score computed before line traces were dropped."""
    if row.get('flagged') not in (0, 1) or row.get('record_identity_ambiguous'):
        return None
    value = row.get('confidence')
    if value is None:
        return None  # Older compact rows have insufficient evidence to rescore.
    if type(value) not in (int, float) or not 0 <= value <= 1:
        raise ValueError('Confidence must be between zero and one or absent')
    return value


def all_record_export(rows):
    """Include answered, unresolved, and duplicate-ID records in input order."""
    columns = [
        'hospital_id', 'record_index', 'invoice_id', 'status', 'flagged', 'error_category',
        'expected_total_cents', 'billed_total_cents', 'confidence', 'reason', 'assumptions',
        'verdict_confidence', 'total_confidence', 'category_confidence',
        'confidence_method', 'confidence_reasons',
    ]
    records = []
    for row in rows:
        answered = row['flagged'] is not None and not row['record_identity_ambiguous']
        records.append({
            'hospital_id': row['hospital_id'],
            'record_index': row['record_index'],
            'invoice_id': row['invoice_id'],
            'status': 'answered' if answered else 'not answered',
            'flagged': row['flagged'] if answered else 'not answered',
            'error_category': row['error_category'],
            'expected_total_cents': row['expected_total_cents'] if answered else None,
            'billed_total_cents': row['billed_total_cents'],
            'confidence': submission_confidence(row) if answered else None,
            'reason': ' | '.join(row['review_reasons']),
            'assumptions': row.get('assessment_basis') or '',
            'verdict_confidence': row.get('verdict_confidence') if answered else None,
            'total_confidence': row.get('total_confidence') if answered else None,
            'category_confidence': row.get('category_confidence') if answered else None,
            'confidence_method': row.get('confidence_method') or '',
            'confidence_reasons': ' | '.join(row.get('confidence_reasons') or []),
        })
    return {'columns': columns, 'rows': records}


def portable_csv_export(directory):
    """Write only the official template; review evidence stays in JSON."""
    submission = json.loads((directory / 'submission_data.json').read_text())
    with (ROOT / 'submission_template.csv').open(newline='', encoding='utf-8') as stream:
        columns = next(csv.reader(stream))
    if submission['columns'] != columns or any(set(row) != set(columns) for row in submission['rows']):
        raise ValueError('Submission must use exactly the supplied template columns, in order')
    with (directory / 'submission.csv').open('x', newline='', encoding='utf-8') as stream:
        writer = csv.DictWriter(stream, fieldnames=columns)
        writer.writeheader()
        writer.writerows(submission['rows'])


def export_results(output_dir):
    """Build the all-record export and remaining-blocker feedback."""
    rows = json.loads((output_dir / 'all_results.json').read_text())
    save(output_dir / 'submit_all_data.json', all_record_export(rows))
    remaining = summarize_remaining(rows)
    save(output_dir / 'remaining_blockers.json', remaining)
    h1 = [row for row in rows if row.get('hospital_id') == 'H1']
    if h1:
        labels = ROOT / 'labels/hospital_1_labels.csv'
        evaluation = evaluation_details(h1, labels)
        evaluation['source_sha256'] = {
            'all_results.json': hashlib.sha256((output_dir / 'all_results.json').read_bytes()).hexdigest(),
            'labels/hospital_1_labels.csv': hashlib.sha256(labels.read_bytes()).hexdigest(),
        }
        save(output_dir / 'evaluation.json', evaluation)
    return rows, remaining


def report_results(output_dir, rows, remaining):
    """Print the existing per-hospital and overall reconciliation."""
    summary = json.loads((output_dir / 'summary.json').read_text())
    print('\nFINAL COUNTS')
    for hospital in summary['hospitals']:
        print(
            f"{hospital['hospital_id']}: answered {hospital['answered']}, "
            f"not answered {hospital['unanswered']}, "
            f"duplicate-ID records {hospital['excluded_duplicate_id_records']}"
        )
    answered = sum(hospital['answered'] for hospital in summary['hospitals'])
    print(
        f"All {len(rows)} records included. Answered: {answered}. Not answered: {len(rows)-answered} "
        f"({summary['unanswered_unique_invoices']} eligible plus "
        f"{summary['excluded_duplicate_id_records']} duplicate-ID records)."
    )
    print('API budget:', json.dumps(summary['api_budget']))
    print('Remaining blocker counts (overlap):', json.dumps(remaining['overlapping_reason_counts']))
    print('Submission: submission.csv uses only the template columns; review evidence stays in JSON.')
    print('Confidence: uncalibrated evidence scores; explanations are in all_results.json.')
    print('Results:', output_dir.resolve())


# Optional detailed verification of a completed run.


def verify_main(argv=None):
    """Source, cache-reference, budget and output reconciliation checks."""
    from agents import assign_folds
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('output_dir', type=Path)
    parser.add_argument('--baseline', type=Path, default=ROOT / 'audit_output/h2_service_date_policy')
    args = parser.parse_args(argv)
    rows = json.loads((args.output_dir / 'all_results.json').read_text())
    old = json.loads((args.baseline / 'all_results.json').read_text())
    summary = json.loads((args.output_dir / 'summary.json').read_text())
    assert len(rows) == len(old) == 4886
    assert summary['quarantine_enabled'] and summary['h2_service_date_as_billing_day'] and summary['invoice_ai_enabled']
    assert summary['api_budget']['charged_or_reserved_usd'] <= summary['api_budget']['budget_usd'] <= 2
    for name, digest in summary['source_hashes'].items():
        path = Path(name)
        if not path.is_absolute(): path = ROOT / path
        assert hashlib.sha256(path.read_bytes()).hexdigest() == digest, name
    changes, reasons = {}, {}
    for h in range(1, 6):
        hospital = 'H' + str(h)
        raw = [json.loads(s) for s in (ROOT / f'invoices/hospital_{h}_invoices.jsonl').read_text().splitlines()]
        subset = [r for r in rows if r['hospital_id'] == hospital]
        prior = [r for r in old if r['hospital_id'] == hospital]
        counts = Counter(i['invoice_id'] for i in raw)
        assert len(subset) == len(raw)
        for i, (r, inv) in enumerate(zip(subset, raw)):
            assert r['record_index'] == i and r['invoice_id'] == inv['invoice_id']
            assert r['billed_total_cents'] == inv['invoice_total_cents']
            assert r['record_identity_ambiguous'] == (counts[inv['invoice_id']] > 1)
            assert r['agent_enabled'] is True
        pairs = [(a, b) for a, b in zip(prior, subset) if not b['record_identity_ambiguous']]
        changes[hospital] = {
            'newly_answered': sum(a['flagged'] is None and b['flagged'] is not None for a, b in pairs),
            'became_unanswered': sum(a['flagged'] is not None and b['flagged'] is None for a, b in pairs),
            'answered_verdict_changed': sum(a['flagged'] is not None and b['flagged'] is not None and a['flagged'] != b['flagged'] for a, b in pairs),
        }
        if h == 1:
            assert all(a[k] == b[k] for a, b in pairs for k in ('flagged','error_category','expected_total_cents','review_reasons'))
        reason_counts = Counter()
        for r in subset:
            if r['flagged'] is not None or r['record_identity_ambiguous']: continue
            codes = set()
            for reason in r['review_reasons']:
                if reason.startswith('Unresolved service description'): code = 'unclear_description'
                elif reason.startswith('Patient history'): code = 'uncertain_patient_history'
                elif reason.startswith('Cumulative usage'): code = 'uncertain_volume_discount'
                elif reason.startswith('Contract unit basis ambiguous'): code = 'ambiguous_contract_unit'
                elif 'boundary' in reason.lower(): code = 'exclusion_boundary'
                else: code = reason
                codes.add(code)
            reason_counts.update(codes)
        reasons[hospital] = dict(reason_counts)
        if h == 1: continue
        folds = assign_folds(raw)
        contract = json.loads((args.output_dir / f'hospital_{h}.runtime.json').read_text())
        names = {s['service_name'] for s in contract['services']}
        for heldout in range(5):
            with gzip.open(args.output_dir / f'{hospital}.fold_{heldout}.resolutions.jsonl.gz', 'rt') as f:
                for line in f:
                    p = json.loads(line)
                    assert p['service_name'] in names
                    if p['basis'] != 'price_pattern_inference': continue
                    forbidden = {inv['invoice_id'] for i, inv in enumerate(raw) if folds[i] in (heldout, folds[p['record_index']])}
                    refs = set(p['reference_invoice_ids'])
                    assert len(refs) >= 3 and not refs & forbidden
                    assert refs <= set(counts) and all(counts[x] == 1 for x in refs)
                    assert p['evidence_tier'] == 'standard_price_inference'
                    assert p['rate_support'][p['service_name']] >= .8
                    assert all(v <= .2 for n, v in p['rate_support'].items() if n != p['service_name'])
    submission = json.loads((args.output_dir / 'submission_data.json').read_text())['rows']
    expected = [r for r in rows if r['hospital_id'] != 'H1' and r['flagged'] is not None and not r['record_identity_ambiguous']]
    assert len(submission) == len(expected) == summary['submission_rows']
    assert {r['invoice_id'] for r in submission} == {r['invoice_id'] for r in expected}
    assert len({r['invoice_id'] for r in submission}) == len(submission)
    report = {'verified_raw_records': len(rows), 'comparison_to_pre_agent': changes,
              'unanswered_reasons_overlapping': reasons, 'source_files_unchanged': True,
              'reference_isolation_checked': True, 'standard_gate_checked': True,
              'budget_checked': summary['api_budget'],
              'accuracy_note': 'Only H1 has labels. Its previously developed result is a regression check, not an independent validation.'}
    save(args.output_dir / 'verification.json', report)
    print(json.dumps(report, indent=2))
