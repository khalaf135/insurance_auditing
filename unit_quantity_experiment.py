"""Separate clause-3.3 arithmetic/pricing pilot; never establishes a unit.

Uses billed quantity only for services without quantity-dependent or cross-line
rules. Original audit verdicts and the normal submission are never overwritten.
"""
import copy
import argparse
import gzip
import hashlib
import json
from collections import Counter
from pathlib import Path

import contracts
import matching
from ai_client import save

ROOT = Path(__file__).resolve().parent
ASSUMPTION = 'The quantity billed is already expressed in the combined contractual unit; its measurement and clinical support have not been verified.'


def calculate_billed_quantity(contract, draft, invoice, raw, service):
    """Fail closed on dependencies or unsupported compound wording."""
    if service.get('unit_basis') is not None:
        raise ValueError('This pilot is only for unresolved units')
    if 'per hour, per item' not in service.get('source', {}).get('text', '').lower():
        raise ValueError('Unsupported compound wording')
    if raw.get('unit_basis_as_billed') != 'per_hour_per_item':
        raise ValueError('Billed unit does not preserve the compound wording')
    if service.get('daily_cap') is not None:
        raise ValueError('Quantity-dependent cap requires a unit definition')
    name = service['service_name']
    for section_name, section in contract.items():
        if isinstance(section, dict):
            for rule in section.get('rules', []):
                if (section_name == 'exclusion_windows' and rule.get('related_service') == name
                        and rule.get('excluded_service') != name):
                    # Positive occurrence is already established by the audit;
                    # this experiment changes neither presence nor exclusions.
                    continue
                if any(value == name for value in rule.values() if isinstance(value, str)):
                    raise ValueError('Service participates in a rule; unit-independent pricing not established')
    if not all(matching.integer(raw.get(k)) for k in ('quantity', 'unit_price_cents', 'line_total_cents')) or raw['quantity'] <= 0:
        raise ValueError('Invalid billed quantity or amounts')
    context = contracts.pricing_context(draft, name, raw, invoice, service['base_rate_cents'])
    if context.get('review') or context.get('finding'):
        raise ValueError('Unresolved pricing context')
    rate = context.get('base_rate_cents', service['base_rate_cents'])
    for field in ('facility_multiplier', 'plan_tier_multiplier'):
        multiplier = context.get(field, contract['contract_details'][field])
        rate = matching.rounded_ratio(rate, multiplier['numerator'], multiplier['denominator'])
    total = rate * raw['quantity']
    errors = []
    if raw['unit_price_cents'] != rate:
        errors.append('unit_price_mismatch')
    if raw['line_total_cents'] != raw['unit_price_cents'] * raw['quantity']:
        errors.append('line_total_arithmetic')
    return {'line_id': raw['line_id'], 'effective_rate_cents': rate,
            'billed_quantity': raw['quantity'], 'conditional_expected_line_total_cents': total,
            'conditional_errors': errors, 'quantity_verified': False, 'assumption': ASSUMPTION}


def run(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--hospital', choices=['H2', 'H3', 'H4', 'H5'], default='H2')
    parser.add_argument('--baseline', type=Path, default=ROOT / 'audit_output/h2_history_prefix_trial')
    parser.add_argument('--output-dir', type=Path)
    args = parser.parse_args(argv)
    hospital = args.hospital
    name = 'hospital_' + hospital[1:]
    base = args.baseline.resolve()
    out = args.output_dir.resolve() if args.output_dir else ROOT / ('audit_output/' + hospital.lower() + '_billed_quantity_experiment')
    out.mkdir(exist_ok=True)
    contract = json.loads((base / (name + '.runtime.json')).read_text())
    draft = json.loads((ROOT / ('contract_agent_final_v4/' + name + '.json')).read_text())
    source_names = {'H2': 'master_services_agreement.txt', 'H3': 'base_agreement.txt', 'H4': 'conditional_reimbursement_agreement.txt', 'H5': 'network_reimbursement_agreement.txt'}
    source_path = ROOT / ('contracts/' + name + '/' + source_names[hospital])
    source = source_path.read_text()
    clause_number = '3.1' if hospital == 'H5' else ('4.4' if hospital == 'H4' else '3.3')
    clause = next(line for line in source.splitlines() if line.startswith(clause_number + ' '))
    if not any(p in clause for p in ('effective unit rate', 'resulting unit rate')) or not any(p in clause for p in ('quantity billed', 'billed quantity')):
        raise ValueError('Clause 3.3 calculation basis changed')
    invoice_path = ROOT / ('invoices/' + name + '_invoices.jsonl')
    invoices = list(map(json.loads, invoice_path.read_text().splitlines()))
    assert all(r['hospital_id'] == hospital for r in invoices)
    with gzip.open(base / (name + '.details.jsonl.gz'), 'rt') as f:
        rows = list(map(json.loads, f))
    catalogue = {s['service_name']: s for s in contract['services']}
    results = []
    for row in rows:
        if row['flagged'] is not None or row.get('record_identity_ambiguous') or not row['review_reasons']:
            continue
        if not all(s.startswith('Contract unit basis ambiguous') for s in row['review_reasons']):
            continue
        inv = invoices[row['record_index']]
        assert inv['invoice_id'] == row['invoice_id']
        raw_lines = {l['line_id']: l for l in inv['line_items']}
        result = {'invoice_id': row['invoice_id'], 'record_index': row['record_index'],
                  'confirmed_flagged': row['flagged'], 'conditional_flagged': None,
                  'conditional_expected_total_cents': None, 'quantity_verified': False,
                  'remaining_review_reasons': copy.deepcopy(row['review_reasons']),
                  'calculation_basis': clause, 'assumption': ASSUMPTION, 'lines': []}
        try:
            total, errors = 0, []
            for line in row['lines']:
                if line['expected_line_total_cents'] is not None:
                    total += line['expected_line_total_cents']
                else:
                    service = catalogue[line['match']['service_name']]
                    detail = calculate_billed_quantity(contract, draft, inv, raw_lines[line['line_id']], service)
                    result['lines'].append(detail)
                    total += detail['conditional_expected_line_total_cents']
                    errors.extend(detail['conditional_errors'])
            if total != inv['invoice_total_cents']:
                errors.append('conditional_invoice_total_difference')
            result.update(conditional_flagged=int(bool(errors)), conditional_expected_total_cents=total,
                          conditional_errors=sorted(set(errors)))
        except (ValueError, KeyError, TypeError) as exc:
            result['deferred_reason'] = str(exc)
        results.append(result)
    summary = {'target_invoices': len(results), 'conditional_verdicts': dict(Counter(str(r['conditional_flagged']) for r in results)),
               'confirmed_unit_resolutions': 0, 'normal_submission_changed': False,
               'note': 'Pricing against billed quantities only; physical quantity/unit compliance remains unresolved.'}
    save(out / 'results.json', results)
    save(out / 'summary.json', summary)
    paths = [source_path, base / (name + '.runtime.json'), base / (name + '.details.jsonl.gz'),
             ROOT / ('contract_agent_final_v4/' + name + '.json'), invoice_path,
             Path(__file__), ROOT / 'contracts.py', ROOT / 'matching.py']
    save(out / 'provenance.json', {str(p.relative_to(ROOT)): hashlib.sha256(p.read_bytes()).hexdigest() for p in paths})
    print(json.dumps(summary, indent=2))


if __name__ == '__main__':
    run()
