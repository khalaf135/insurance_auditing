"""Publish user-approved conditional hospital overlays; preserve other hospitals.

Run after both H2 experiment scripts. Keeps a recoverable root-submission
backup and separates the merged snapshot from the conservative main.py output.
"""
import copy
import argparse
import csv
import hashlib
import json
from collections import Counter
from pathlib import Path

from ai_client import save
from confidence import FIELDS
from reporting import submission_confidence, summarize_remaining

ROOT = Path(__file__).resolve().parent


def merge_rows(baseline, descriptions, units, hospital='H2'):
    merged = copy.deepcopy(baseline)
    lookup = {(r['hospital_id'], r['record_index'], r['invoice_id']): r for r in merged}
    touched = set()
    for kind, proposals in [('description_reference', descriptions), ('billed_quantity', units)]:
        for proposal in proposals:
            key = (hospital, proposal['record_index'], proposal['invoice_id'])
            row = lookup[key]
            if key in touched or row['flagged'] is not None or row.get('record_identity_ambiguous'):
                raise ValueError('Overlay must target a distinct unanswered unique hospital record')
            flagged = proposal.get('flagged') if kind == 'description_reference' else proposal.get('conditional_flagged')
            if flagged is None:
                continue
            if type(flagged) is not int or flagged not in (0, 1):
                raise ValueError('Invalid overlay verdict')
            touched.add(key)
            row['pre_overlay_review_reasons'] = row['review_reasons']
            row['conditional_answer'] = True
            row['conditional_answer_basis'] = kind
            row['flagged'] = flagged
            if kind == 'description_reference':
                row['expected_total_cents'] = proposal['expected_total_cents']
                row['error_category'] = proposal['error_category']
                row['review_reasons'] = proposal['review_reasons']
                for field in FIELDS:
                    row[field] = proposal[field]
                row['conditional_evidence'] = [l['match'].get('resolution_evidence') for l in proposal['lines']
                                               if l['match'].get('inferred')]
            else:
                # Do not auto-map experimental-only discrepancy categories into
                # the official schema. Such a case requires explicit review.
                if proposal.get('conditional_errors'):
                    raise ValueError('Conditional pricing discrepancy needs category review before publication')
                row['expected_total_cents'] = proposal['conditional_expected_total_cents']
                row['error_category'] = ''
                row['quantity_verified'] = False
                row['conditional_evidence'] = proposal
                # Previously unanswered rows may have no submission score.
                # Use an explicit provisional cap, not a calibrated probability.
                for field in ('confidence', 'verdict_confidence', 'total_confidence', 'category_confidence'):
                    value = row.get(field)
                    row[field] = min(value, .5) if isinstance(value, (int, float)) else .5
                row['confidence_reasons'] = row['confidence_reasons'] + [proposal['assumption']]
                row['confidence_reasons'].append('Conditional billed-quantity opinion capped at 0.50; uncalibrated policy score.')
            row['assessment_basis'] += '; User-approved conditional overlay: ' + kind
    return merged


def run():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--hospital', choices=['H2', 'H3', 'H4', 'H5'], default='H2')
    parser.add_argument('--history', action='store_true', help='Merge the separate H3 historical-quantity trial')
    args = parser.parse_args()
    hospital = args.hospital
    if args.history and hospital not in {'H3', 'H5'}:
        raise ValueError('History overlay currently reviewed for H3 and H5 only')
    base = ROOT / ('audit_output/h3_family_trial' if hospital == 'H3' else 'audit_output/h2_history_prefix_trial')
    folders = ([ROOT / 'audit_output/h3_billed_quantity_experiment'] if hospital == 'H3' else
               [ROOT / 'audit_output/h2_independent_reference_experiment', ROOT / 'audit_output/h2_billed_quantity_experiment'])
    if hospital == 'H4':
        base = ROOT / 'audit_output/h4_family_trial'
        folders = [ROOT / 'audit_output/h4_billed_quantity_experiment']
    if hospital == 'H5':
        base = ROOT / 'audit_output/h5_family_trial'
        folders = [ROOT / 'audit_output/h5_billed_quantity_experiment']
    if args.history:
        base = ROOT / ('audit_output/' + hospital.lower() + '_merged_conditional')
        folders = [ROOT / ('audit_output/' + hospital.lower() + suffix) for suffix in
                   ('_history_quantity_experiment', '_history_quantity_units')]
    for folder in folders:
        for relative, digest in json.loads((folder / 'provenance.json').read_text()).items():
            path = (ROOT / relative).resolve()
            if not path.is_relative_to(ROOT) or hashlib.sha256(path.read_bytes()).hexdigest() != digest:
                raise ValueError('Experiment source changed: ' + relative)
    baseline = json.loads((base / 'all_results.json').read_text())
    preserved_summary = None
    if hospital in {'H4', 'H5'}:
        prior_folder = ROOT / ('audit_output/h4_merged_conditional' if hospital == 'H5' else 'audit_output/h3_history_merged_conditional')
        prior = json.loads((prior_folder / 'all_results.json').read_text())
        preserved = {(r['hospital_id'], r['record_index'], r['invoice_id']): r for r in prior if r['hospital_id'] != hospital}
        baseline = [preserved[(r['hospital_id'], r['record_index'], r['invoice_id'])] if r['hospital_id'] != hospital else r for r in baseline]
        preserved_summary = {h['hospital_id']: h for h in json.loads((prior_folder / 'summary.json').read_text())['hospitals']}
    if hospital == 'H3' and not args.history:
        prior = json.loads((ROOT / 'audit_output/h2_merged_conditional/all_results.json').read_text())
        preserved = {(r['hospital_id'], r['record_index'], r['invoice_id']): r for r in prior if r['hospital_id'] == 'H2'}
        baseline = [preserved[(r['hospital_id'], r['record_index'], r['invoice_id'])] if r['hospital_id'] == 'H2' else r for r in baseline]
    proposals = [json.loads((folder / 'results.json').read_text()) for folder in folders]
    if hospital in {'H3', 'H4', 'H5'} and not args.history:
        proposals.insert(0, [])
    if args.history:
        eligible = {r['record_index'] for r in baseline if r['hospital_id'] == hospital and r['flagged'] is None and not r['record_identity_ambiguous']}
        resolved = {r['record_index'] for r in proposals[0] if r['flagged'] is not None}
        proposals[1] = [r for r in proposals[1] if r['record_index'] in eligible - resolved]
    rows = merge_rows(baseline, *proposals, hospital=hospital)
    if args.history:
        for row in rows:
            if row['hospital_id'] == hospital and row['record_index'] in resolved:
                row['conditional_answer_basis'] = 'historical_quantity_reference'
                row['assessment_basis'] = row['assessment_basis'].replace('User-approved conditional overlay: description_reference', 'User-approved conditional overlay: historical_quantity_reference')
                detail = next(r for r in proposals[0] if r['record_index'] == row['record_index'])
                row['conditional_evidence'] = [assumption for line in detail['lines'] for key in ('historical_unit_assumptions', 'history_family_assumptions') for assumption in line.get(key, [])]
    assert len(rows) == len(baseline) == 4886
    for before, after in zip(baseline, rows):
        if before['hospital_id'] != hospital or before['flagged'] is not None:
            assert before == after
    out = ROOT / ('audit_output/' + hospital.lower() + ('_history' if args.history else '') + '_merged_conditional')
    if out.exists() and not (out / 'merge_verification.json').exists():
        raise ValueError('Refusing to overwrite an unrelated output directory')
    out.mkdir(exist_ok=True)
    summary = copy.deepcopy(json.loads((base / 'summary.json').read_text()))
    if preserved_summary:
        summary['hospitals'] = [h if h['hospital_id'] == hospital else copy.deepcopy(preserved_summary[h['hospital_id']]) for h in summary['hospitals']]
    for h in summary['hospitals']:
        eligible = [r for r in rows if r['hospital_id'] == h['hospital_id'] and not r['record_identity_ambiguous']]
        h.update(answered=sum(r['flagged'] is not None for r in eligible),
                 unanswered=sum(r['flagged'] is None for r in eligible),
                 flagged=sum(r['flagged'] == 1 for r in eligible),
                 predicted_correct=sum(r['flagged'] == 0 for r in eligible),
                 totals_provided=sum(r['expected_total_cents'] is not None for r in eligible))
        if h['hospital_id'] in {'H2', hospital}:
            added = sum(r.get('conditional_answer', False) for r in eligible)
            h['method'] += f'; {added} user-approved conditional experiment overlays'
            h['unanswered_reasons'] = dict(Counter(s for r in eligible if r['flagged'] is None for s in r['review_reasons']))
    header = next(csv.reader((ROOT / 'submission_template.csv').open()))
    submission = [{k: submission_confidence(r) if k == 'confidence' else r[k] for k in header}
                  for r in rows if r['hospital_id'] != 'H1' and r['flagged'] is not None and not r['record_identity_ambiguous']]
    assert len({r['invoice_id'] for r in submission}) == len(submission)
    assert all(isinstance(r['confidence'], (int, float)) and 0 <= r['confidence'] <= 1 for r in submission)
    summary.update(submission_rows=len(submission),
                   unanswered_unique_invoices=sum(h['unanswered'] for h in summary['hospitals']),
                   conditional_overlay_count=sum(r.get('conditional_answer', False) for r in rows),
                   baseline=str(base.relative_to(ROOT)), new_invoice_api_calls=0)
    save(out / 'all_results.json', rows)
    save(out / 'summary.json', summary)
    save(out / 'remaining_blockers.json', summarize_remaining(rows))
    save(out / 'submission_data.json', {'columns': header, 'rows': submission})
    with (out / 'submission.csv').open('w', newline='') as stream:
        writer = csv.DictWriter(stream, fieldnames=header, lineterminator='\n')
        writer.writeheader()
        writer.writerows(submission)
    backup = out / 'prior_root_submission.csv'
    if not backup.exists():
        backup.write_bytes((ROOT / 'submission.csv').read_bytes())
    old = backup.read_bytes()
    save(out / 'merge_verification.json', {'raw_records': len(rows), 'conditional_overlays': summary['conditional_overlay_count'],
        'other_hospitals_exactly_unchanged': True, 'existing_answers_unchanged': True,
        'prior_root_submission_sha256': hashlib.sha256(old).hexdigest(),
        'experiment_results_sha256': {str(f.relative_to(ROOT)): hashlib.sha256((f / 'results.json').read_bytes()).hexdigest() for f in folders}})
    (ROOT / 'submission.csv').write_bytes((out / 'submission.csv').read_bytes())
    for h in summary['hospitals']:
        print(h['hospital_id'], 'answered', h['answered'], 'unanswered', h['unanswered'], 'duplicates', h['excluded_duplicate_id_records'])
    print('Updated root submission; previous copy saved in', out)


if __name__ == '__main__':
    run()
