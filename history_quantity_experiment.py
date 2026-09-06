"""H3 offline historical unit-label experiment, isolated from normal results."""
import gzip
import hashlib
import json
from collections import Counter
from pathlib import Path

import agents
import argparse
import matching
import rules
from ai_client import save
from confidence import score_invoice
from pipeline import POLICY
from reference_experiment import normalized

ROOT = Path(__file__).resolve().parent


def run(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--hospital', choices=['H3', 'H5'], default='H3')
    parser.add_argument('--baseline', type=Path)
    parser.add_argument('--output-dir', type=Path)
    parser.add_argument('--merged-results', type=Path)
    args = parser.parse_args(argv)
    hospital = args.hospital
    hospital_stem = 'hospital_' + hospital[1:]
    prefix = hospital.lower()
    base = args.baseline or ROOT / ('audit_output/' + prefix + '_family_trial')
    out = args.output_dir or ROOT / ('audit_output/' + prefix + '_history_quantity_experiment')
    out.mkdir(exist_ok=True)
    contract = json.loads((base / (hospital_stem + '.runtime.json')).read_text())
    if hospital == 'H3':
        contract['uncertainty_policy']['single_word_volume_families'] = True
    draft = json.loads((ROOT / ('contract_agent_final_v4/' + hospital_stem + '.json')).read_text())
    invoices = list(map(json.loads, (ROOT / ('invoices/' + hospital_stem + '_invoices.jsonl')).read_text().splitlines()))
    with gzip.open(base / (hospital_stem + '.details.jsonl.gz'), 'rt') as f:
        baseline = list(map(json.loads, f))
    merged_path = args.merged_results or ROOT / ('audit_output/' + prefix + '_merged_conditional/all_results.json')
    merged = json.loads(merged_path.read_text())
    targets = {r['record_index'] for r in merged if r['hospital_id'] == hospital and r['flagged'] is None and not r['record_identity_ambiguous']}
    if hospital == 'H5':
        from reporting import reason_codes
        targets &= {r['record_index'] for r in merged if r['hospital_id'] == hospital
                    and set(reason_codes(r['review_reasons'])) <= {'volume_discount', 'contract_unit'}}
    counts, folds = Counter(i['invoice_id'] for i in invoices), agents.assign_folds(invoices)
    catalogue = {s['service_name']: s for s in contract['services']}
    volume_names = {r['service_name'] for r in contract['volume_discounts']['rules']}
    candidates, references = [], []
    for index, inv in enumerate(invoices):
        if counts[inv['invoice_id']] != 1:
            continue
        for raw, line in zip(inv['line_items'], baseline[index]['lines']):
            assert raw['line_id'] == line['line_id']
            name = line['match'].get('service_name')
            if name not in volume_names or line['match'].get('inferred'):
                continue
            words = normalized(raw['description'], contract, line['match'].get('resolution_evidence', {}).get('token_expansions', {}))
            if words != matching.tokens(name, contract['matching_guidance']['token_aliases']):
                continue
            if not matching.parse_date(raw.get('service_date')) or not matching.integer(raw.get('quantity')) or raw['quantity'] <= 0:
                continue
            entry = {'record_index': index, 'invoice_id': inv['invoice_id'], 'line_id': raw['line_id'],
                     'service_name': name, 'quantity': raw['quantity'], 'billed_unit': raw['unit_basis_as_billed']}
            if raw['unit_price_cents'] not in agents.contextual_rates(contract, draft, name, raw, inv):
                continue
            if raw['unit_basis_as_billed'] == catalogue[name]['unit_basis']:
                references.append(entry)
            else:
                candidates.append(entry)
    scope_rows = json.loads((base / (hospital + '.candidate_scopes.json')).read_text())
    scopes = {(r['record_index'], r['line_id']): r for r in scope_rows}
    final, evidence, all_final = {}, [], {}
    for heldout in range(5):
        estimates = {}
        for event in candidates:
            excluded = {heldout, folds[event['record_index']]}
            refs = [r for r in references if folds[r['record_index']] not in excluded
                    and r['service_name'] == event['service_name']]
            ids = sorted({r['invoice_id'] for r in refs})
            observed = sorted({r['quantity'] for r in refs})
            if len(ids) < 3 or event['quantity'] not in observed:
                continue
            estimate = {**event, 'reference_invoice_ids': ids, 'reference_quantities': observed,
                        'excluded_folds': sorted(excluded),
                        'assumption': 'The historical numeric quantity is correct and only its unit label is wrong. Other clear-service invoices corroborate this convention; they do not verify actual delivery.'}
            estimates[(event['record_index'], event['line_id'])] = estimate
            evidence.append({'heldout': heldout, **estimate})
        with gzip.open(base / f'{hospital}.fold_{heldout}.resolutions.jsonl.gz', 'rt') as f:
            resolutions = {(r['record_index'], r['line_id']): r for r in map(json.loads, f)}
        rows = rules.audit(contract, invoices, policy=POLICY, extended_contract=draft,
                           line_service_resolutions=resolutions, line_candidate_scopes=scopes,
                           historical_unit_estimates=estimates)
        for row in rows:
            if folds[row['record_index']] == heldout:
                row.update(hospital_id=hospital, record_identity_ambiguous=counts[row['invoice_id']] != 1)
                all_final[row['record_index']] = row
            if row['record_index'] in targets and folds[row['record_index']] == heldout:
                row.update(hospital_id=hospital, conditional_answer=True, reference_fold=heldout)
                row.update(score_invoice(row))
                final[row['record_index']] = row
        # The source invoice's actual wrong-unit finding must not disappear.
        for key in estimates:
            assert any(f['category'] == 'wrong_unit_basis' and f['line_id'] == key[1] for f in rows[key[0]]['findings'])
        print(f'{hospital} history experiment fold {heldout+1}/5', flush=True)
    save(out / 'reference_evidence.json', evidence)
    save(out / 'results.json', [final[i] for i in sorted(final)])
    save(out / (hospital_stem + '.runtime.json'), contract)
    with gzip.open(out / (hospital_stem + '.details.jsonl.gz'), 'wt') as stream:
        for i in sorted(all_final):
            stream.write(json.dumps(all_final[i]) + '\n')
    paths = [Path(__file__), ROOT / 'rules.py', ROOT / 'matching.py', ROOT / 'confidence.py',
             base / (hospital_stem + '.runtime.json'), base / (hospital_stem + '.details.jsonl.gz'),
             ROOT / ('invoices/' + hospital_stem + '_invoices.jsonl'), ROOT / ('contract_agent_final_v4/' + hospital_stem + '.json'),
             merged_path]
    paths += [base / f'{hospital}.fold_{i}.resolutions.jsonl.gz' for i in range(5)]
    save(out / 'provenance.json', {str(p.relative_to(ROOT)): hashlib.sha256(p.read_bytes()).hexdigest() for p in paths})
    summary = {'target_invoices': len(targets), 'answered': sum(r['flagged'] is not None for r in final.values()),
               'unanswered': sum(r['flagged'] is None for r in final.values()),
               'verdicts': dict(Counter(str(r['flagged']) for r in final.values())),
               'note': 'Conditional history assumptions only; original submission is unchanged.'}
    save(out / 'summary.json', summary)
    print(json.dumps(summary, indent=2))


if __name__ == '__main__':
    run()
