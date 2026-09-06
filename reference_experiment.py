"""Offline H2 description pilot. Never changes the normal pipeline/submission.

Learn ambiguous-wording signatures from OTHER patient/invoice folds, supported
by fully worded reference services. These are conditional billing-pattern
inferences, not clinical confirmation. No labels, target prices or API calls.
"""
import copy
import gzip
import hashlib
import json
from collections import Counter, defaultdict
from pathlib import Path

import agents
import matching
import rules
from ai_client import save
from confidence import score_invoice
from pipeline import POLICY

ROOT = Path(__file__).resolve().parent


def choose_reference(names, observations, anchors, rates, units):
    """Equal invoice weights; signatures must be witnessed in clear references."""
    anchor_ids = {name: {r['invoice_id'] for r in anchors if r['service_name'] == name
                        and r['unit'] == units[name] and r['price'] in rates[name]} for name in names}
    signatures = {name: {(r['unit'], r['price']) for r in anchors if r['service_name'] == name
                         and r['unit'] == units[name] and r['price'] in rates[name]} for name in names}
    by_invoice = defaultdict(list)
    for row in observations:
        by_invoice[row['invoice_id']].append(row)
    evidence = {'reference_invoice_ids': sorted(by_invoice),
                'anchor_invoice_ids': {n: sorted(ids) for n, ids in anchor_ids.items()}}
    if len(names) < 2 or len(by_invoice) < 3 or any(len(ids) < 3 for ids in anchor_ids.values()):
        return None, {**evidence, 'reason': 'insufficient_independent_references'}
    support = {n: sum(sum((r['unit'], r['price']) in signatures[n] for r in rows) / len(rows)
                      for rows in by_invoice.values()) / len(by_invoice) for n in names}
    winners = [n for n in names if support[n] >= .8 and all(v <= .2 for k, v in support.items() if k != n)]
    return (winners[0] if len(winners) == 1 else None), {
        **evidence, 'rate_support': support,
        'reason': 'reference_gate_passed' if len(winners) == 1 else 'conflicting_reference_patterns'}


def normalized(description, contract, expansion=None):
    task = {'descriptions': [description]}
    answer = {'token_expansions': [{'token': k, 'expansion': v} for k, v in (expansion or {}).items()]}
    additions, _ = agents.repaired_expansions(answer, task, contract)
    return matching.tokens(description, {**contract['matching_guidance']['token_aliases'], **additions})


def run(base=None, out=None):
    base = Path(base) if base is not None else ROOT / 'audit_output/h2_history_prefix_trial'
    out = Path(out) if out is not None else ROOT / 'audit_output/h2_independent_reference_experiment'
    out.mkdir(exist_ok=True)
    contract = json.loads((base / 'hospital_2.runtime.json').read_text())
    draft = json.loads((ROOT / 'contract_agent_final_v4/hospital_2.json').read_text())
    invoices = list(map(json.loads, (ROOT / 'invoices/hospital_2_invoices.jsonl').read_text().splitlines()))
    with gzip.open(base / 'hospital_2.details.jsonl.gz', 'rt') as f:
        baseline = list(map(json.loads, f))
    counts, folds = Counter(i['invoice_id'] for i in invoices), agents.assign_folds(invoices)
    targets = {r['record_index'] for r in baseline if r['flagged'] is None
               and not r.get('record_identity_ambiguous') and r['review_reasons']
               and all(s.startswith('Unresolved service description') for s in r['review_reasons'])}
    catalogue = {s['service_name']: s for s in contract['services']}
    scope_rows = json.loads((base / 'H2.candidate_scopes.json').read_text())
    original_scopes = {(r['record_index'], r['line_id']): r for r in scope_rows}
    tasks, reference_rows = {}, []
    for index, inv in enumerate(invoices):
        for raw, line in zip(inv['line_items'], baseline[index]['lines']):
            assert raw['line_id'] == line['line_id']
            match = line['match']
            expansion = match.get('resolution_evidence', {}).get('token_expansions', {})
            if match.get('method') != 'ai_description_mapping':
                expansion = {}
            words = normalized(raw['description'], contract, expansion)
            name = match.get('service_name')
            # A price-inferred identity cannot become a supposedly clear anchor.
            clear = (name if name in catalogue and match.get('method') != 'price_pattern_inference'
                     and words == matching.tokens(name, contract['matching_guidance']['token_aliases']) else None)
            if counts[inv['invoice_id']] == 1 and matching.parse_date(raw.get('service_date')) and matching.parse_date(inv.get('invoice_date')):
                reference_rows.append({'record_index': index, 'invoice_id': inv['invoice_id'], 'line_id': raw['line_id'],
                    'pattern': agents.variant_key(raw['description']), 'service_name': clear,
                    'unit': raw.get('unit_basis_as_billed'), 'price': raw.get('unit_price_cents')})
            if index in targets and name is None:
                # SERVICE is generic; do not eliminate nursing solely because
                # the catalogue name omits this generic billing word.
                query = normalized(raw['description'], contract) - {'service'}
                candidates = sorted(n for n in catalogue if query <= matching.tokens(n, contract['matching_guidance']['token_aliases']))
                tasks[(index, raw['line_id'])] = {'pattern': agents.variant_key(raw['description']),
                                                'candidate_services': candidates, 'description': raw['description']}
    feedback, final = [], {}
    for heldout in range(5):
        with gzip.open(base / f'H2.fold_{heldout}.resolutions.jsonl.gz', 'rt') as f:
            resolutions = {(r['record_index'], r['line_id']): r for r in map(json.loads, f)}
        scopes = copy.deepcopy(original_scopes)
        for key, task in tasks.items():
            excluded = {heldout, folds[key[0]]}
            pool = [r for r in reference_rows if folds[r['record_index']] not in excluded]
            observations = [r for r in pool if r['pattern'] == task['pattern']]
            names = task['candidate_services']
            anchors = [r for r in pool if r['service_name'] in names]
            chosen, evidence = choose_reference(names, observations, anchors,
                {n: agents.possible_rates(contract, n) for n in names}, {n: catalogue[n]['unit_basis'] for n in names})
            feedback.append({'heldout': heldout, 'record_index': key[0], 'line_id': key[1],
                **task, 'excluded_folds': sorted(excluded), 'selected_service': chosen, **evidence})
            if not chosen:
                continue
            assert all(folds[r['record_index']] not in excluded for r in observations + anchors)
            scopes[key] = {'candidate_services': names, 'basis': 'exhaustive_catalogue_after_validated_AI_expansions',
                           'assumption': 'Normalized wording is truthful; generic service does not constrain the family.'}
            resolutions[key] = {'service_name': chosen, 'basis': 'price_pattern_inference',
                'evidence_tier': 'experimental_clear_anchor_reference', **evidence,
                'explanation': 'Experimental cross-fitted inference from other invoices with the same wording, corroborated by fully worded anchors; not confirmed identity.',
                'reference_scope': 'other_folds_same_exact_wording_and_clear_anchors', 'excluded_reference_folds': sorted(excluded)}
        rows = rules.audit(contract, invoices, policy=POLICY, extended_contract=draft,
                           line_service_resolutions=resolutions, line_candidate_scopes=scopes)
        for index in targets:
            if folds[index] == heldout:
                row = rows[index]
                row.update(hospital_id='H2', experimental=True, reference_fold=heldout)
                row.update(score_invoice(row))
                final[index] = row
        print(f'Experimental H2 fold {heldout+1}/5 audited', flush=True)
    summary = {'target_invoices': len(targets), 'answered': sum(r['flagged'] is not None for r in final.values()),
               'unanswered': sum(r['flagged'] is None for r in final.values()),
               'verdicts': dict(Counter(str(r['flagged']) for r in final.values())),
               'note': 'Experimental conditional coverage, not H2 accuracy. Normal pipeline and submission unchanged. No APIs or labels used.'}
    save(out / 'reference_evidence.json', feedback)
    save(out / 'results.json', [final[i] for i in sorted(final)])
    save(out / 'summary.json', summary)
    sources = [base / 'hospital_2.runtime.json', base / 'hospital_2.details.jsonl.gz',
               base / 'H2.candidate_scopes.json', ROOT / 'contract_agent_final_v4/hospital_2.json',
               ROOT / 'invoices/hospital_2_invoices.jsonl', Path(__file__),
               ROOT / 'agents.py', ROOT / 'matching.py', ROOT / 'rules.py', ROOT / 'confidence.py']
    sources += [base / f'H2.fold_{i}.resolutions.jsonl.gz' for i in range(5)]
    save(out / 'provenance.json', {str(p.relative_to(ROOT)): hashlib.sha256(p.read_bytes()).hexdigest() for p in sources})
    print(json.dumps(summary, indent=2))


if __name__ == '__main__':
    run()
