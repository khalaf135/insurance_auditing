"""Recompute conditional improvements from this run; publish only after success."""
import copy
import csv
import hashlib
import json
import os
from pathlib import Path
import tempfile
from collections import Counter

from ai_client import save
from merge_h2_results import merge_rows
import reference_experiment
import history_quantity_experiment
import unit_quantity_experiment
from reporting import submission_rows, portable_csv_export

ROOT = Path(__file__).resolve().parent


def eligible_units(rows, proposals, hospital):
    eligible = {r['record_index'] for r in rows if r['hospital_id'] == hospital
                and r['flagged'] is None and not r['record_identity_ambiguous']}
    # Keep discrepant experimental calculations for review, not auto-publication.
    return [p for p in proposals if p['record_index'] in eligible
            and not p.get('conditional_errors')]


def run(base):
    base = Path(base).resolve()
    out = base / 'final'
    out.mkdir()  # A completed run must never silently be overwritten.
    steps = base / 'improvements'
    steps.mkdir()
    rows = json.loads((base / 'all_results.json').read_text())
    original = copy.deepcopy(rows)
    for hospital in ('H2', 'H3', 'H4', 'H5'):
        print(f'{hospital}: recomputing conditional improvements', flush=True)
        if hospital == 'H2':
            folder = steps / 'H2_descriptions'
            reference_experiment.run(base, folder)
            proposals = json.loads((folder / 'results.json').read_text())
            rows = merge_rows(rows, proposals, [], hospital)
        folder = steps / (hospital + '_units')
        unit_quantity_experiment.run(['--hospital', hospital, '--baseline', str(base), '--output-dir', str(folder)])
        proposals = json.loads((folder / 'results.json').read_text())
        rows = merge_rows(rows, [], eligible_units(rows, proposals, hospital), hospital)
        if hospital in {'H3', 'H5'}:
            current = steps / (hospital + '_before_history.json')
            save(current, rows)
            history = steps / (hospital + '_history')
            history_quantity_experiment.run(['--hospital', hospital, '--baseline', str(base),
                '--output-dir', str(history), '--merged-results', str(current)])
            proposals = json.loads((history / 'results.json').read_text())
            rows = merge_rows(rows, proposals, [], hospital)
            details = {p['record_index']: p for p in proposals if p['flagged'] is not None}
            for row in rows:
                if row['hospital_id'] == hospital and row['record_index'] in details:
                    row['conditional_answer_basis'] = 'historical_quantity_reference'
                    row['assessment_basis'] = row['assessment_basis'].replace(
                        'overlay: description_reference', 'overlay: historical_quantity_reference')
                    row['conditional_evidence'] = [a for line in details[row['record_index']]['lines']
                        for key in ('historical_unit_assumptions', 'history_family_assumptions')
                        for a in line.get(key, [])]
            folder = steps / (hospital + '_history_units')
            unit_quantity_experiment.run(['--hospital', hospital, '--baseline', str(history), '--output-dir', str(folder)])
            proposals = json.loads((folder / 'results.json').read_text())
            rows = merge_rows(rows, [], eligible_units(rows, proposals, hospital), hospital)
    assert len(rows) == len(original)
    for before, after in zip(original, rows):
        assert (before['hospital_id'], before['record_index'], before['invoice_id']) == (after['hospital_id'], after['record_index'], after['invoice_id'])
        if before['flagged'] is not None or before['record_identity_ambiguous']:
            assert before == after
    summary = json.loads((base / 'summary.json').read_text())
    for h in summary['hospitals']:
        eligible = [r for r in rows if r['hospital_id'] == h['hospital_id'] and not r['record_identity_ambiguous']]
        h.update(answered=sum(r['flagged'] is not None for r in eligible),
                 unanswered=sum(r['flagged'] is None for r in eligible),
                 flagged=sum(r['flagged'] == 1 for r in eligible),
                 predicted_correct=sum(r['flagged'] == 0 for r in eligible),
                 totals_provided=sum(r['expected_total_cents'] is not None for r in eligible),
                 unanswered_reasons=dict(Counter(s for r in eligible if r['flagged'] is None for s in r['review_reasons'])))
        h['method'] += '; fresh conditional improvements; assumptions retained'
    with (ROOT / 'submission_template.csv').open() as f:
        header = next(csv.reader(f))
    submission = submission_rows(rows, header, root=ROOT)
    assert len({r['invoice_id'] for r in submission}) == len(submission)
    assert all(isinstance(r['confidence'], (int, float)) and 0 <= r['confidence'] <= 1 for r in submission)
    summary.update(submission_rows=len(submission), unanswered_unique_invoices=sum(h['unanswered'] for h in summary['hospitals']),
                   conditional_overlay_count=sum(bool(r.get('conditional_answer')) for r in rows))
    save(out / 'all_results.json', rows)
    save(out / 'summary.json', summary)
    save(out / 'submission_data.json', {'columns': header, 'rows': submission})
    return out


def publish(directory, destination):
    """Back up the old CSV, then atomically install a validated completed export."""
    directory, destination = Path(directory), Path(destination)
    portable_csv_export(directory)
    content = (directory / 'submission.csv').read_bytes()
    backup = directory / 'prior_root_submission.csv'
    if destination.exists():
        with backup.open('xb') as f:
            f.write(destination.read_bytes())
    fd, temporary = tempfile.mkstemp(prefix='.submission-', dir=destination.parent)
    try:
        with os.fdopen(fd, 'wb') as f:
            f.write(content)
        os.replace(temporary, destination)
    finally:
        if os.path.exists(temporary):
            os.unlink(temporary)
    save(directory / 'publication.json', {'destination': str(destination),
        'sha256': hashlib.sha256(content).hexdigest(), 'backup': str(backup) if backup.exists() else None})
