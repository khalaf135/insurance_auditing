#!/usr/bin/env python3
"""Local regression suite. Run with ``python test.py``; no invoice audit or API calls.

All regression families use the active root modules and shared synthetic fixtures.
Real contract documents are read only to verify extraction and provenance.
"""
from contextlib import redirect_stderr, redirect_stdout
import copy
import csv
import hashlib
import io
import json
from pathlib import Path
import re
import subprocess
import sys
import tempfile
from types import SimpleNamespace
import unittest
from unittest.mock import call, patch

# Keep local test runs from creating bytecode folders beside the source files.
sys.dont_write_bytecode = True

import agents
import ai_client
import confidence
import contract_builder
import contracts
import main
import matching
import pipeline
import reporting
import reference_experiment
import unit_quantity_experiment
import merge_h2_results
import complete_workflow
import rules

ROOT = Path(__file__).resolve().parent
FALLBACK_POLICY_PATH = ROOT / 'config/fallback_v2_policy.json'


def setUpModule():
    """A missing mock must fail locally, before any paid or remote request."""
    network = patch(
        'urllib.request.urlopen',
        side_effect=AssertionError('The local test suite forbids network requests'),
    )
    network.start()
    unittest.addModuleCleanup(network.stop)


# Shared invoice and contract fixtures; baseline audit

def sample_contract():
    """Return fresh minimal rules while retaining the reviewed H1 schema."""
    c = json.loads((ROOT / 'structured_contracts/hospital_1.json').read_text())
    c['schema_version'] = '1.0'
    c.pop('matching_guidance', None)
    c.pop('uncertainty_policy', None)
    for key in ('threshold_premiums', 'non_business_day_uplifts', 'volume_discounts', 'daily_caps', 'bundles', 'exclusion_windows'):
        c[key]['rules'] = []
    c['services'] = [
        {'service_name': 'Alpha Consultation', 'unit_basis': 'per_visit',
         'base_rate_cents': 101, 'daily_cap': None},
        {'service_name': 'Beta Laboratory Panel', 'unit_basis': 'per_test',
         'base_rate_cents': 200, 'daily_cap': None},
    ]
    return c

def sample_invoice(identifier='one', service='Alpha Consultation', quantity=1, price=101, day='2024-01-05', patient='patient'):
    """Return an independent single-line invoice, with all amounts in cents."""
    return {
        'invoice_id': identifier, 'hospital_id': 'H1',
        'contract_number': 'INS-H1-2024-0417', 'invoice_date': '2024-01-09',
        'patient_id': patient, 'facility_code': 'F-MAIN', 'plan_tier': 'GOLD',
        'invoice_total_cents': price * quantity,
        'line_items': [{
            'line_id': identifier + '-line', 'invoice_id': identifier,
            'service_date': day, 'description': service, 'quantity': quantity,
            'unit_basis_as_billed': 'per_visit', 'unit_price_cents': price,
            'line_total_cents': price * quantity,
        }],
    }

class BaselineAuditTests(unittest.TestCase):

    def test_labels_cannot_change_a_different_hospitals_audit(self):
        c, inv = sample_contract(), sample_invoice()
        c['contract_details']['contract_number'] = 'INS-H9-TEST'
        inv.update(contract_number='INS-H9-TEST', hospital_id='H9')
        rows = rules.audit_baseline(c, [inv])
        original = copy.deepcopy(rows)
        with tempfile.TemporaryDirectory() as directory:
            labels = Path(directory) / 'hospital_9_labels.csv'
            labels.write_text(
                'invoice_id,is_erroneous,error_categories,expected_total_cents,ambiguity_sensitive\n'
                'one,1,unit_price_mismatch,999,0\n'
            )
            result = reporting.metrics(rows, labels, [inv])
        self.assertEqual(result['incorrect'], 1)
        self.assertEqual(rows, original)
        self.assertEqual(rows, rules.audit_baseline(c, [inv]))
        self.assertEqual(inv['hospital_id'], 'H9')
        self.assertEqual(rows[0]['expected_total_cents'], 101)

    def test_correct_and_date_violation_preserve_amount(self):
        a = rules.audit_baseline(sample_contract(), [sample_invoice()])[0]
        self.assertEqual((a['flagged'], a['expected_total_cents']), (0, 101))
        a = rules.audit_baseline(sample_contract(), [sample_invoice(day='2024-01-13')])[0]
        self.assertEqual(a['error_category'], 'service_date_after_invoice_date')
        self.assertEqual(a['expected_total_cents'], 101)

    def test_exact_rounding(self):
        self.assertEqual(matching.rounded_ratio(101, 150, 100), 152)
        self.assertEqual(matching.rounded_ratio(-101, 150, 100), -152)

    def test_weekend_rate(self):
        c = sample_contract()
        c['non_business_day_uplifts']['rules'] = [{'service_name': 'Alpha Consultation', 'uplift_percent': 50}]
        a = rules.audit_baseline(c, [sample_invoice(day='2024-01-07', price=152)])[0]
        self.assertEqual((a['flagged'], a['expected_total_cents']), (0, 152))
        a = rules.audit_baseline(c, [sample_invoice(day='2024-01-07', price=101)])[0]
        self.assertIn('premium_omitted', a['error_category'])

    def test_prior_usage_strict_threshold_and_sort(self):
        c = sample_contract()
        c['volume_discounts']['rules'] = [{'service_name': 'Alpha Consultation', 'quantity': 2, 'comparison': 'greater_than', 'discount_percent': 10}]
        inputs = [sample_invoice('c', day='2024-01-03', price=91), sample_invoice('b', day='2024-01-02'), sample_invoice('a', quantity=2, day='2024-01-01')]
        out = rules.audit_baseline(c, inputs)
        self.assertEqual([r['flagged'] for r in out], [0, 0, 0])
        self.assertEqual(out[0]['lines'][0]['calculation'][-1]['prior_units'], 3)

    def test_unmapped_abstains_and_taints_discount_history(self):
        c = sample_contract()
        c['volume_discounts']['rules'] = [{'service_name': 'Alpha Consultation', 'quantity': 2, 'comparison': 'greater_than', 'discount_percent': 10}]
        a, b = rules.audit_baseline(c, [sample_invoice('a', service='nonsense xyz'), sample_invoice('b', patient='other')])
        self.assertIsNone(a['flagged'])
        self.assertIsNone(b['expected_total_cents'])

    def test_malformed_date_no_crash(self):
        out = rules.audit_baseline(sample_contract(), [sample_invoice(day='2024-99-01')])[0]
        self.assertIn('malformed_service_date', out['error_category'])
        self.assertIsNone(out['expected_total_cents'])

    def test_duplicate_ids_preserved(self):
        out = rules.audit_baseline(sample_contract(), [sample_invoice(), sample_invoice()])
        self.assertEqual(len(out), 2)
        self.assertTrue(all(('duplicate_invoice_id' in r['error_category'] for r in out)))
        self.assertTrue(all((r['expected_total_cents'] is None for r in out)))

    def test_daily_aggregation_across_invoices(self):
        c = sample_contract()
        c['daily_caps']['rules'] = [{'service_name': 'Alpha Consultation', 'quantity': 3}]
        out = rules.audit_baseline(c, [sample_invoice('a', quantity=2), sample_invoice('b', quantity=2)])
        self.assertTrue(all(('daily_cap_exceeded' in r['error_category'] for r in out)))

    def test_bundle_and_boundary_uncertainty(self):
        c = sample_contract()
        c['bundles']['rules'] = [{'service_a': 'Alpha Consultation', 'service_b': 'Beta Laboratory Panel', 'bundled_rate_a_cents': 90, 'bundled_rate_b_cents': 180}]
        a, b = (sample_invoice(price=90), sample_invoice('b', service='Beta Laboratory Panel', price=180))
        b['line_items'][0]['unit_basis_as_billed'] = 'per_test'
        out = rules.audit_baseline(c, [a, b])
        self.assertEqual([r['flagged'] for r in out], [0, 0])
        c['exclusion_windows']['rules'] = [{'excluded_service': 'Alpha Consultation', 'related_service': 'Beta Laboratory Panel', 'window_days': 2}]
        b['line_items'][0]['service_date'] = '2024-01-07'
        out = rules.audit_baseline(c, [a, b])
        self.assertTrue(any(('boundary' in x for x in out[0]['review_reasons'])))

    def test_unsupported_semantics_fail_closed(self):
        c = sample_contract()
        c['definitions']['service_day'] = '07:00_to_06:59'
        with self.assertRaisesRegex(ValueError, 'Non-calendar'):
            rules.audit_baseline(c, [sample_invoice()])
        c = sample_contract()
        c['services'][0]['rate_versions'] = []
        with self.assertRaisesRegex(ValueError, 'Unsupported service'):
            rules.audit_baseline(c, [sample_invoice()])

# Contract extraction and source evidence

class ContractAgentTests(unittest.TestCase):

    def test_hospital_isolation_and_multidocument_discovery(self):
        docs, inventory = contract_builder.discover(ROOT / 'contracts/hospital_3')
        self.assertEqual({d['document_id'] for d in docs}, {'base_agreement.txt', 'appendix_b_rate_schedule.txt', 'amendment_no_1.txt'})
        request = contract_builder.make_request('H3', docs[0], next(contract_builder.chunks(docs[0])), docs, ai_client.MODEL)
        payload = request['messages'][1]['content']
        self.assertIn('INS-H3-2024-0562', payload)
        self.assertNotIn('INS-H1-2024-0417', payload)

    def test_chunks_cover_every_line_once(self):
        doc = {'lines': ['long clause ' * 400, 'b', '', 'c']}
        covered = [n for c in contract_builder.chunks(doc, 1000) for n in range(c['line_start'], c['line_end'] + 1)]
        self.assertEqual(covered, [1, 2, 3, 4])

    def test_money_and_evidence_validation(self):
        doc = {'document_id': 'x.txt', 'sha256': 'x', 'lines': ['Alpha Consultation  per visit  GBP 1.01']}
        item = {'section': 'services', 'key': 'Alpha Consultation', 'value_json': json.dumps({'service_name': 'Alpha Consultation', 'unit_basis': 'per_visit', 'unit_basis_source': 'per visit', 'base_rate_cents': 101, 'daily_cap': None}), 'line_start': 1, 'line_end': 1, 'uncertainty': None}

        def answer():
            return {'choices': [{'finish_reason': 'stop', 'message': {'content': json.dumps({'items': [item], 'warnings': []})}}]}
        facts, issues = contract_builder.validate_response(answer(), doc, {'line_start': 1, 'line_end': 1})
        self.assertEqual(len(facts), 1)
        item['value_json'] = item['value_json'].replace('101', '102')
        facts, issues = contract_builder.validate_response(answer(), doc, {'line_start': 1, 'line_end': 1})
        self.assertFalse(facts)
        self.assertIn('Monetary', issues[0]['reason'])
        item['line_start'] = 0
        facts, issues = contract_builder.validate_response(answer(), doc, {'line_start': 1, 'line_end': 1})
        self.assertFalse(facts)

    def test_conflicting_rates_never_silently_overwritten(self):
        facts = [{'section': 'services', 'key': 'Alpha', 'value': {'service_name': 'Alpha', 'base_rate_cents': price}, 'uncertainty': None, 'source': {'document_id': f'{price}.txt'}} for price in (101, 102)]
        j = contract_builder.assemble('H7', [], [], facts, [], 0)
        self.assertIsNone(j['services'][0]['base_rate_cents'])
        self.assertEqual(len(j['services'][0]['variants']), 2)
        self.assertFalse(j['uncertainty_policy']['automatic_audit_allowed'])

    def test_offline_cache_miss_does_not_use_network(self):
        with tempfile.TemporaryDirectory() as temp, patch('urllib.request.urlopen', side_effect=AssertionError('network')):
            api = ai_client.API({}, output_dir=temp, cache_only=True)
            with self.assertRaisesRegex(RuntimeError, 'Offline cache miss'):
                api.call('reasoning', {'model': ai_client.MODEL})

# Reviewed H1 contract conversion

class ContractConverterTests(unittest.TestCase):

    @classmethod
    def setUpClass(cls):
        cls.text = (ROOT / 'contracts/hospital_1/provider_services_agreement.txt').read_text()
        cls.result = contract_builder.convert_h1(cls.text, 'contract.txt')

    def test_independent_markdown_rate_schedule(self):
        md = (ROOT / 'contracts/hospital_1/provider_services_agreement.md').read_text()
        section = md.split('## 4. Rate Schedule')[1].split('## 5.')[0]
        expected = {}
        for line in section.splitlines():
            if '| GBP ' in line:
                name, unit, rate, cap = [c.strip() for c in line.strip('|').split('|')]
                expected[name] = (unit, int(rate[4:].replace(',', '').replace('.', '')), cap)
        actual = {s['service_name']: (s['unit_basis_source'], s['base_rate_cents']) for s in self.result['services']}
        self.assertEqual(len(actual), 108)
        self.assertEqual(actual, {k: v[:2] for k, v in expected.items()})

    def test_rule_counts_and_source_coverage(self):
        for field, count in [('threshold_premiums', 9), ('non_business_day_uplifts', 7), ('volume_discounts', 11), ('daily_caps', 7), ('bundles', 3), ('exclusion_windows', 6)]:
            self.assertEqual(len(self.result[field]['rules']), count)
            for rule in self.result[field]['rules']:
                source = rule['source']
                self.assertEqual(self.text.splitlines()[source['line'] - 1], source['text'])
        self.assertEqual(len(self.result['source_sections']), 11)
        self.assertEqual(len(self.result['source_clauses']), len(re.findall('^\\d+\\.\\d+ ', self.text, re.M)))

    def test_rate_is_extracted_not_hardcoded(self):
        changed = contract_builder.convert_h1(self.text.replace('GBP 200.00', 'GBP 201.23'), 'changed.txt')
        self.assertEqual(changed['services'][0]['base_rate_cents'], 20123)

    def test_changed_prose_rejected(self):
        with self.assertRaisesRegex(ValueError, 'changed contract prose'):
            contract_builder.convert_h1(self.text.replace('not once at the end', 'only once at the end'), 'changed.txt')

    def test_conflicting_caps_rejected(self):
        with self.assertRaisesRegex(ValueError, 'caps disagree'):
            contract_builder.convert_h1(self.text.replace('6 days', '5 days', 1), 'changed.txt')

    def test_unknown_service_reference_rejected(self):
        with self.assertRaisesRegex(ValueError, 'unknown service'):
            contract_builder.convert_h1(self.text.replace('Advanced Neurological Consultation           +20%', 'Unknown Consultation                        +20%'), 'changed.txt')

    def test_malformed_table_row_rejected(self):
        with self.assertRaisesRegex(ValueError, 'Unparsed row'):
            contract_builder.convert_h1(self.text.replace('5. THRESHOLD PREMIUMS', 'Unexpected broken row\n\n5. THRESHOLD PREMIUMS'), 'changed.txt')

    def test_money_and_unresolved_semantics(self):
        self.assertEqual(contract_builder.h1_money('GBP 1,301.25'), 130125)
        with self.assertRaises(ValueError):
            contract_builder.h1_money('GBP 1.005')
        self.assertIsNone(self.result['exclusion_windows']['boundary_inclusive'])
        self.assertEqual(self.result['invoice_requirements']['service_date_within_admission_discharge'], 'not_stated')

        def check(value):
            self.assertNotIsInstance(value, float)
            if isinstance(value, dict):
                for child in value.values():
                    check(child)
            elif isinstance(value, list):
                for child in value:
                    check(child)
        check(self.result)

# Contract runtime and amendments

class ContractRuntimeTests(unittest.TestCase):

    def test_literal_h1_rules_match_reviewed_counts(self):
        docs, _ = contract_builder.discover(ROOT / 'contracts/hospital_1')
        facts, issues = contract_builder.literal_facts(docs[0])
        self.assertFalse(issues)
        old = json.loads((ROOT / 'structured_contracts/hospital_1.json').read_text())
        for section in ['threshold_premiums', 'non_business_day_uplifts', 'volume_discounts', 'bundles', 'exclusion_windows']:
            self.assertEqual(sum((f['section'] == section for f in facts)), len(old[section]['rules']), section)
        services = [f['value'] for f in facts if f['section'] == 'services']
        self.assertEqual({s['service_name']: s['base_rate_cents'] for s in services}, {s['service_name']: s['base_rate_cents'] for s in old['services']})

    def test_threshold_and_discount_table_context_distinct(self):
        d = {'document_id': 'sample.txt', 'sha256': 'x', 'text': '', 'lines': ['5. THRESHOLD PREMIUMS', 'Alpha  8 hours  +20%', '6. VOLUME DISCOUNTS', 'Alpha  more than 80 hours  10%']}
        facts, issues = contract_builder.literal_facts(d)
        self.assertEqual([f['section'] for f in facts], ['threshold_premiums', 'volume_discounts'])
        self.assertFalse(issues)

    def test_matrix_decimal_is_exact_rational(self):
        d = {'document_id': 'sample.txt', 'sha256': 'x', 'text': '', 'lines': ['Service  F-MAIN  F-NORTH  F-COAST', 'Alpha Service  1  1.15  0.92']}
        facts, issues = contract_builder.literal_facts(d)
        self.assertEqual(facts[0]['value']['multipliers']['F-NORTH'], {'numerator': 23, 'denominator': 20})
        self.assertFalse(issues)

    def test_version_uses_service_date_not_invoice_date(self):
        c = {'extensions': {'rate_versions': [{'service_name': 'Alpha', 'old_rate_cents': 100, 'new_rate_cents': 120, 'effective_from': '2025-01-01', 'applies_by': 'service_date'}], 'additional_services': [], 'facility_multipliers': [], 'plan_tier_multipliers': []}}
        before = contracts.pricing_context(c, 'Alpha', {'service_date': '2024-12-31'}, {'invoice_date': '2025-02-01'}, 100)
        after = contracts.pricing_context(c, 'Alpha', {'service_date': '2025-01-01'}, {'invoice_date': '2025-02-01'}, 100)
        self.assertEqual((before['base_rate_cents'], after['base_rate_cents']), (100, 120))

    def test_new_service_not_billable_early(self):
        c = {'extensions': {'rate_versions': [], 'additional_services': [{'service_name': 'Alpha', 'base_rate_cents': 100, 'effective_from': '2025-01-01'}], 'facility_multipliers': [], 'plan_tier_multipliers': []}}
        self.assertEqual(contracts.pricing_context(c, 'Alpha', {'service_date': '2024-12-31'}, {}, 100)['finding'], 'service_not_yet_contracted')

    def test_ambiguous_contract_unit_does_not_become_wrong_unit(self):
        c = sample_contract()
        c['services'][0]['unit_basis'] = None
        row = rules.audit(c, [sample_invoice()])[0]
        self.assertNotIn('wrong_unit_basis', row['error_category'])
        self.assertIsNone(row['flagged'])
        self.assertIsNone(row['expected_total_cents'])

    def test_h2_service_day_not_silently_calendar(self):
        c = {'extraction_status': {'all_chunks_processed': True, 'unreadable_documents': [], 'uncovered_currency_lines': []}, 'source_sections': [], 'conflicts': [], 'definitions': {'service_day': '07:00 to 06:59'}}
        with self.assertRaisesRegex(ValueError, 'UNSUPPORTED_SERVICE_DAY'):
            contracts.compile_contract(c, {})

    def test_h2_date_label_opt_in_preserves_source(self):
        draft = json.loads((ROOT / 'contract_agent_final_v4/hospital_2.json').read_text())
        original = copy.deepcopy(draft)
        with self.assertRaisesRegex(ValueError, 'UNSUPPORTED_SERVICE_DAY'):
            contracts.compile_contract(draft, {})
        c = contracts.compile_contract(draft, {}, service_date_as_billing_day=True)
        self.assertEqual(draft, original)
        self.assertEqual(c['definitions']['service_day'], 'calendar_date_of_line_service_date')
        self.assertEqual(c['uncertainty_policy']['service_day_assumption']['source_definition'], draft['definitions']['service_day'])
        self.assertEqual(c['invoice_requirements']['duplicate_scope'], 'not_stated')
        self.assertEqual(len(c['services']), 76)
        self.assertTrue(all((type(r['uplift_percent']) is int for r in c['threshold_premiums']['rules'])))

    def test_date_label_option_does_not_accept_unknown_definitions(self):
        draft = json.loads((ROOT / 'contract_agent_final_v4/hospital_2.json').read_text())
        draft['definitions']['service_day'] = '09:00 to 08:59'
        with self.assertRaisesRegex(ValueError, 'UNSUPPORTED_SERVICE_DAY'):
            contracts.compile_contract(draft, {}, service_date_as_billing_day=True)

    def test_repeated_services_not_duplicates_without_contract_rule(self):
        c = sample_contract()
        c['invoice_requirements']['duplicate_scope'] = 'not_stated'
        c['threshold_premiums']['rules'] = [{'service_name': 'Alpha Consultation', 'quantity': 3, 'comparison': 'greater_than', 'uplift_percent': 50}]
        a, b = (sample_invoice('a', quantity=2, price=152), sample_invoice('b', quantity=2, price=152))
        originals = copy.deepcopy([a, b])
        rows = rules.audit(c, [a, b], policy={'allocate_later_duplicates': True})
        self.assertEqual([r['flagged'] for r in rows], [0, 0])
        self.assertEqual([r['expected_total_cents'] for r in rows], [304, 304])
        self.assertEqual([a, b], originals)

    def test_h2_assumption_keeps_invalid_dates_and_weekend_checks(self):
        draft = json.loads((ROOT / 'contract_agent_final_v4/hospital_2.json').read_text())
        c = contracts.compile_contract(draft, {}, service_date_as_billing_day=True)
        name = 'Intermittent Ophthalmic Anaesthesia Administration'
        inv = sample_invoice(service=name, price=470340, day='2024-01-07')
        inv['hospital_id'] = 'H2'
        inv['contract_number'] = c['contract_details']['contract_number']
        inv['line_items'][0]['unit_basis_as_billed'] = 'per_procedure'
        original = copy.deepcopy(inv)
        row = rules.audit(c, [inv], extended_contract=draft)[0]
        self.assertEqual((row['flagged'], row['expected_total_cents']), (0, 470340))
        self.assertEqual(inv, original)
        inv['line_items'][0]['service_date'] = '2024-99-01'
        row = rules.audit(c, [inv], policy={'quarantine_invalid_history_dates': True}, extended_contract=draft)[0]
        self.assertIn('malformed_service_date', row['error_category'])
        self.assertIsNone(row['expected_total_cents'])

# Rule dependency bounds

def with_dependency_policy(c):
    c['schema_version'] = '1.1'
    c['matching_guidance'] = {'token_aliases': {}, 'reviewed_descriptions': []}
    c['uncertainty_policy'] = {'mode': 'rule_dependency_bounds'}
    for service in c['services']:
        service['rule_dependencies'] = contracts.dependencies_for(c, service['service_name'])
    return c

class ContractDependencyTests(unittest.TestCase):

    def test_new_abbreviations_accept_only_unique_matches(self):
        c = json.loads((ROOT / 'structured_contracts/hospital_1.json').read_text())
        m = matching.Matcher(c['services'], guidance=c['matching_guidance'])
        self.assertEqual(m.match('Beds Psych Session')['service_name'], 'Bedside Psychiatric Dialysis Session')
        for description, count in [('Emer Ortho', 2), ('Cr Cont Wnd', 2), ('Psychiatric Rehab Programme', 2)]:
            match = m.match(description)
            self.assertIsNone(match['service_name'])
            self.assertEqual(len(match['candidates']), count)
            self.assertTrue(match['candidate_scope_complete_under_description_assumption'])

    def test_conflicting_family_is_history_only_even_with_ai_override(self):
        c = json.loads((ROOT / 'structured_contracts/hospital_1.json').read_text())
        d = 'Extended Haem Anaes Admin'
        m = matching.Matcher(c['services'], {d: 'Advanced Metabolic Anaesthesia Administration'}, c['matching_guidance'])
        match = m.match(d)
        self.assertIsNone(match['service_name'])
        self.assertEqual(match['method'], 'history_only_service_family')
        self.assertTrue(match['outside_catalogue_possible'])
        self.assertEqual([r['service_name'] for r in match['candidates']], ['Advanced Metabolic Anaesthesia Administration'])

    def test_family_restriction_does_not_hide_related_history(self):
        c = with_dependency_policy(sample_contract())
        c['matching_guidance']['history_only_service_families'] = [{'tokens': ['laboratory', 'panel'], 'evidence': 'Reviewed family'}]
        unknown = sample_invoice('b', service='unrecognised specialty Laboratory Panel', patient='patient')
        known = sample_invoice('a', service='Beta Laboratory Panel', price=200)
        known['line_items'][0]['unit_basis_as_billed'] = 'per_test'
        out = rules.audit_baseline(c, [known, unknown])
        self.assertIsNone(out[0]['flagged'])
        self.assertIsNone(out[1]['flagged'])

    def test_billed_price_and_unit_cannot_resolve_omitted_identity(self):
        c = json.loads((ROOT / 'structured_contracts/hospital_1.json').read_text())
        for price, unit in [(28425, 'per_visit'), (8225, 'per_visit'), (1, 'per_item')]:
            inv = sample_invoice(service='Visit Amb Hm', price=price)
            inv['line_items'][0]['unit_basis_as_billed'] = unit
            out = rules.audit_baseline(c, [inv])[0]
            self.assertIsNone(out['flagged'])
            self.assertIsNone(out['expected_total_cents'])

    def test_multiple_history_families_keep_union_of_candidates(self):
        c = with_dependency_policy(sample_contract())
        c['matching_guidance']['history_only_service_families'] = [{'tokens': ['alpha', 'consultation'], 'evidence': 'Family one'}, {'tokens': ['laboratory', 'panel'], 'evidence': 'Family two'}]
        m = matching.Matcher(c['services'], guidance=c['matching_guidance'])
        match = m.match('Unknown Alpha Consultation and Laboratory Panel')
        self.assertIsNone(match['service_name'])
        self.assertEqual({r['service_name'] for r in match['candidates']}, {'Alpha Consultation', 'Beta Laboratory Panel'})

    def test_family_restriction_unblocks_only_unrelated_discount(self):
        c = sample_contract()
        c['volume_discounts']['rules'] = [{'service_name': 'Alpha Consultation', 'quantity': 2, 'comparison': 'greater_than', 'discount_percent': 10}]
        with_dependency_policy(c)
        c['matching_guidance']['history_only_service_families'] = [{'tokens': ['laboratory', 'panel'], 'evidence': 'Reviewed family'}]
        unknown = sample_invoice('b', service='unrecognised specialty Laboratory Panel', quantity=10, day='2024-01-01', patient='other')
        out = rules.audit_baseline(c, [sample_invoice(), unknown])
        self.assertEqual(out[0]['flagged'], 0)
        bounds = out[0]['lines'][0]['utilisation_bounds']
        self.assertEqual((bounds['prior_units_lower'], bounds['prior_units_upper']), (0, 0))
        self.assertIsNone(out[1]['flagged'])

    def test_bounds_report_uncertain_contributors(self):
        c = sample_contract()
        c['volume_discounts']['rules'] = [{'service_name': 'Alpha Consultation', 'quantity': 2, 'comparison': 'greater_than', 'discount_percent': 10}]
        with_dependency_policy(c)
        unknown = sample_invoice('b', service='nonsense xyz', quantity=10, day='2024-01-01', patient='other')
        out = rules.audit_baseline(c, [sample_invoice(), unknown])
        bounds = out[0]['lines'][0]['utilisation_bounds']
        self.assertEqual((bounds['prior_units_lower'], bounds['prior_units_upper']), (0, 10))
        self.assertEqual(bounds['uncertain_contributors'][0]['line_id'], 'b-line')
        self.assertIsNone(out[0]['flagged'])

    def test_ent_maps_without_using_price(self):
        c = json.loads((ROOT / 'structured_contracts/hospital_1.json').read_text())
        m = matching.Matcher(c['services'], guidance=c['matching_guidance'])
        self.assertEqual(m.match('Occupancy Bedside Ent Recovery Room')['service_name'], 'Bedside Otolaryngologic Recovery Room Occupancy')
        self.assertEqual(m.match('Fraction Std Ent Radiotherapy')['service_name'], 'Standard Otolaryngologic Radiotherapy Fraction')

    def test_ai_cannot_override_reviewed_ambiguity(self):
        c = json.loads((ROOT / 'structured_contracts/hospital_1.json').read_text())
        m = matching.Matcher(c['services'], {'Procedure Immun Endosc': 'Ambulatory Immunologic Endoscopic Procedure'}, c['matching_guidance'])
        match = m.match('Procedure Immun Endosc')
        self.assertIsNone(match['service_name'])
        self.assertEqual(len(match['candidates']), 2)

    def test_malformed_unrelated_service_does_not_block(self):
        c = with_dependency_policy(sample_contract())
        unrelated = sample_invoice('b', service='Beta Laboratory Panel', day='broken', price=200)
        unrelated['line_items'][0]['unit_basis_as_billed'] = 'per_test'
        out = rules.audit_baseline(c, [sample_invoice(), unrelated])
        self.assertEqual(out[0]['flagged'], 0)
        self.assertIn('malformed_service_date', out[1]['error_category'])

    def test_malformed_related_service_still_blocks(self):
        c = sample_contract()
        c['bundles']['rules'] = [{'service_a': 'Alpha Consultation', 'service_b': 'Beta Laboratory Panel', 'bundled_rate_a_cents': 90, 'bundled_rate_b_cents': 180}]
        with_dependency_policy(c)
        other = sample_invoice('b', service='Beta Laboratory Panel', day='broken', price=200)
        other['line_items'][0]['unit_basis_as_billed'] = 'per_test'
        self.assertIsNone(rules.audit_baseline(c, [sample_invoice(), other])[0]['expected_total_cents'])

    def test_stale_dependencies_rejected(self):
        c = with_dependency_policy(sample_contract())
        c['services'][0]['rule_dependencies']['same_patient_same_day_services'] = []
        with self.assertRaisesRegex(ValueError, 'Stale'):
            rules.audit_baseline(c, [sample_invoice()])

    def test_exhaustive_candidates_can_exclude_unrelated_discount(self):
        c = sample_contract()
        c['services'].append({'service_name': 'Gamma Laboratory Panel', 'unit_basis': 'per_test', 'base_rate_cents': 300})
        c['volume_discounts']['rules'] = [{'service_name': 'Alpha Consultation', 'quantity': 2, 'comparison': 'greater_than', 'discount_percent': 10}]
        with_dependency_policy(c)
        unknown = sample_invoice('b', service='Laboratory Panel', quantity=10, day='2024-01-01', patient='other')
        results = rules.audit_baseline(c, [sample_invoice(), unknown])
        self.assertEqual(results[0]['flagged'], 0)
        self.assertIsNone(results[1]['flagged'])

    def test_unconstrained_unknown_still_blocks_discount(self):
        c = sample_contract()
        c['volume_discounts']['rules'] = [{'service_name': 'Alpha Consultation', 'quantity': 2, 'comparison': 'greater_than', 'discount_percent': 10}]
        with_dependency_policy(c)
        unknown = sample_invoice('b', service='xyz nonsense', quantity=10, day='2024-01-01', patient='other')
        self.assertIsNone(rules.audit_baseline(c, [sample_invoice(), unknown])[0]['expected_total_cents'])

# Hospital-specific audit policy

class H1AuditPolicyTests(unittest.TestCase):

    def test_only_later_duplicate_flagged_and_order_invariant(self):
        a, b = (sample_invoice('original'), sample_invoice('repeat'))
        b['invoice_date'] = '2024-02-01'
        raw = copy.deepcopy([b, a])
        results = rules.audit_h1(sample_contract(), raw, policy={'allocate_later_duplicates': True})
        self.assertEqual([r['flagged'] for r in results], [1, 0])
        self.assertEqual(results[1]['expected_total_cents'], 101)
        self.assertIsNone(results[0]['expected_total_cents'])
        self.assertEqual(raw, [b, a])
        self.assertEqual([r['flagged'] for r in rules.audit_h1(sample_contract(), [a, b])], [1, 1])

    def test_tied_or_missing_invoice_dates_remain_review(self):
        for later_date in ('2024-01-09', 'invalid'):
            a, b = (sample_invoice('a'), sample_invoice('b'))
            b['invoice_date'] = later_date
            rows = rules.audit_h1(sample_contract(), [a, b], policy={'allocate_later_duplicates': True})
            self.assertTrue(all((r['flagged'] is None for r in rows)))
            self.assertTrue(all((r['expected_total_cents'] is None for r in rows)))

    def test_duplicate_ids_never_allocated(self):
        a, b = (sample_invoice(), sample_invoice())
        b['invoice_date'] = '2024-02-01'
        rows = rules.audit_h1(sample_contract(), [a, b], policy={'allocate_later_duplicates': True})
        self.assertTrue(all(('duplicate_invoice_id' in r['error_category'] for r in rows)))
        self.assertTrue(all((r['expected_total_cents'] is None for r in rows)))

    def test_within_invoice_duplicates_unchanged(self):
        a = sample_invoice()
        line = copy.deepcopy(a['line_items'][0])
        line['line_id'] += '2'
        a['line_items'].append(line)
        a['invoice_total_cents'] *= 2
        row = rules.audit_h1(sample_contract(), [a], policy={'allocate_later_duplicates': True})[0]
        self.assertIn('duplicate_service_same_day', row['error_category'])

    def test_unknown_never_invents_total_and_ignores_prices(self):
        c = json.loads((ROOT / 'structured_contracts/hospital_1.json').read_text())
        matcher = matching.HistoryFallbackMatcher(c['services'], {}, c['matching_guidance'])
        desc = 'Adv Renal Consultation'
        proof = rules.unsupported_service_evidence(desc, c, matcher.match(desc))
        self.assertIsNotNone(proof)
        for price in (1, 14125, 355550):
            row = rules.audit_h1(c, [sample_invoice(service=desc, price=price)], policy={'classify_unsupported_services': True})[0]
            self.assertIn('unknown_service', row['error_category'])
            self.assertIsNone(row['expected_total_cents'])
        for desc in ('Emergency Orthopaedic', 'unrecognised xyz consultation', 'Advanced Neurological Consultation'):
            self.assertIsNone(rules.unsupported_service_evidence(desc, c, matcher.match(desc)))

    def test_quarantine_preserves_bad_date_and_changes_only_conditional_history(self):
        c = sample_contract()
        a, b = (sample_invoice('bad', day='2025-06-31'), sample_invoice('good'))
        raw = copy.deepcopy([a, b])
        strict = rules.audit_h1(c, raw, bounded_history=True)
        relaxed = rules.audit_h1(c, raw, bounded_history=True, policy={'quarantine_invalid_history_dates': True})
        self.assertIsNone(strict[1]['flagged'])
        self.assertEqual(relaxed[1]['flagged'], 0)
        self.assertIn('malformed_service_date', relaxed[0]['error_category'])
        self.assertIsNone(relaxed[0]['expected_total_cents'])
        self.assertTrue(relaxed[1]['lines'][0]['quarantined_history'])
        self.assertEqual(raw, [a, b])

    def test_quarantine_retains_uncertain_volume_units(self):
        c = sample_contract()
        c['volume_discounts']['rules'] = [{'service_name': 'Alpha Consultation', 'quantity': 0, 'comparison': 'greater_than', 'discount_percent': 10}]
        rows = rules.audit_h1(c, [sample_invoice('bad', day='2025-06-31'), sample_invoice('good')], bounded_history=True, policy={'quarantine_invalid_history_dates': True})
        self.assertIsNone(rows[1]['flagged'])
        self.assertTrue(any(('Cumulative usage uncertain' in r for r in rows[1]['review_reasons'])))

# Baseline description mapping and history

class BaselineMappingHistoryTests(unittest.TestCase):

    def test_description_prompt_excludes_price_and_invoice_verdicts(self):
        prompt = agents.PROMPTS['abbreviation_agent']
        self.assertIn('You have no billed prices', prompt)
        self.assertIn('Do not return an invoice verdict or corrected amount', prompt)
        self.assertEqual(ai_client.MODEL, 'google/gemini-3.5-flash-lite')

    def test_mapping_validated_against_catalogue(self):
        c = sample_contract()
        result = rules.audit_baseline(c, [sample_invoice(service='special shorthand')], {'special shorthand': 'Alpha Consultation'})[0]
        self.assertEqual(result['flagged'], 0)
        with self.assertRaises(ValueError):
            rules.audit_baseline(c, [sample_invoice()], {'special shorthand': 'Not a service'})

    def test_future_unmapped_does_not_change_prior_discount(self):
        c = sample_contract()
        c['volume_discounts']['rules'] = [{'service_name': 'Alpha Consultation', 'quantity': 2, 'comparison': 'greater_than', 'discount_percent': 10}]
        rows = [sample_invoice('a', day='2024-01-01'), sample_invoice('b', service='nonsense unknown', day='2024-02-01', patient='other')]
        self.assertIsNone(rules.audit_baseline(c, rows)[0]['expected_total_cents'])
        self.assertEqual(rules.audit_baseline(c, rows, bounded_history=True)[0]['expected_total_cents'], 101)

    def test_unknown_prior_usage_crossing_threshold_abstains(self):
        c = sample_contract()
        c['volume_discounts']['rules'] = [{'service_name': 'Alpha Consultation', 'quantity': 2, 'comparison': 'greater_than', 'discount_percent': 10}]
        rows = [sample_invoice('a', service='nonsense unknown', quantity=3, day='2024-01-01', patient='other'), sample_invoice('b', day='2024-01-02')]
        self.assertIsNone(rules.audit_baseline(c, rows, bounded_history=True)[1]['expected_total_cents'])

    def test_same_discount_at_both_bounds_can_be_calculated(self):
        c = sample_contract()
        c['volume_discounts']['rules'] = [{'service_name': 'Alpha Consultation', 'quantity': 100, 'comparison': 'greater_than', 'discount_percent': 10}]
        rows = [sample_invoice('a', service='nonsense unknown', quantity=3, day='2024-01-01', patient='other'), sample_invoice('b', day='2024-01-02')]
        self.assertEqual(rules.audit_baseline(c, rows, bounded_history=True)[1]['expected_total_cents'], 101)

    def test_patient_same_day_unknown_still_blocks(self):
        out = rules.audit_baseline(sample_contract(), [sample_invoice(), sample_invoice('b', service='unknown nonsense')], bounded_history=True)
        self.assertIsNone(out[0]['expected_total_cents'])

    def test_exclusion_unknown_nearby_still_blocks(self):
        c = sample_contract()
        c['exclusion_windows']['rules'] = [{'excluded_service': 'Alpha Consultation', 'related_service': 'Beta Laboratory Panel', 'window_days': 7}]
        out = rules.audit_baseline(c, [sample_invoice(), sample_invoice('b', service='unknown nonsense', day='2024-01-08')], bounded_history=True)
        self.assertIsNone(out[0]['expected_total_cents'])

# Reason-routed fallback

def reviewed_h1_contract():
    return json.loads((ROOT / 'structured_contracts/hospital_1.json').read_text())

class DescriptionFallbackTests(unittest.TestCase):

    def test_router_selects_only_description_only_abstentions(self):
        row = {'flagged': None, 'review_reasons': ['Unresolved service description on a']}
        self.assertTrue(agents.eligible(row))
        for reason in ['Patient history contains unresolved services', 'Cumulative usage uncertain for a', 'Some new unknown blocker']:
            self.assertFalse(agents.eligible({**row, 'review_reasons': row['review_reasons'] + [reason]}))
        self.assertFalse(agents.eligible({**row, 'flagged': 1}))
        self.assertFalse(agents.eligible({'flagged': None, 'review_reasons': []}))

    def test_tasks_exclude_all_target_invoice_prices(self):
        c = reviewed_h1_contract()
        invs = [sample_invoice('a', service='Visit Amb Hm', price=8225), sample_invoice('b', service='Visit Amb Hm', price=28425, day='2024-01-06'), sample_invoice('c', service='Visit Amb Hm', price=8225, day='2024-01-07', patient='ref')]
        baseline = rules.audit_baseline(c, invs)
        baseline[2]['review_reasons'].append('Cumulative usage uncertain for test')
        selected, tasks = agents.build_tasks(c, invs, baseline)
        self.assertEqual(selected, {0, 1})
        task = next(iter(tasks.values()))
        self.assertEqual({r['invoice_id'] for r in task['reference_observations']}, {'c'})
        original_body = agents.request_body(task, c)
        invs[0]['line_items'][0]['unit_price_cents'] = 777777
        invs[1]['line_items'][0]['unit_price_cents'] = 888888
        _, changed = agents.build_tasks(c, invs, baseline)
        self.assertEqual(original_body, agents.request_body(next(iter(changed.values())), c))

    def test_price_gate_rejects_small_or_mixed_evidence(self):
        c = reviewed_h1_contract()
        a, b = ('Ambulatory Cardiac Home Visit', 'Ambulatory Infectious Home Visit')
        task = {'route': 'missing_detail_agent', 'candidates': [a, b], 'reference_observations': [{'invoice_id': str(i)} for i in range(3)], 'rate_support': {a: 0, b: 1}}
        answer = {'decision': 'inferred_match', 'service_name': b, 'explanation': 'Repeated rate pattern, still inferred', 'token_expansions': []}
        self.assertEqual(agents.validate_raw_answer(answer, task, c)['basis'], 'price_pattern_inference')
        task['rate_support'] = {a: 0.4, b: 0.6}
        with self.assertRaises(ValueError):
            agents.validate_raw_answer(answer, task, c)
        task['rate_support'] = {a: 0, b: 1}
        task['reference_observations'] = task['reference_observations'][:2]
        with self.assertRaises(ValueError):
            agents.validate_raw_answer(answer, task, c)

    def test_ai_expansion_requires_unique_catalogue_match(self):
        c = reviewed_h1_contract()
        task = {'route': 'abbreviation_agent', 'candidates': [s['service_name'] for s in c['services']], 'pattern': 'assisted gi infusion therapy', 'descriptions': ['Asst - Gi Inf Therapy /NG-1139']}
        answer = {'decision': 'text_match', 'service_name': 'Assisted Gastrointestinal Infusion Therapy', 'explanation': 'GI means gastrointestinal', 'token_expansions': [{'token': 'gi', 'expansion': 'gastrointestinal'}]}
        self.assertEqual(agents.validate_raw_answer(answer, task, c)['basis'], 'ai_description_mapping')
        answer['token_expansions'] = []
        with self.assertRaises(ValueError):
            agents.validate_raw_answer(answer, task, c)

    def test_abbreviation_capitalisation_and_known_expansions(self):
        c = reviewed_h1_contract()
        task = {'route': 'abbreviation_agent', 'candidates': [s['service_name'] for s in c['services']], 'pattern': 'assisted gi infusion therapy', 'descriptions': ['Asst - Gi Inf Therapy /NG-1139']}
        answer = {'decision': 'text_match', 'service_name': 'Assisted Gastrointestinal Infusion Therapy', 'explanation': 'GI means gastrointestinal', 'token_expansions': [{'token': 'Asst', 'expansion': 'Assisted'}, {'token': 'Gi', 'expansion': 'Gastrointestinal'}, {'token': 'Inf', 'expansion': 'Infusion'}]}
        self.assertEqual(agents.validate_raw_answer(answer, task, c)['token_expansions'], {'gi': 'gastrointestinal'})
        answer['token_expansions'][0]['expansion'] = 'Ambulatory'
        with self.assertRaises(ValueError):
            agents.validate_raw_answer(answer, task, c)

    def test_offline_cache_miss_never_requests_a_key_or_network(self):
        with tempfile.TemporaryDirectory() as directory:
            api = ai_client.API({}, output_dir=directory, cache_only=True)
            with self.assertRaisesRegex(RuntimeError, 'Offline cache miss'):
                api.call('reasoning', {'model': 'test'})
            self.assertEqual(api.ledger, [])

    def test_price_mapping_cannot_hide_target_wrong_rate(self):
        c = reviewed_h1_contract()
        inv = sample_invoice(service='Visit Amb Hm', price=9999)
        resolution = {(0, 'one-line'): {'service_name': 'Ambulatory Infectious Home Visit', 'basis': 'price_pattern_inference', 'explanation': 'External reference pattern'}}
        result = rules.audit_baseline(c, [inv], line_service_resolutions=resolution)[0]
        self.assertEqual(result['flagged'], 1)
        self.assertEqual(result['expected_total_cents'], 8225)
        self.assertEqual(result['status'], 'inferred_assessment')

    def test_resolution_cannot_escape_scoped_candidates_or_target(self):
        c = reviewed_h1_contract()
        inv = sample_invoice(service='Visit Amb Hm', price=8225)
        p = {'service_name': 'Advanced Neurological Consultation', 'basis': 'price_pattern_inference', 'explanation': 'Invalid service'}
        with self.assertRaises(ValueError):
            rules.audit_baseline(c, [inv], line_service_resolutions={(0, 'one-line'): p})
        with self.assertRaises(ValueError):
            rules.audit_baseline(c, [inv], line_service_resolutions={(99, 'one-line'): p})

    def test_unselected_results_are_preserved_exactly(self):
        old = [{'invoice_id': 'a', 'flagged': None}, {'invoice_id': 'b', 'flagged': None}]
        new = [{'invoice_id': 'a', 'flagged': 0}, {'invoice_id': 'b', 'flagged': 1}]
        merged = agents.merge_selected(old, new, {0})
        self.assertEqual(merged, [new[0], old[1]])

    def test_fingerprint_half_up_discount_rates(self):
        c = reviewed_h1_contract()
        self.assertEqual(agents.possible_rates(c, 'Preoperative Immunologic Endoscopic Procedure'), [121580, 133738, 151975])

# Independent reference gates

class ReferenceGateTests(unittest.TestCase):

    def test_history_matcher_injection_leaves_default_matching_unchanged(self):
        default_matcher = matching.Matcher
        inputs = [sample_invoice(service='Rtn Gi Svc /NG-4995')]
        before = rules.audit_baseline(self.contract, inputs)
        with patch.object(matching, 'HistoryFallbackMatcher', wraps=matching.HistoryFallbackMatcher) as factory:
            rules.audit_baseline(self.contract, inputs, matcher_factory=factory)
        factory.assert_called_once()
        self.assertIs(matching.Matcher, default_matcher)
        self.assertEqual(rules.audit_baseline(self.contract, inputs), before)

    def setUp(self):
        self.original = json.loads((ROOT / 'structured_contracts/hospital_1.json').read_text())
        self.policy = json.loads(FALLBACK_POLICY_PATH.read_text())
        self.contract = agents.derive_contract(self.original, self.policy, history=True)
        self.matcher = matching.HistoryFallbackMatcher(self.contract['services'], guidance=self.contract['matching_guidance'])

    def test_overlay_does_not_mutate_original(self):
        expected = copy.deepcopy(self.original)
        agents.derive_contract(self.original, self.policy, history=True)
        self.assertEqual(self.original, expected)
        self.assertNotIn('gi', self.original['matching_guidance']['token_aliases'])

    def test_abbreviation_rule_generalises_across_word_order(self):
        for desc in ['Rtn Gi Svc /NG-4995', 'Svc GI Routine', 'GI Rtn Service']:
            self.assertEqual(self.matcher.match(desc)['service_name'], 'Routine Gastrointestinal Transfusion Service')

    def test_original_wording_variants_are_not_merged(self):
        self.assertNotEqual(matching.variant_key('Psych Rehab Programme'), matching.variant_key('Psychiatric Rehab Programme /NG-8199'))
        self.assertEqual(matching.variant_key('Psych  Rehab Programme /NG-123'), matching.variant_key('psych-rehab programme'))

    def test_family_restricts_history_without_confirming_service(self):
        match = self.matcher.match('Session Interm Hep Dial')
        self.assertIsNone(match['service_name'])
        self.assertEqual(match['method'], 'history_only_service_family')
        self.assertTrue(all(('Dialysis Session' in r['service_name'] for r in match['candidates'])))
        self.assertTrue(match['outside_catalogue_possible'])

    def test_two_references_only_work_in_experimental_tier(self):
        refs = [sample_invoice(str(i), service='Visit Amb Hm', price=8225) for i in range(2)]
        evidence = agents.References(refs, set(), self.contract)
        match = self.matcher.match('Visit Amb Hm')
        self.assertIsNone(agents.infer('Visit Amb Hm', match, evidence, self.contract, self.policy))
        chosen = agents.infer('Visit Amb Hm', match, evidence, self.contract, self.policy, experimental=True)
        self.assertEqual(chosen['evidence_tier'], 'experimental_low_evidence')
        self.assertEqual(chosen['service_name'], 'Ambulatory Infectious Home Visit')
        self.assertIsNone(agents.infer('Visit Amb Hm', match, evidence, self.contract, self.policy, experimental=True, exclude_invoice_id='0'))

    def test_exact_variants_resolve_mixed_coarse_evidence(self):
        refs = [sample_invoice('a' + str(i), service='Psych Rehab Programme', price=72500) for i in range(3)]
        refs += [sample_invoice('b' + str(i), service='Psychiatric Rehab Programme', price=20300) for i in range(3)]
        evidence = agents.References(refs, set(), self.contract)
        for desc, name in [('Psych Rehab Programme', 'Routine Psychiatric Rehabilitation Programme'), ('Psychiatric Rehab Programme', 'Continuous Psychiatric Rehabilitation Programme')]:
            p = agents.infer(desc, self.matcher.match(desc), evidence, self.contract, self.policy)
            self.assertEqual(p['service_name'], name)
            self.assertEqual(p['reference_scope'], 'original_wording_variant')

    def test_coarse_pool_cannot_override_exact_variant_conflict(self):
        refs = [sample_invoice('a' + str(i), service='Psych Rehab Programme', price=72500) for i in range(8)]
        refs.append(sample_invoice('b', service='Psychiatric Rehab Programme', price=20300))
        evidence = agents.References(refs, set(), self.contract)
        d = 'Psychiatric Rehab Programme'
        self.assertIsNone(agents.infer(d, self.matcher.match(d), evidence, self.contract, self.policy, experimental=True))

    def test_reference_excludes_targets_and_historical_self(self):
        refs = [sample_invoice(str(i), service='Visit Amb Hm', price=8225) for i in range(5)]
        evidence = agents.References(refs, {'0'}, self.contract)
        d = 'Visit Amb Hm'
        p = agents.infer(d, self.matcher.match(d), evidence, self.contract, self.policy, exclude_invoice_id='1')
        self.assertEqual(p['reference_invoice_ids'], ['2', '3', '4'])

    def test_one_invoice_many_lines_cannot_pass_reference_minimum(self):
        rates = {'a': [100], 'b': [200]}
        observations = [{'invoice_id': 'one', 'price_cents': 100} for _ in range(20)]
        self.assertIsNone(agents.winner(observations, rates, self.policy['experimental_low_evidence_inference']))

    def test_family_candidate_never_becomes_price_confirmed_identity(self):
        desc = 'Session Interm Hep Dial'
        refs = [sample_invoice(str(i), service=desc, price=35150) for i in range(5)]
        evidence = agents.References(refs, set(), self.contract)
        self.assertIsNone(agents.infer(desc, self.matcher.match(desc), evidence, self.contract, self.policy, experimental=True))

    def test_history_families_never_displace_existing_text_matches(self):
        descriptions = ['Std Endo Dial Session /NG-7220', 'Routine Card Specimen Analysis', 'Elect Cardiac Nutritional Supp /NG-3162']
        old_matcher = matching.Matcher(self.original['services'], guidance=self.original['matching_guidance'])
        for d in descriptions:
            before = old_matcher.match(d)
            self.assertIsNotNone(before['service_name'])
            self.assertEqual(self.matcher.match(d)['service_name'], before['service_name'])

# Grouped reference folds

class CrossfitTests(unittest.TestCase):

    def setUp(self):
        self.policy = json.loads(FALLBACK_POLICY_PATH.read_text())
        self.policy.pop('experimental_low_evidence_inference')
        original = json.loads((ROOT / 'structured_contracts/hospital_1.json').read_text())
        self.contract = agents.derive_contract(original, self.policy, history=True)

    def test_patient_and_duplicate_id_groups_stay_together(self):
        rows = [sample_invoice('a', patient='p1'), sample_invoice('b', patient='p1'), sample_invoice('b', patient='p2'), sample_invoice('c', patient='p2')]
        self.assertEqual(len(set(agents.assign_folds(rows))), 1)

    def test_assignment_is_order_invariant_and_price_independent(self):
        rows = [sample_invoice(str(i), patient='p' + str(i)) for i in range(20)]
        before = dict(zip([r['invoice_id'] for r in rows], agents.assign_folds(rows)))
        reverse = copy.deepcopy(rows[::-1])
        for r in reverse:
            r['invoice_total_cents'] = -123
            r['line_items'][0]['unit_price_cents'] = 999999
        self.assertEqual(before, dict(zip([r['invoice_id'] for r in reverse], agents.assign_folds(reverse))))

    def test_heldout_prices_cannot_influence_any_history_mapping(self):
        rows = [sample_invoice(str(i), service='Visit Amb Hm', price=8225, patient='p' + str(i)) for i in range(6)]
        assignments = [0, 0, 1, 2, 3, 4]
        a, trace = agents.fold_resolutions(self.contract, rows, self.policy, {}, assignments, 0)
        self.assertTrue(a)
        self.assertTrue(all((not {'0', '1'}.intersection(r['reference_invoice_ids']) for r in trace)))
        changed = copy.deepcopy(rows)
        for r in changed[:2]:
            r['line_items'][0]['unit_price_cents'] = 28425
            r['line_items'][0]['line_total_cents'] = 28425
            r['invoice_total_cents'] = 28425
        b, _ = agents.fold_resolutions(self.contract, changed, self.policy, {}, assignments, 0)
        self.assertEqual(a, b)
        audited, _ = agents.run_fold(self.contract, changed, self.policy, {}, assignments, 0)
        self.assertEqual([r['flagged'] for r in audited], [1, 1])
        self.assertTrue(all((r['expected_total_cents'] == 8225 for r in audited)))

    def test_standard_gate_never_uses_only_two_references(self):
        rows = [sample_invoice(str(i), service='Visit Amb Hm', price=8225, patient='p' + str(i)) for i in range(3)]
        resolutions, _ = agents.fold_resolutions(self.contract, rows, self.policy, {}, [0, 1, 2], 0)
        self.assertEqual(resolutions, {})

# Budget accounting and hospital agents

def ai_request_body():
    return {'model': ai_client.MODEL, 'max_tokens': 100, 'messages': [{'role': 'user', 'content': 'hello'}]}

class BudgetTests(unittest.TestCase):

    def test_blocks_before_network_when_reservation_wont_fit(self):
        with tempfile.TemporaryDirectory() as d:
            api = ai_client.BudgetedAI(d, budget=0.001, transport=lambda _: self.fail('network called'))
            try:
                with self.assertRaises(ai_client.BudgetStop):
                    api.call(ai_request_body())
                self.assertEqual(api.committed, 0)
            finally:
                api.close()

    def test_metered_and_cached_calls_not_charged_twice(self):
        sent = []
        with tempfile.TemporaryDirectory() as d:

            def transport(data):
                sent.append(json.loads(data))
                self.assertTrue(Path(d, 'spending.json').exists())
                return {'usage': {'cost': 0.001}, 'choices': []}
            api = ai_client.BudgetedAI(d, transport=transport)
            try:
                api.call(ai_request_body())
                api.call(ai_request_body())
                self.assertEqual(api.committed, 1000)
                self.assertEqual(len(sent), 1)
                self.assertEqual(sent[0]['provider']['max_price']['completion'], 2.5)
            finally:
                api.close()

    def test_failed_charge_stays_reserved_across_restart_no_retry(self):
        with tempfile.TemporaryDirectory() as d:

            def fail(_):
                raise TimeoutError()
            api = ai_client.BudgetedAI(d, transport=fail)
            with self.assertRaises(RuntimeError):
                api.call(ai_request_body())
            reserved = api.committed
            api.close()
            resumed = ai_client.BudgetedAI(d, transport=lambda _: self.fail('uncertain retry'))
            try:
                with self.assertRaises(ai_client.BudgetStop):
                    resumed.call(ai_request_body())
                self.assertGreater(reserved, 0)
                self.assertEqual(resumed.committed, reserved)
            finally:
                resumed.close()

    def test_missing_cost_not_counted_as_free(self):
        with tempfile.TemporaryDirectory() as d:
            api = ai_client.BudgetedAI(d, transport=lambda _: {'usage': {}})
            try:
                api.call(ai_request_body())
                self.assertGreater(api.committed, 0)
                self.assertEqual(api.summary()['uncertain_requests'], 1)
            finally:
                api.close()

    def test_bad_budget_and_unbounded_request_rejected(self):
        with tempfile.TemporaryDirectory() as d:
            for budget in (0, -1, 2.01, float('nan')):
                with self.assertRaises(ValueError):
                    ai_client.BudgetedAI(d, budget=budget)
            api = ai_client.BudgetedAI(d, transport=lambda _: self.fail('network called'))
            try:
                with self.assertRaises(ValueError):
                    api.call({**ai_request_body(), 'max_tokens': 1000000})
                with self.assertRaises(ValueError):
                    api.call({**ai_request_body(), 'tools': []})
            finally:
                api.close()

    def test_provider_overrun_stops_future_requests(self):
        with tempfile.TemporaryDirectory() as d:
            api = ai_client.BudgetedAI(d, transport=lambda _: {'usage': {'cost': 0.1}})
            try:
                with self.assertRaises(ai_client.BudgetStop):
                    api.call(ai_request_body())
                with self.assertRaises(ai_client.BudgetStop):
                    api.call({**ai_request_body(), 'temperature': 0})
                self.assertEqual(len(api.ledger), 1)
            finally:
                api.close()

class HospitalAgentTests(unittest.TestCase):

    def test_price_batches_cannot_mix_excluded_groups(self):
        tasks = [{'route': 'missing_detail_agent', 'excluded_reference_folds': [0]}, {'route': 'missing_detail_agent', 'excluded_reference_folds': [1]}]
        with self.assertRaisesRegex(ValueError, 'Mixed reference pools'):
            agents.batch_body(tasks, sample_contract())

    def test_prompt_has_own_catalogue_no_invoice_money_or_patient(self):
        c = sample_contract()
        c['matching_guidance'] = {'token_aliases': {}}
        tasks = agents.build_text_tasks(c, [sample_invoice(service='Alph Consult zz')])
        task = next(iter(tasks.values()))
        task['id'] = 'test'
        prompt = json.dumps(agents.batch_body([task], c))
        self.assertIn('Alpha Consultation', prompt)
        self.assertNotIn('unit_price_cents', prompt)
        self.assertNotIn('patient_id', prompt)
        self.assertNotIn('expected_total_cents', prompt)

    def test_invented_service_fails_validation(self):
        c = sample_contract()
        c['matching_guidance'] = {'token_aliases': {}}
        task = next(iter(agents.build_text_tasks(c, [sample_invoice(service='Alph Consult zz')]).values()))
        task['id'] = 'test'

        class API:
            committed = 0

            def call(self, _):
                return {'choices': [{'finish_reason': 'stop', 'message': {'content': json.dumps({'results': [{'task_id': '0', 'decision': 'text_match', 'service_name': 'Invented', 'explanation': 'guess', 'token_expansions': []}]})}}]}
        decisions = {}
        agents.decide_batches([task], c, API(), decisions, 'test')
        self.assertIsNone(decisions['test']['resolution'])

    def test_price_gate_excludes_whole_target_group(self):
        c = sample_contract()
        c['matching_guidance'] = {'token_aliases': {}}
        c['services'][0]['service_name'] = 'Advanced Alpha Consultation'
        c['services'][1]['service_name'] = 'Routine Alpha Consultation'
        raw = [sample_invoice(str(i), service='Alpha Consultation', price=200 if i == 0 else 101, patient=str(i)) for i in range(5)]
        draft = {'extensions': {'rate_versions': [], 'additional_services': [], 'facility_multipliers': [], 'plan_tier_multipliers': []}}
        task = {'pattern': 'alpha consultation', 'descriptions': ['Alpha Consultation'], 'candidates': [s['service_name'] for s in c['services']], 'route': 'missing_detail_agent'}
        result = agents.price_task(task, c, draft, raw, [0, 1, 2, 3, 4], (0,), {str(i): 1 for i in range(5)})
        self.assertEqual(result['proposed_service'], 'Advanced Alpha Consultation')
        self.assertNotIn('0', [o['invoice_id'] for o in result['reference_observations']])
        self.assertIsNone(agents.price_task(task, c, draft, raw, [0, 1, 2, 3, 4], (0, 1, 2), {str(i): 1 for i in range(5)}))

    def test_contextual_fingerprint_uses_amendment_and_facility(self):
        c = sample_contract()
        d = {'extensions': {'rate_versions': [{'service_name': 'Alpha Consultation', 'old_rate_cents': 101, 'new_rate_cents': 120, 'effective_from': '2025-01-01', 'applies_by': 'service_date'}], 'additional_services': [], 'facility_multipliers': [{'service_name': 'Alpha Consultation', 'multipliers': {'F-MAIN': {'numerator': 2, 'denominator': 1}}}], 'plan_tier_multipliers': []}}
        inv = sample_invoice(day='2025-01-05')
        inv['invoice_date'] = '2025-01-09'
        self.assertEqual(agents.contextual_rates(c, d, 'Alpha Consultation', inv['line_items'][0], inv), [240])

# Contract blockers and candidate scopes

class FourBlockerTests(unittest.TestCase):

    def test_export_keeps_abstentions_and_duplicate_records(self):
        row = {'hospital_id': 'H2', 'record_index': 0, 'invoice_id': 'a',
               'flagged': None, 'record_identity_ambiguous': False,
               'error_category': '', 'expected_total_cents': None,
               'billed_total_cents': 100, 'review_reasons': ['Unknown service']}
        duplicate = {**row, 'record_index': 1, 'flagged': 1, 'record_identity_ambiguous': True}
        output = reporting.all_record_export([row, duplicate])
        self.assertEqual([record['record_index'] for record in output['rows']], [0, 1])
        self.assertTrue(all(record['status'] == 'not answered' and record['flagged'] == 'not answered'
                            for record in output['rows']))

    def setup_task(self):
        c = sample_contract()
        c['matching_guidance'] = {'token_aliases': {}}
        c['services'][0]['service_name'] = 'Outpatient Renal Specimen Analysis'
        task = {'route': 'abbreviation_agent', 'descriptions': ['Outpatient Ren Spcm Anly'], 'pattern': 'anly outpatient renal spcm', 'candidates': [s['service_name'] for s in c['services']]}
        answer = {'decision': 'text_match', 'service_name': c['services'][0]['service_name'], 'explanation': 'Expanded abbreviations', 'token_expansions': [{'token': a, 'expansion': b} for a, b in [('outpatient', 'outpatient'), ('ren', 'renal'), ('spcm', 'specimen'), ('anly', 'analysis'), ('spcm', 'specimen')]]}
        return (c, task, answer)

    def test_identity_and_duplicate_expansions_are_harmless(self):
        c, t, a = self.setup_task()
        self.assertEqual(agents.validate_answer(a, t, c)['service_name'], c['services'][0]['service_name'])

    def test_existing_meaning_cannot_be_overwritten(self):
        c, t, a = self.setup_task()
        a['token_expansions'].append({'token': 'ren', 'expansion': 'specimen'})
        with self.assertRaises(ValueError):
            agents.validate_answer(a, t, c)

    def test_ambiguous_identity_can_have_bounded_candidates(self):
        c, t, a = self.setup_task()
        c['services'][1]['service_name'] = 'Routine Renal Specimen Analysis'
        t.update(descriptions=['Ren Spcm Anly'], pattern='anly renal spcm')
        a.update(decision='unresolved', service_name=None)
        a['token_expansions'] = [e for e in a['token_expansions'] if e['token'] != 'outpatient']
        scope = agents.candidate_scope(a, t, c)
        self.assertEqual(len(scope['candidate_services']), 2)
        self.assertIsNone(agents.validate_answer(a, t, c))

    def test_compound_source_unit_not_guessed(self):
        evidence = [{'id': 's:1', 'text': 'Alpha Service per hour, per item GBP 10.00'}]
        answer = {'decision': 'resolved', 'unit_basis': 'per_hour', 'source_ids': ['s:1'], 'explanation': 'Guess'}
        with self.assertRaises(ValueError):
            contracts.validate_unit(answer, evidence, 'Alpha Service')
        evidence[0]['text'] = 'Alpha Service per hour GBP 10.00'
        self.assertEqual(contracts.validate_unit(answer, evidence, 'Alpha Service'), 'per_hour')
        answer['source_ids'] = ['invented']
        with self.assertRaises(ValueError):
            contracts.validate_unit(answer, evidence, 'Alpha Service')

    def test_graph_retrieves_source_and_definition(self):
        draft = {'hospital_id': 'H99', 'services': [{'service_name': 'Alpha Service'}], 'source_sections': [{'document_id': 'sample.txt', 'sha256': 'x', 'text': 'Unit means one billable unit.\nAlpha Service per hour GBP 10.00\nOther service.'}]}
        graph = contracts.ClauseGraph(draft)
        self.assertIn('sample.txt:2', graph.edges['Alpha Service'])
        self.assertTrue(any(('Unit means' in n['text'] for n in graph.retrieve('Alpha Service'))))

    def test_no_fictional_same_day_dependency(self):
        c = sample_contract()
        c['invoice_requirements']['duplicate_scope'] = 'not_stated'
        for s in c['services']:
            s['rule_dependencies'] = {'same_patient_same_day_services': [s['service_name']]}
        refined = contracts.refine_dependencies(c)
        self.assertEqual(refined['services'][0]['rule_dependencies']['same_patient_same_day_services'], [])
        self.assertNotEqual(c, refined)

    def test_unrelated_scoped_history_does_not_block_discount(self):
        c = sample_contract()
        c['schema_version'] = '1.1'
        c['uncertainty_policy'] = {'mode': 'rule_dependency_bounds', 'improved_usage_bounds': True}
        c['matching_guidance'] = {'token_aliases': {}, 'reviewed_descriptions': []}
        c['volume_discounts']['rules'] = [{'service_name': 'Alpha Consultation', 'quantity': 2, 'comparison': 'greater_than', 'discount_percent': 10}]
        a = sample_invoice('a', service='Beta unknown', quantity=10, day='2024-01-01')
        a['line_items'][0]['unit_basis_as_billed'] = 'per_test'
        b = sample_invoice('b', patient='other', day='2024-01-02')
        scope = {'candidate_services': ['Beta Laboratory Panel'], 'basis': 'exhaustive_catalogue_after_validated_AI_expansions'}
        rows = rules.audit(contracts.refine_dependencies(c), [a, b], line_candidate_scopes={(0, 'a-line'): scope})
        self.assertIsNone(rows[0]['flagged'])
        self.assertEqual(rows[1]['flagged'], 0)

    def test_wrong_units_cannot_earn_discount(self):
        c = sample_contract()
        c['schema_version'] = '1.1'
        c['uncertainty_policy'] = {'mode': 'rule_dependency_bounds', 'improved_usage_bounds': True}
        c['matching_guidance'] = {'token_aliases': {}, 'reviewed_descriptions': []}
        c['volume_discounts']['rules'] = [{'service_name': 'Alpha Consultation', 'quantity': 2, 'comparison': 'greater_than', 'discount_percent': 10}]
        a = sample_invoice('a', quantity=10, day='2024-01-01')
        a['line_items'][0]['unit_basis_as_billed'] = 'per_hour'
        b = sample_invoice('b', price=91, day='2024-01-02', patient='other')
        row = rules.audit(contracts.refine_dependencies(c), [a, b])[1]
        bounds = row['lines'][0]['utilisation_bounds']
        self.assertEqual(bounds['prior_units_lower'], 0)
        self.assertTrue(bounds['upper_unbounded'])
        self.assertIsNone(row['flagged'])

# Description validation and verified replay

def description_sample(description, names, expansions, decision='text_match', chosen=0):
    """Make a contract, description task, and proposed response for gate tests."""
    c = sample_contract()
    c['matching_guidance'] = {'token_aliases': {}}
    c['services'] = [{**c['services'][0], 'service_name': name} for name in names]
    t = {'id': 'test', 'route': 'abbreviation_agent', 'descriptions': [description],
         'pattern': description.lower(), 'candidates': names}
    a = {
        'decision': decision, 'service_name': names[chosen] if chosen is not None else None,
        'explanation': 'Text-only abbreviation proposal.',
        'token_expansions': [
            {'token': token, 'expansion': expansion} for token, expansion in expansions
        ],
    }
    return (c, t, a)

class DescriptionValidationTests(unittest.TestCase):

    def test_extra_words_never_introduce_missing_specificity(self):
        c, t, a = description_sample('Elect Metab Disp', ['Elective Metabolic Pharmaceutical Dispensing'], [('elect', 'elective'), ('metab', 'metabolic'), ('disp', 'pharmaceutical dispensing')])
        result = agents.validate_answer(a, t, c, repair=True)
        self.assertEqual(result['token_expansions']['disp'], 'dispensing')
        self.assertNotIn('pharmaceutical', result['token_expansions'].values())
        with self.assertRaises(ValueError):
            agents.validate_answer(a, t, c)

    def test_conflicting_qualifier_cannot_break_real_service_tie(self):
        c, t, a = description_sample('Obs Derm Nursing', ['Inpatient Dermatologic Nursing Observation', 'Specialist Dermatologic Nursing Observation'], [('obs', 'inpatient'), ('obs', 'observation')])
        with self.assertRaisesRegex(ValueError, 'MISSING_SERVICE_QUALIFIER'):
            agents.validate_answer(a, t, c, repair=True)
        scope = agents.candidate_scope(a, t, c, repair=True)
        self.assertEqual(scope['candidate_services'], sorted(t['candidates']))
        self.assertEqual(scope['token_expansions']['obs'], 'observation')

    def test_known_alias_is_preserved_not_overwritten(self):
        c, t, a = description_sample('Conf Urol Cs', ['Focused Urologic Case Conference'], [('conf', 'conference'), ('urol', 'urologic'), ('cs', 'conference')])
        result = agents.validate_answer(a, t, c, repair=True)
        self.assertNotIn('cs', result['token_expansions'])
        self.assertTrue(any(('Preserved existing token meaning' in n for n in result['normalization_notes'])))

    def test_prefix_recovery_can_scope_missing_response_expansion(self):
        c, t, a = description_sample('Analysis Card Specimen', ['Routine Cardiac Specimen Analysis', 'Preoperative Cardiac Specimen Analysis'], [], chosen=None)
        scope = agents.candidate_scope(a, t, c, repair=True)
        self.assertEqual(scope['token_expansions']['card'], 'cardiac')
        self.assertEqual(len(scope['candidate_services']), 2)

    def test_inferred_match_is_text_only_only_when_independently_unique(self):
        c, t, a = description_sample('Elect Gastro Nurs', ['Elective Gastrointestinal Nursing Observation'], [('elect', 'elective'), ('gastro', 'gastrointestinal'), ('nurs', 'nursing')], decision='inferred_match')
        result = agents.validate_answer(a, t, c, repair=True)
        self.assertEqual(result['basis'], 'ai_description_mapping')
        self.assertTrue(any(('Reclassified inferred_match' in note for note in result['normalization_notes'])))
        c['services'].append({**c['services'][0], 'service_name': 'Elective Gastrointestinal Nursing Home Visit'})
        with self.assertRaisesRegex(ValueError, 'MISSING_SERVICE_QUALIFIER'):
            agents.validate_answer(a, t, c, repair=True)

    def test_phrase_expansion_requires_source_and_aligned_words(self):
        c, t, a = description_sample('Routine Card Anaes Admin', ['Routine Cardiac Anaesthesia Administration'], [('card', 'cardiac'), ('anaes admin', 'anaesthesia administration')])
        self.assertEqual(agents.validate_answer(a, t, c, repair=True)['token_expansions']['anaes'], 'anaesthesia')
        t['descriptions'] = ['Routine Card Anaes Mystery']
        with self.assertRaisesRegex(ValueError, 'NO_EXHAUSTIVE_CATALOGUE_MATCH'):
            agents.validate_answer(a, t, c, repair=True)

    def test_consonant_contractions_are_preserved(self):
        c, t, a = description_sample('Ren Pnl Anly', ['Renal Panel Analysis'], [('pnl', 'panel'), ('anly', 'analysis')])
        self.assertEqual(agents.validate_answer(a, t, c, repair=True)['service_name'], 'Renal Panel Analysis')

    def test_unrelated_expansion_cannot_add_a_qualifier(self):
        c, t, a = description_sample('Obs Nursing', ['Inpatient Nursing'], [('obs', 'inpatient')])
        with self.assertRaisesRegex(ValueError, 'NO_EXHAUSTIVE_CATALOGUE_MATCH'):
            agents.validate_answer(a, t, c, repair=True)

    def test_ambiguous_prefix_and_catalogue_words_are_not_rewritten(self):
        c, t, a = description_sample('Obs Card', ['Obstetric Cardiac Consultation', 'Observation Cardiac Panel'], [])
        additions, _ = agents.repaired_expansions(a, t, c)
        self.assertNotIn('obs', additions)
        self.assertEqual(additions['card'], 'cardiac')

    def test_model_abstention_recovers_only_independently_unique_text(self):
        c, t, a = description_sample('Card Nurs', ['Cardiac Nursing Observation'], [], decision='unresolved', chosen=None)
        result = agents.validate_answer(a, t, c, repair=True)
        self.assertEqual(result['service_name'], 'Cardiac Nursing Observation')
        self.assertEqual(result['original_model_decision'], 'unresolved')
        self.assertTrue(any(('Recovered model abstention' in note for note in result['normalization_notes'])))
        original = copy.deepcopy(c)
        agents.candidate_scope(a, t, c, repair=True)
        self.assertEqual(c, original)
        c['services'].append({**c['services'][0], 'service_name': 'Cardiac Nursing Home Visit'})
        self.assertIsNone(agents.validate_answer(a, t, c, repair=True))

    def test_rejection_feedback_preserves_specific_reason(self):
        c, t, a = description_sample('Obs Derm Nursing', ['Inpatient Dermatologic Nursing Observation', 'Specialist Dermatologic Nursing Observation'], [('obs', 'observation')])

        class API:
            committed = 0

            def call(self, _):
                return {'choices': [{'finish_reason': 'stop', 'message': {'content': json.dumps({'results': [{'task_id': '0', **a}]})}}]}
        decisions = {}
        agents.decide_batches([t], c, API(), decisions, 'synthetic', improved=True)
        self.assertIn('MISSING_SERVICE_QUALIFIER', decisions['test']['reason'])
        self.assertIsNone(decisions['test']['resolution'])

    def test_price_feedback_keeps_three_invoice_gate(self):
        c = sample_contract()
        c['matching_guidance'] = {'token_aliases': {}}
        c['services'][0]['service_name'] = 'Advanced Alpha Consultation'
        c['services'][1]['service_name'] = 'Routine Alpha Consultation'
        raw = [sample_invoice(str(i), service='Alpha Consultation', price=101, patient=str(i)) for i in range(4)]
        t = {'pattern': 'alpha consultation', 'descriptions': ['Alpha Consultation'], 'candidates': [s['service_name'] for s in c['services']], 'route': 'missing_detail_agent'}
        draft = {'extensions': {'rate_versions': [], 'additional_services': [], 'facility_multipliers': [], 'plan_tier_multipliers': []}}
        feedback = {}
        self.assertIsNone(agents.price_task(t, c, draft, raw, [0, 1, 2, 3], (0, 1), {str(i): 1 for i in range(4)}, feedback=feedback))
        self.assertEqual(feedback['reason'], 'STANDARD_REFERENCE_GATE_NOT_MET')
        self.assertTrue(all((s['reference_invoice_count'] == 2 for s in feedback['reference_scopes'])))
        self.assertTrue(all(('INSUFFICIENT_INDEPENDENT_REFERENCES' in s['reason'] for s in feedback['reference_scopes'])))

class VerifiedTaskReplayTests(unittest.TestCase):

    def fixture(self, directory, answer_change=None):
        root = Path(directory)
        cache_dir = root / 'cache'
        cache_dir.mkdir()
        c, t, a = description_sample('Card Nurs', ['Cardiac Nursing Observation'], [('card', 'cardiac'), ('nurs', 'nursing')])
        t.update(id='H99:text:test', targets=[{'record_index': 0, 'line_id': 'line1'}])
        if answer_change:
            a.update(answer_change)
        draft = {'hospital_id': 'H99', 'source_sections': [{'document_id': 'test.txt', 'sha256': 'source-hash', 'text': 'Original contract evidence'}]}
        files = {'H99.agent_tasks.json': [t], 'H99.agent_decisions.json': {t['id']: {'answer': a, 'status': 'accepted', 'resolution': {'service_name': 'DO NOT IMPORT THIS STORED RESOLUTION'}}}, 'hospital_99.runtime.json': c, 'H99.reviewed_contract.json': draft}
        for name, value in files.items():
            (root / name).write_text(json.dumps(value))
        request = {**agents.batch_body([t], c), 'provider': {'max_price': {'prompt': 0.3, 'completion': 2.5, 'request': 0}, 'allow_fallbacks': False, 'require_parameters': True}}
        digest = hashlib.sha256(json.dumps(request, sort_keys=True).encode()).hexdigest()
        response = {'choices': [{'finish_reason': 'stop', 'message': {'content': json.dumps({'results': [{'task_id': '0', **a}]})}}]}
        (cache_dir / (digest + '.json')).write_text(json.dumps(response))
        return (c, t, a, draft, SimpleNamespace(cache=cache_dir))

    def test_raw_answers_verified_and_revalidated_without_api(self):
        with tempfile.TemporaryDirectory() as d:
            c, t, a, draft, api = self.fixture(d)
            replay, context = agents.load_replay_answers(d, 'H99', c, draft, api)
            self.assertEqual(context['verified_cached_tasks'], 1)
            api.call = lambda _: self.fail('A verified replay must not call the API')
            decisions = {}
            agents.decide_batches([t], c, api, decisions, 'replay', improved=True, replay_answers=replay)
            self.assertEqual(decisions[t['id']]['resolution']['service_name'], 'Cardiac Nursing Observation')
            self.assertIn('api_request_id', decisions[t['id']]['raw_answer_replay'])

    def test_changed_contract_and_source_reject_replay(self):
        with tempfile.TemporaryDirectory() as d:
            c, t, a, draft, api = self.fixture(d)
            changed = copy.deepcopy(c)
            changed['services'][0]['base_rate_cents'] += 1
            with self.assertRaisesRegex(ValueError, 'contract evidence changed'):
                agents.load_replay_answers(d, 'H99', changed, draft, api)
            changed_draft = copy.deepcopy(draft)
            changed_draft['source_sections'][0]['text'] = 'Changed clause'
            with self.assertRaisesRegex(ValueError, 'source-clause provenance mismatch'):
                agents.load_replay_answers(d, 'H99', c, changed_draft, api)
            with self.assertRaisesRegex(ValueError, 'hospital provenance mismatch'):
                agents.load_replay_answers(d, 'H99', c, {**draft, 'hospital_id': 'H98'}, api)

    def test_current_validator_rejects_formerly_accepted_bad_answer(self):
        with tempfile.TemporaryDirectory() as d:
            c, t, a, draft, api = self.fixture(d, {'service_name': 'Invented Service'})
            replay, _ = agents.load_replay_answers(d, 'H99', c, draft, api)
            api.call = lambda _: self.fail('A rejected replay must not be retried automatically')
            decisions = {}
            agents.decide_batches([t], c, api, decisions, 'replay', improved=True, replay_answers=replay)
            self.assertEqual(decisions[t['id']]['status'], 'rejected')
            self.assertIsNone(decisions[t['id']]['resolution'])

    def test_exact_reference_observations_support_and_folds_are_required(self):
        c = sample_contract()
        c['matching_guidance'] = {'token_aliases': {}}
        t = {'route': 'missing_detail_agent', 'pattern': 'alpha consultation', 'descriptions': ['Alpha Consultation'], 'candidates': ['Advanced Alpha Consultation', 'Routine Alpha Consultation'], 'excluded_reference_folds': [0, 1], 'reference_observations': [{'invoice_id': 'ref2', 'line_id': 'line2', 'support': {'Advanced Alpha Consultation': True}}], 'rate_support': {'Advanced Alpha Consultation': 1, 'Routine Alpha Consultation': 0}, 'proposed_service': 'Advanced Alpha Consultation', 'reference_scope': 'original_wording_variant'}
        key = agents._task_replay_key(t, c)
        for key_name, value in [('excluded_reference_folds', [0, 2]), ('rate_support', {'Advanced Alpha Consultation': 0.8, 'Routine Alpha Consultation': 0}), ('reference_observations', [{'invoice_id': 'OTHER', 'line_id': 'line2', 'support': {'Advanced Alpha Consultation': True}}])]:
            self.assertNotEqual(key, agents._task_replay_key({**t, key_name: value}, c))

    def test_unverified_decision_file_cannot_seed_replay(self):
        with tempfile.TemporaryDirectory() as d:
            c, t, a, draft, api = self.fixture(d)
            path = Path(d) / 'H99.agent_decisions.json'
            data = json.loads(path.read_text())
            data[t['id']]['answer']['explanation'] = 'Changed after the cached API response'
            path.write_text(json.dumps(data))
            replay, _ = agents.load_replay_answers(d, 'H99', c, draft, api)
            self.assertEqual(replay, {})

# Patient history and volume discounts

def history_contract():
    """Enable dependency bounds without inventing a duplicate-service clause."""
    c = sample_contract()
    c['schema_version'] = '1.1'
    c['uncertainty_policy'] = {'mode': 'rule_dependency_bounds', 'improved_usage_bounds': True}
    c['matching_guidance'] = {'token_aliases': {}, 'reviewed_descriptions': []}
    c['invoice_requirements']['duplicate_scope'] = 'not_stated'
    return c

def set_history_discount(c, *levels):
    c['volume_discounts']['rules'] = [
        {'service_name': 'Alpha Consultation', 'quantity': quantity,
         'comparison': 'greater_than', 'discount_percent': percent}
        for quantity, percent in levels
    ]

def audit_history(c, rows, scopes=None, policy=None):
    return rules.audit(contracts.refine_dependencies(c), rows, line_candidate_scopes=scopes, policy=policy)

def history_scope_for(index, identifier, service='Alpha Consultation'):
    return {(index, identifier + '-line'): {
        'candidate_services': [service],
        'basis': 'exhaustive_catalogue_after_validated_AI_expansions',
    }}

def is_history_blocked(row):
    return any(('Patient history' in reason for reason in row['review_reasons']))

class HistoryVolumeTests(unittest.TestCase):

    def test_reference_quantity_changes_only_discount_history_not_wrong_unit(self):
        c = history_contract()
        set_history_discount(c, (2, 10))
        prior = sample_invoice('prior', quantity=3, day='2024-01-04')
        prior['line_items'][0]['unit_basis_as_billed'] = 'per_item'
        target = sample_invoice('target', price=91, day='2024-01-05')
        self.assertIsNone(audit_history(c, [prior, target])[1]['flagged'])
        estimate = {'service_name': 'Alpha Consultation', 'quantity': 3,
                    'reference_invoice_ids': ['ref1', 'ref2', 'ref3'], 'assumption': 'Unit label only is wrong'}
        rows = rules.audit(contracts.refine_dependencies(c), [prior, target],
                           historical_unit_estimates={(0, 'prior-line'): estimate})
        self.assertEqual(rows[1]['flagged'], 0)
        self.assertEqual(rows[1]['lines'][0]['utilisation_bounds']['prior_units_lower'], 3)
        self.assertIn('wrong_unit_basis', rows[0]['error_category'])
        self.assertEqual(prior['line_items'][0]['unit_basis_as_billed'], 'per_item')
        self.assertTrue(rows[1]['lines'][0]['historical_unit_assumptions'])
        for bad in ({**estimate, 'quantity': 4}, {**estimate, 'reference_invoice_ids': ['prior', 'ref2', 'ref3']}):
            with self.assertRaises(ValueError):
                rules.audit(contracts.refine_dependencies(c), [prior, target], historical_unit_estimates={(0, 'prior-line'): bad})

    def test_amended_rate_keeps_prior_year_contract_usage(self):
        c = history_contract()
        c['contract_details']['effective_to'] = '2025-12-31'
        set_history_discount(c, (100, 10))
        prior = sample_invoice('prior-year', quantity=101, day='2024-12-31')
        prior['invoice_date'] = '2024-12-31'
        current = sample_invoice('new-year', price=108, day='2025-01-01')
        current['invoice_date'] = '2025-01-02'
        draft = {'extensions': {'additional_services': [], 'facility_multipliers': [], 'plan_tier_multipliers': [],
                 'rate_versions': [{'service_name': 'Alpha Consultation', 'old_rate_cents': 101,
                                    'new_rate_cents': 120, 'effective_from': '2025-01-01', 'applies_by': 'service_date'}]}}
        result = rules.audit(contracts.refine_dependencies(c), [prior, current], extended_contract=draft)[1]
        self.assertEqual(result['flagged'], 0)
        self.assertEqual(result['lines'][0]['utilisation_bounds']['prior_units_lower'], 101)
        self.assertEqual(result['expected_total_cents'], 108)

    def test_missing_patient_does_not_erase_confirmed_contract_usage(self):
        c = history_contract()
        set_history_discount(c, (2, 10))
        prior = sample_invoice('a', quantity=3, day='2024-01-01', patient=None)
        target = sample_invoice('b', day='2024-01-02', price=91)
        result = audit_history(c, [prior, target])[1]
        self.assertEqual(result['flagged'], 0)
        self.assertEqual(result['lines'][0]['utilisation_bounds']['prior_units_lower'], 3)
        self.assertEqual(result['lines'][0]['utilisation_bounds']['prior_units_upper'], 3)

    def test_current_line_does_not_earn_its_own_discount(self):
        c = history_contract()
        set_history_discount(c, (2, 10))
        target = sample_invoice('b', quantity=20)
        target['line_items'][0]['unit_basis_as_billed'] = 'per_hour'
        result = audit_history(c, [target])[0]
        bounds = result['lines'][0]['utilisation_bounds']
        self.assertEqual((bounds['prior_units_lower'], bounds['prior_units_upper']), (0, 0))
        self.assertFalse(any(('Cumulative usage' in reason for reason in result['review_reasons'])))
        self.assertIn('wrong_unit_basis', result['error_category'])

    def test_invalid_prior_identifier_has_no_same_day_position(self):
        for invalid_id in (None, '', '   ', 7, [], {}):
            with self.subTest(line_id=invalid_id):
                c = history_contract()
                set_history_discount(c, (2, 10))
                prior = sample_invoice('a', quantity=3, patient='other')
                prior['line_items'][0]['line_id'] = invalid_id
                result = audit_history(c, [prior, sample_invoice('b')])[1]
                bounds = result['lines'][0]['utilisation_bounds']
                self.assertEqual((bounds['prior_units_lower'], bounds['prior_units_upper']), (0, 3))
                self.assertIsNone(result['flagged'])
                self.assertNotIn('unit_price_mismatch', result['error_category'])

    def test_missing_prior_identifier_key_is_optional_same_day(self):
        c = history_contract()
        set_history_discount(c, (2, 10))
        prior = sample_invoice('a', quantity=3, patient='other')
        del prior['line_items'][0]['line_id']
        result = audit_history(c, [prior, sample_invoice('b')])[1]
        bounds = result['lines'][0]['utilisation_bounds']
        self.assertEqual((bounds['prior_units_lower'], bounds['prior_units_upper']), (0, 3))
        self.assertIsNone(result['flagged'])

    def test_invalid_target_identifier_never_counts_itself(self):
        for value in ('MISSING_KEY', None, '', ' ', 7, [], {}):
            with self.subTest(line_id=value):
                c = history_contract()
                set_history_discount(c, (2, 10))
                target = sample_invoice('b', quantity=3)
                if value == 'MISSING_KEY':
                    del target['line_items'][0]['line_id']
                else:
                    target['line_items'][0]['line_id'] = value
                result = audit_history(c, [target])[0]
                bounds = result['lines'][0]['utilisation_bounds']
                self.assertEqual((bounds['prior_units_lower'], bounds['prior_units_upper']), (0, 0))
                self.assertNotIn('unit_price_mismatch', result['error_category'])

    def test_missing_target_identifier_makes_other_same_day_usage_optional(self):
        c = history_contract()
        set_history_discount(c, (2, 10))
        target = sample_invoice('b')
        del target['line_items'][0]['line_id']
        prior = sample_invoice('a', quantity=3, patient='other')
        result = audit_history(c, [prior, target])[1]
        bounds = result['lines'][0]['utilisation_bounds']
        self.assertEqual((bounds['prior_units_lower'], bounds['prior_units_upper']), (0, 3))
        self.assertNotIn('unit_price_mismatch', result['error_category'])

    def test_earlier_date_proves_order_despite_missing_identifiers(self):
        c = history_contract()
        set_history_discount(c, (2, 10))
        prior = sample_invoice('a', quantity=3, day='2024-01-01', patient='other')
        prior['line_items'][0]['line_id'] = None
        target = sample_invoice('b', day='2024-01-02', price=91)
        result = audit_history(c, [prior, target])[1]
        bounds = result['lines'][0]['utilisation_bounds']
        self.assertEqual((bounds['prior_units_lower'], bounds['prior_units_upper']), (3, 3))
        self.assertEqual(result['flagged'], 0)

    def test_duplicate_invoice_records_cannot_prove_earned_usage(self):
        for price in (91, 101):
            with self.subTest(price=price):
                c = history_contract()
                set_history_discount(c, (5, 10))
                first = sample_invoice('same', quantity=3, day='2024-01-01', patient='other')
                second = sample_invoice('same', quantity=3, day='2024-01-01', patient='other')
                second['line_items'][0]['line_id'] = 'different-line'
                result = audit_history(c, [first, second, sample_invoice('target', day='2024-01-02', price=price)])[2]
                bounds = result['lines'][0]['utilisation_bounds']
                self.assertEqual((bounds['prior_units_lower'], bounds['prior_units_upper']), (0, 6))
                self.assertIsNone(result['flagged'])
                self.assertNotIn('unit_price_mismatch', result['error_category'])

    def test_out_of_term_event_cannot_earn_contract_term_discount(self):
        c = history_contract()
        set_history_discount(c, (2, 10))
        prior = sample_invoice('a', quantity=20, day='2023-12-30')
        prior['line_items'][0]['unit_basis_as_billed'] = 'per_hour'
        target = sample_invoice('b', day='2024-01-02')
        result = audit_history(c, [prior, target])[1]
        self.assertEqual(result['flagged'], 0)
        self.assertEqual(result['lines'][0]['utilisation_bounds']['prior_units_upper'], 0)

    def test_out_of_term_confirmed_units_not_in_lower_bound(self):
        c = history_contract()
        set_history_discount(c, (2, 10))
        prior = sample_invoice('a', quantity=20, day='2023-12-30')
        result = audit_history(c, [prior, sample_invoice('b', day='2024-01-02')])[1]
        self.assertEqual(result['flagged'], 0)
        self.assertEqual(result['lines'][0]['utilisation_bounds']['prior_units_lower'], 0)

    def test_tied_sort_keys_only_block_if_discount_outcome_changes(self):
        c = history_contract()
        set_history_discount(c, (2, 10))
        first, second = (sample_invoice('a'), sample_invoice('b', patient='other'))
        for row in (first, second):
            row['line_items'][0]['line_id'] = 'same-key'
        results = audit_history(c, [first, second])
        self.assertEqual([r['flagged'] for r in results], [0, 0])
        for row in results:
            bounds = row['lines'][0]['utilisation_bounds']
            self.assertEqual((bounds['prior_units_lower'], bounds['prior_units_upper']), (0, 1))

    def test_tied_sort_keys_remain_uncertain_across_threshold(self):
        c = history_contract()
        set_history_discount(c, (2, 10))
        first = sample_invoice('a', quantity=3)
        second = sample_invoice('b', quantity=3, patient='other')
        for row in (first, second):
            row['line_items'][0]['line_id'] = 'same-key'
        result = audit_history(c, [first, second])[1]
        self.assertIsNone(result['flagged'])
        self.assertEqual(result['lines'][0]['possible_unit_prices_cents'], [91, 101])

    def test_unknown_conversion_remains_unbounded(self):
        c = history_contract()
        set_history_discount(c, (2, 10))
        prior = sample_invoice('a', quantity=20, day='2024-01-01')
        prior['line_items'][0]['unit_basis_as_billed'] = 'per_hour'
        result = audit_history(c, [prior, sample_invoice('b', day='2024-01-02')])[1]
        self.assertIsNone(result['flagged'])
        self.assertTrue(result['lines'][0]['utilisation_bounds']['upper_unbounded'])

    def test_quarantine_keeps_unknown_date_in_volume_upper_bound(self):
        c = history_contract()
        set_history_discount(c, (2, 10))
        prior = sample_invoice('a', quantity=3, day='2024-99-01')
        result = audit_history(c, [prior, sample_invoice('b')], policy={'quarantine_invalid_history_dates': True})[1]
        self.assertIsNone(result['flagged'])
        self.assertEqual(result['lines'][0]['utilisation_bounds']['prior_units_upper'], 3)

    def test_any_legal_discount_price_prevents_guessed_error(self):
        c = history_contract()
        set_history_discount(c, (2, 10), (5, 20))
        prior = sample_invoice('a', service='opaque xyz', quantity=10, day='2024-01-01')
        target = sample_invoice('b', day='2024-01-02', patient='other', price=91)
        result = audit_history(c, [prior, target], history_scope_for(0, 'a'))[1]
        self.assertEqual(result['lines'][0]['possible_unit_prices_cents'], [81, 91, 101])
        self.assertIsNone(result['flagged'])

    def test_price_outside_every_possible_discount_is_proven_error(self):
        c = history_contract()
        set_history_discount(c, (2, 10), (5, 20))
        prior = sample_invoice('a', service='opaque xyz', quantity=10, day='2024-01-01')
        target = sample_invoice('b', day='2024-01-02', patient='other', price=77)
        result = audit_history(c, [prior, target], history_scope_for(0, 'a'))[1]
        self.assertEqual(result['flagged'], 1)
        self.assertEqual(result['error_category'], 'unit_price_mismatch')
        self.assertIsNone(result['expected_total_cents'])
        self.assertEqual(result['findings'][0]['evidence']['possible_expected_cents'], [81, 91, 101])

    def test_discount_proof_includes_bundle_multipliers_and_each_rounding_step(self):
        c = history_contract()
        set_history_discount(c, (2, 10))
        c['contract_details']['facility_multiplier'] = {'numerator': 3, 'denominator': 2}
        c['contract_details']['plan_tier_multiplier'] = {'numerator': 7, 'denominator': 5}
        c['bundles']['rules'] = [{'service_a': 'Alpha Consultation', 'service_b': 'Beta Laboratory Panel', 'bundled_rate_a_cents': 91, 'bundled_rate_b_cents': 180}]
        c['non_business_day_uplifts']['rules'] = [{'service_name': 'Alpha Consultation', 'uplift_percent': 12}]
        prior = sample_invoice('a', service='opaque xyz', quantity=10, day='2024-01-01', patient='other')
        target = sample_invoice('b', day='2024-01-07', price=194)
        partner = sample_invoice('c', service='Beta Laboratory Panel', day='2024-01-07')
        partner['line_items'][0]['unit_basis_as_billed'] = 'per_test'
        result = audit_history(c, [prior, target, partner], history_scope_for(0, 'a'))[1]
        self.assertEqual(result['lines'][0]['possible_unit_prices_cents'], [194, 215])
        self.assertIsNone(result['flagged'])

    def test_discount_price_proof_not_used_with_uncertain_competing_bundle(self):
        c = history_contract()
        set_history_discount(c, (2, 10))
        c['bundles']['rules'] = [{'service_a': 'Alpha Consultation', 'service_b': 'Beta Laboratory Panel', 'bundled_rate_a_cents': 90, 'bundled_rate_b_cents': 180}]
        prior = sample_invoice('a', service='opaque xyz', quantity=10, day='2024-01-01', patient='other')
        target = sample_invoice('b', price=77)
        partner = sample_invoice('c', service='opaque abc')
        scopes = {**history_scope_for(0, 'a'), **history_scope_for(2, 'c', 'Beta Laboratory Panel')}
        result = audit_history(c, [prior, target, partner], scopes)[1]
        self.assertIsNone(result['flagged'])
        self.assertTrue(is_history_blocked(result))
        self.assertNotIn('possible_unit_prices_cents', result['lines'][0])

    def test_bundle_presence_does_not_require_known_quantity(self):
        c = history_contract()
        c['bundles']['rules'] = [{'service_a': 'Alpha Consultation', 'service_b': 'Beta Laboratory Panel', 'bundled_rate_a_cents': 90, 'bundled_rate_b_cents': 180}]
        other = sample_invoice('b', service='Beta Laboratory Panel', price=180)
        other['line_items'][0].update(quantity=None, unit_basis_as_billed='per_test')
        result = audit_history(c, [sample_invoice('a', price=90), other])[0]
        self.assertEqual(result['flagged'], 0)
        self.assertFalse(is_history_blocked(result))
        self.assertTrue(result['lines'][0]['history_presence_assumptions'])

    def test_nonpositive_quantity_cannot_force_bundle_presence(self):
        for quantity in (0, -1):
            for price in (90, 101):
                with self.subTest(quantity=quantity, price=price):
                    c = history_contract()
                    c['bundles']['rules'] = [{'service_a': 'Alpha Consultation', 'service_b': 'Beta Laboratory Panel', 'bundled_rate_a_cents': 90, 'bundled_rate_b_cents': 180}]
                    other = sample_invoice('b', service='Beta Laboratory Panel', quantity=quantity, price=180)
                    other['line_items'][0]['unit_basis_as_billed'] = 'per_test'
                    result = audit_history(c, [sample_invoice('a', price=price), other])[0]
                    self.assertIsNone(result['flagged'])
                    self.assertTrue(is_history_blocked(result))
                    self.assertNotIn('unit_price_mismatch', result['error_category'])

    def test_nonpositive_quantity_cannot_force_exclusion_presence(self):
        c = history_contract()
        c['exclusion_windows']['patient_scope']['status'] = 'reviewed_interpretation'
        c['exclusion_windows']['rules'] = [{'excluded_service': 'Alpha Consultation', 'related_service': 'Beta Laboratory Panel', 'window_days': 2}]
        other = sample_invoice('b', service='Beta Laboratory Panel', quantity=0, day='2024-01-04')
        result = audit_history(c, [sample_invoice('a'), other])[0]
        self.assertIsNone(result['flagged'])
        self.assertTrue(is_history_blocked(result))
        self.assertNotIn('exclusion_window_violation', result['error_category'])

    def test_bundle_presence_does_not_require_resolved_unit(self):
        c = history_contract()
        c['services'][1]['unit_basis'] = None
        c['bundles']['rules'] = [{'service_a': 'Alpha Consultation', 'service_b': 'Beta Laboratory Panel', 'bundled_rate_a_cents': 90, 'bundled_rate_b_cents': 180}]
        other = sample_invoice('b', service='Beta Laboratory Panel', price=180)
        result = audit_history(c, [sample_invoice('a', price=90), other])[0]
        self.assertEqual(result['flagged'], 0)

    def test_optional_extra_partner_cannot_remove_existing_bundle(self):
        c = history_contract()
        c['bundles']['rules'] = [{'service_a': 'Alpha Consultation', 'service_b': 'Beta Laboratory Panel', 'bundled_rate_a_cents': 90, 'bundled_rate_b_cents': 180}]
        known = sample_invoice('b', service='Beta Laboratory Panel', price=180)
        known['line_items'][0]['unit_basis_as_billed'] = 'per_test'
        maybe = sample_invoice('c', service='opaque xyz')
        result = audit_history(c, [sample_invoice('a', price=90), known, maybe], history_scope_for(2, 'c', 'Beta Laboratory Panel'))[0]
        self.assertEqual(result['flagged'], 0)

    def test_optional_partner_still_blocks_when_presence_changes_rate(self):
        c = history_contract()
        c['bundles']['rules'] = [{'service_a': 'Alpha Consultation', 'service_b': 'Beta Laboratory Panel', 'bundled_rate_a_cents': 90, 'bundled_rate_b_cents': 180}]
        maybe = sample_invoice('b', service='opaque xyz')
        result = audit_history(c, [sample_invoice('a'), maybe], history_scope_for(1, 'b', 'Beta Laboratory Panel'))[0]
        self.assertIsNone(result['flagged'])
        self.assertTrue(is_history_blocked(result))

    def test_already_exceeded_threshold_is_not_tainted_by_extra_usage(self):
        c = history_contract()
        c['threshold_premiums']['rules'] = [{'service_name': 'Alpha Consultation', 'quantity': 2, 'comparison': 'greater_than', 'uplift_percent': 50}]
        target = sample_invoice('a', quantity=3, price=152)
        maybe = sample_invoice('b', service='opaque xyz', quantity=10)
        maybe['line_items'][0]['unit_basis_as_billed'] = 'per_hour'
        result = audit_history(c, [target, maybe], history_scope_for(1, 'b'))[0]
        self.assertEqual(result['flagged'], 0)

    def test_possible_threshold_crossing_still_blocks(self):
        c = history_contract()
        c['threshold_premiums']['rules'] = [{'service_name': 'Alpha Consultation', 'quantity': 2, 'comparison': 'greater_than', 'uplift_percent': 50}]
        maybe = sample_invoice('b', service='opaque xyz', quantity=2)
        result = audit_history(c, [sample_invoice('a'), maybe], history_scope_for(1, 'b'))[0]
        self.assertIsNone(result['flagged'])
        self.assertTrue(is_history_blocked(result))

    def test_uncertain_units_do_not_prove_daily_cap_violation(self):
        c = history_contract()
        c['daily_caps']['rules'] = [{'service_name': 'Alpha Consultation', 'quantity': 2}]
        other = sample_invoice('b', quantity=20)
        other['line_items'][0]['unit_basis_as_billed'] = 'per_hour'
        result = audit_history(c, [sample_invoice('a'), other])[0]
        self.assertNotIn('daily_cap_exceeded', result['error_category'])
        self.assertIsNone(result['flagged'])

    def test_exclusion_uses_known_presence_even_if_quantity_missing(self):
        c = history_contract()
        c['exclusion_windows']['patient_scope']['status'] = 'reviewed_interpretation'
        c['exclusion_windows']['rules'] = [{'excluded_service': 'Alpha Consultation', 'related_service': 'Beta Laboratory Panel', 'window_days': 2}]
        other = sample_invoice('b', service='Beta Laboratory Panel', day='2024-01-04')
        other['line_items'][0]['quantity'] = None
        result = audit_history(c, [sample_invoice('a'), other])[0]
        self.assertIn('exclusion_window_violation', result['error_category'])
        self.assertFalse(is_history_blocked(result))

    def test_exclusive_boundary_cannot_be_changed_by_optional_edge_event(self):
        c = history_contract()
        c['exclusion_windows']['boundary_inclusive'] = False
        c['exclusion_windows']['rules'] = [{'excluded_service': 'Alpha Consultation', 'related_service': 'Beta Laboratory Panel', 'window_days': 2}]
        maybe = sample_invoice('b', service='opaque xyz', day='2024-01-07')
        result = audit_history(c, [sample_invoice('a'), maybe], history_scope_for(1, 'b', 'Beta Laboratory Panel'))[0]
        self.assertEqual(result['flagged'], 0)

    def test_legacy_contract_does_not_enable_new_policy(self):
        c = sample_contract()
        set_history_discount(c, (2, 10))
        prior = sample_invoice('a', quantity=3, day='2024-01-01', patient=None)
        target = sample_invoice('b', day='2024-01-02', price=101)
        result = rules.audit(c, [prior, target])[1]
        self.assertEqual(result['flagged'], 0)
        self.assertEqual(result['lines'][0]['calculation'][-1]['prior_units'], 0)
        self.assertNotIn('history_dependency_reasons', result['lines'][0])

# Source-backed unit clarifications

class UnitClarificationTests(unittest.TestCase):

    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        source_dir = self.root / 'contracts' / 'hospital_2'
        source_dir.mkdir(parents=True)
        self.source = source_dir / 'contract.txt'
        self.out = self.root / 'output'
        self.out.mkdir()
        self.patcher = patch.object(contracts, 'ROOT', self.root)
        self.patcher.start()
        self.addCleanup(self.patcher.stop)

    def draft(self, text='Alpha Service per hour GBP 10.00'):
        self.source.write_text(text, encoding='utf-8')
        digest = hashlib.sha256(self.source.read_bytes()).hexdigest()
        return {'hospital_id': 'H2', 'contract_details': {'contract_number': 'C-H2'}, 'services': [{'service_name': 'Alpha Service', 'unit_basis': None}], 'source_sections': [{'document_id': 'contract.txt', 'sha256': digest, 'text': text}]}

    def clarification(self, draft, **extra):
        return {'hospital_id': 'H2', 'contract_number': 'C-H2', 'service_name': 'Alpha Service', 'unit_basis': 'per_hour', 'source_ids': ['contract.txt:1'], 'source_document_sha256': {'contract.txt': draft['source_sections'][0]['sha256']}, 'explanation': 'Reviewed the named source rate clause.', 'reviewed_by': 'Test reviewer', **extra}

    def answer(self, **extra):
        return {'decision': 'resolved', 'unit_basis': 'per_hour', 'source_ids': ['contract.txt:1'], 'explanation': 'The source states the unit.', **extra}

    def test_existing_clear_source_can_repair_an_extraction_omission(self):
        draft = self.draft()
        graph = contracts.ClauseGraph(draft)
        accepted = contracts.validate_clarifications(draft, graph, [self.clarification(draft)])
        self.assertEqual(accepted['Alpha Service']['accepted_unit'], 'per_hour')

    def test_compound_source_cannot_be_overridden_by_reviewed_by_field(self):
        draft = self.draft('Alpha Service per hour, per item GBP 10.00')
        with self.assertRaisesRegex(ValueError, 'compound'):
            contracts.review_units(draft, None, self.out, clarifications=[self.clarification(draft)])
        self.assertFalse((self.out / 'H2.reviewed_contract.json').exists())

    def test_manual_source_review_never_calls_agent_and_preserves_input(self):
        draft = self.draft()
        original = copy.deepcopy(draft)

        class UnexpectedAPI:

            def call(self, body):
                raise AssertionError('No paid call is needed for a validated source selection')
        reviewed = contracts.review_units(draft, UnexpectedAPI(), self.out, [self.clarification(draft)])
        self.assertEqual(reviewed['services'][0]['unit_basis'], 'per_hour')
        self.assertEqual(draft, original)
        self.assertEqual(json.loads((self.out / 'H2.unit_clarification_requests.json').read_text()), [])

    def test_pending_request_reports_options_and_exact_source(self):
        draft = self.draft('Alpha Service per hour, per item GBP 10.00')
        reviewed = contracts.review_units(draft, None, self.out)
        service = reviewed['services'][0]
        self.assertIsNone(service['unit_basis'])
        self.assertEqual(service['unit_uncertainty']['unit_options'], ['per_hour', 'per_item'])
        requests = json.loads((self.out / 'H2.unit_clarification_requests.json').read_text())
        self.assertEqual(requests[0]['reason'], 'compound_unit_not_defined')
        self.assertEqual(requests[0]['source_clauses'][0]['source_ids'], ['contract.txt:1'])
        self.assertIn('quantity', requests[0]['required_clarification'])
        self.assertEqual(requests[0]['contract_number'], 'C-H2')

    def test_compound_unit_does_not_spend_to_repeat_known_ambiguity(self):
        draft = self.draft('Alpha Service per hour, per item GBP 10.00')

        class UnexpectedAPI:

            def call(self, body):
                raise AssertionError('A model cannot resolve a missing contractual definition')
        reviewed = contracts.review_units(draft, UnexpectedAPI(), self.out)
        self.assertIsNone(reviewed['services'][0]['unit_basis'])
        decisions = json.loads((self.out / 'H2.unit_review.json').read_text())
        self.assertTrue(decisions[0]['agent_call_skipped'])

    def test_contract_and_document_bindings_are_required(self):
        draft = self.draft()
        graph = contracts.ClauseGraph(draft)
        for change in ({'contract_number': 'OTHER'}, {'source_ids': ['invented:1']}, {'source_document_sha256': {'contract.txt': '0' * 64}}, {'source_document_sha256': {}}, {'reviewed_by': ''}):
            with self.subTest(change=change), self.assertRaises(ValueError):
                contracts.validate_clarifications(draft, graph, [self.clarification(draft, **change)])

    def test_duplicate_service_choices_rejected(self):
        draft = self.draft()
        entry = self.clarification(draft)
        with self.assertRaisesRegex(ValueError, 'duplicate service'):
            contracts.validate_clarifications(draft, contracts.ClauseGraph(draft), [entry, entry])

    def test_embedded_unverified_text_cannot_authorize_a_manual_selection(self):
        draft = self.draft()
        with patch.object(contracts, 'ROOT', self.root / 'missing'):
            graph = contracts.ClauseGraph(draft)
        with self.assertRaisesRegex(ValueError, 'hash-verified original'):
            contracts.validate_clarifications(draft, graph, [self.clarification(draft)])

    def test_non_rate_citation_does_not_substitute_for_actual_rate_citation(self):
        evidence = [{'id': 'contract.txt:1', 'text': 'Alpha Service per hour GBP 10.00'}, {'id': 'contract.txt:2', 'text': 'Alpha Service uses a per hour basis.'}]
        with self.assertRaisesRegex(ValueError, 'No cited service rate'):
            contracts.validate_unit(self.answer(source_ids=['contract.txt:2']), evidence, 'Alpha Service')

    def test_unit_word_boundaries(self):
        evidence = [{'id': 'contract.txt:1', 'text': 'Alpha Service per hourglass GBP 10.00'}]
        with self.assertRaises(ValueError):
            contracts.validate_unit(self.answer(), evidence, 'Alpha Service')

    def test_conditional_unit_is_not_a_scalar(self):
        draft = self.draft('Alpha Service per hour GBP 10.00 when continuously monitored.')
        evidence = contracts.ClauseGraph(draft).retrieve('Alpha Service')
        self.assertEqual(contracts.unit_evidence_details(evidence, 'Alpha Service')['reason'], 'conditional_unit_requires_applicability_rule')
        with self.assertRaises(ValueError):
            contracts.validate_unit(self.answer(), evidence, 'Alpha Service')

    def test_no_price_unit_amendment_cannot_be_ignored(self):
        draft = self.draft('Alpha Service per hour GBP 10.00\n\nThe billing unit for Alpha Service is replaced by per visit.')
        evidence = contracts.ClauseGraph(draft).retrieve('Alpha Service')
        self.assertEqual(contracts.unit_evidence_details(evidence, 'Alpha Service')['reason'], 'conflicting_source_units_require_precedence')
        with self.assertRaises(ValueError):
            contracts.validate_unit(self.answer(), evidence, 'Alpha Service')

    def test_wrapped_rate_requires_citing_both_original_lines(self):
        draft = self.draft('Alpha Service\n    per hour GBP 10.00')
        evidence = contracts.ClauseGraph(draft).retrieve('Alpha Service')
        with self.assertRaisesRegex(ValueError, 'No cited service rate'):
            contracts.validate_unit(self.answer(), evidence, 'Alpha Service')
        self.assertEqual(contracts.validate_unit(self.answer(source_ids=['contract.txt:1', 'contract.txt:2']), evidence, 'Alpha Service'), 'per_hour')

    def test_wrapped_compound_unit_is_not_truncated(self):
        draft = self.draft('Alpha Service per hour GBP 10.00\n    per item')
        evidence = contracts.ClauseGraph(draft).retrieve('Alpha Service')
        self.assertEqual(contracts.unit_evidence_details(evidence, 'Alpha Service')['unit_options'], ['per_hour', 'per_item'])
        with self.assertRaises(ValueError):
            contracts.validate_unit(self.answer(), evidence, 'Alpha Service')

    def test_longer_service_name_is_not_assigned_to_shorter_catalogue_name(self):
        draft = self.draft('Advanced Alpha Service per hour GBP 10.00\nAlpha Service per visit GBP 8.00')
        draft['services'].append({'service_name': 'Advanced Alpha Service', 'unit_basis': 'per_hour'})
        graph = contracts.ClauseGraph(draft)
        self.assertEqual(graph.edges['Alpha Service'], ['contract.txt:2'])
        self.assertEqual(contracts.unit_evidence_details(graph.retrieve('Alpha Service'), 'Alpha Service')['unit_options'], ['per_visit'])

    def test_source_path_cannot_escape_hospital_directory(self):
        draft = self.draft()
        draft['source_sections'][0]['document_id'] = '../../outside.txt'
        with self.assertRaisesRegex(ValueError, 'Invalid or duplicate'):
            contracts.ClauseGraph(draft)

    def test_original_four_telemetry_clauses_still_require_clarification(self):
        with patch.object(contracts, 'ROOT', ROOT):
            for number in (2, 3, 4, 5):
                draft = json.loads((ROOT / 'contract_agent_final_v4' / f'hospital_{number}.json').read_text())
                graph = contracts.ClauseGraph(draft)
                services = [s for s in draft['services'] if s.get('unit_basis') is None]
                self.assertEqual(len(services), 1)
                name = services[0]['service_name']
                evidence = graph.retrieve(name)
                details = contracts.unit_evidence_details(evidence, name)
                self.assertEqual(details['reason'], 'compound_unit_not_defined')
                self.assertEqual(details['unit_options'], ['per_hour', 'per_item'])
                ids = details['source_clauses'][0]['source_ids']
                with self.assertRaises(ValueError):
                    contracts.validate_unit(self.answer(source_ids=ids), evidence, name)

# Feedback reconciliation and preflight

class RunFeedbackTests(unittest.TestCase):

    def row(self, reasons, **changes):
        return {'hospital_id': 'H2', 'flagged': None, 'record_identity_ambiguous': False, 'review_reasons': reasons, **changes}

    def test_repeated_line_reasons_count_once_per_invoice(self):
        reasons = ['Cumulative usage uncertain for Alpha', 'Cumulative usage uncertain for Beta', 'Patient history uncertain for Alpha']
        self.assertEqual(reporting.reason_codes(reasons), ['patient_history', 'volume_discount'])

    def test_overlap_and_disjoint_groups_reconcile(self):
        rows = [self.row(['Cumulative usage uncertain', 'Patient history uncertain']), self.row(['Contract unit basis ambiguous'], hospital_id='H3'), self.row(['Unresolved service description']), self.row([]), self.row(['Cumulative usage uncertain'], flagged=1), self.row(['Patient history uncertain'], record_identity_ambiguous=True)]
        result = reporting.summarize_remaining(rows)
        self.assertEqual(result['eligible_unanswered'], 4)
        self.assertEqual(result['duplicate_id_records_excluded_separately'], 1)
        self.assertEqual(result['multiple_reasons'], 1)
        self.assertEqual(sum(result['non_overlapping_reason_groups'].values()), 4)
        self.assertEqual(result['overlapping_reason_counts']['volume_discount'], 1)
        self.assertEqual(result['single_reason_counts']['unexplained_abstention'], 1)
        self.assertEqual(result['by_hospital_overlapping']['H3'], {'contract_unit': 1})

    def test_empty_run(self):
        result = reporting.summarize_remaining([])
        self.assertEqual(result['eligible_unanswered'], 0)
        self.assertEqual(result['non_overlapping_reason_groups'], {})

class UnitManifestPreflightTests(unittest.TestCase):

    def test_pipeline_rejects_invalid_manifest_before_budget_setup(self):
        manifest = self.manifest([{'hospital_id': 'H1', 'service_name': 'Alpha'}])
        output = self.root / 'must_not_be_created'
        arguments = ['--invoice-ai', '--four-blocker-fixes',
                     '--unit-clarifications', str(manifest), '--output-dir', str(output)]
        with patch.object(pipeline, 'BudgetedAI') as api:
            with self.assertRaisesRegex(ValueError, 'frozen pipeline'):
                pipeline.main(arguments)
        api.assert_not_called()
        self.assertFalse(output.exists())

    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.path = self.root / 'clarifications.json'

    def manifest(self, clarifications, **extra):
        self.path.write_text(json.dumps({'schema_version': 1, 'clarifications': clarifications, **extra}))
        return self.path

    def test_optional_or_empty_manifest_needs_no_contract(self):
        self.assertEqual(contracts.load_and_preflight(None, self.root), [])
        self.assertEqual(contracts.load_and_preflight(self.manifest([]), self.root), [])

    def test_wrong_manifest_shape_fails(self):
        for payload in ([], {}, {'schema_version': True, 'clarifications': []}, {'schema_version': 1, 'clarifications': {}}, {'schema_version': 1, 'clarifications': [], 'override': True}):
            with self.subTest(payload=payload):
                self.path.write_text(json.dumps(payload))
                with self.assertRaises(ValueError):
                    contracts.load_and_preflight(self.path, self.root)

    def test_invalid_binding_duplicate_and_h1_rejected(self):
        valid = {'hospital_id': 'H2', 'service_name': 'Alpha Service'}
        for entries in ([valid, valid], [{**valid, 'hospital_id': 'H1'}], [{**valid, 'hospital_id': '../H2'}], [42], [{**valid, 'service_name': ''}]):
            with self.subTest(entries=entries), self.assertRaises(ValueError):
                contracts.load_and_preflight(self.manifest(entries), self.root)

    def test_each_hospital_validated_against_own_draft(self):
        entries = [{'hospital_id': h, 'service_name': 'Alpha'} for h in ('H2', 'H3')]
        for h in ('H2', 'H3'):
            (self.root / f'hospital_{h[1:]}.json').write_text(json.dumps({'hospital_id': h}))
        with patch('contracts.ClauseGraph') as graph, patch('contracts.validate_clarifications') as validate:
            self.assertEqual(contracts.load_and_preflight(self.manifest(entries), self.root), entries)
        self.assertEqual(graph.call_count, 2)
        for call, entry in zip(validate.call_args_list, entries):
            self.assertEqual(call.args[0]['hospital_id'], entry['hospital_id'])
            self.assertEqual(call.args[2], [entry])

    def test_source_rejection_is_not_swallowed(self):
        entry = {'hospital_id': 'H2', 'service_name': 'Alpha'}
        (self.root / 'hospital_2.json').write_text(json.dumps({'hospital_id': 'H2'}))
        with patch('contracts.ClauseGraph'), patch('contracts.validate_clarifications', side_effect=ValueError('compound')):
            with self.assertRaisesRegex(ValueError, 'compound'):
                contracts.load_and_preflight(self.manifest([entry]), self.root)

# Comparison reporting

class CompareResultsTests(unittest.TestCase):

    def test_explicit_unanswered_is_not_invalid_prediction(self):
        labels = {'a': [{'is_erroneous': '0'}], 'b': [{'is_erroneous': '1'}]}
        predictions = {'a': [{'flagged': 'not answered'}], 'b': [{'flagged': '1'}]}
        counts = reporting.compare(predictions, labels)
        self.assertEqual((counts['correct'], counts['unanswered'], counts['invalid_predictions']), (1, 1, 0))

    def test_other_hospitals_do_not_enter_h1_comparison(self):
        with tempfile.TemporaryDirectory() as temp:
            path = Path(temp) / 'predictions.csv'
            with path.open('w', newline='') as stream:
                writer = csv.DictWriter(stream, fieldnames=['hospital_id', 'invoice_id', 'flagged'])
                writer.writeheader()
                writer.writerows([{'hospital_id': h, 'invoice_id': h + '-1', 'flagged': '0'} for h in ('H1', 'H2')])
            self.assertEqual(set(reporting.load(path)), {'H1-1'})

    def test_incomplete_run_is_not_selected(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            completed = root / 'audit_output/test_all_20260101'
            completed.mkdir(parents=True)
            for name in ('summary.json', 'test_all_context.json', 'all_results.json'):
                (completed / name).write_text('{}')
            incomplete = root / 'audit_output/test_all_20261231'
            incomplete.mkdir()
            (incomplete / 'summary.json').write_text(json.dumps({}))
            self.assertEqual(reporting.latest_predictions(root), completed / 'all_results.json')

    def test_json_comparison_filters_h1_and_normalizes_unanswered_and_duplicate_records(self):
        with tempfile.TemporaryDirectory() as temp:
            path = Path(temp) / 'all_results.json'
            rows = [
                {'hospital_id': 'H1', 'invoice_id': 'correct', 'flagged': 0},
                {'hospital_id': 'H1', 'invoice_id': 'error', 'flagged': 1},
                {'hospital_id': 'H1', 'invoice_id': 'unknown', 'flagged': None},
                {'hospital_id': 'H1', 'invoice_id': 'ambiguous', 'flagged': 1,
                 'record_identity_ambiguous': True},
                {'hospital_id': 'H2', 'invoice_id': 'other-hospital', 'flagged': 0},
            ]
            path.write_text(json.dumps(rows))
            predictions = reporting.load(path)
            self.assertEqual(set(predictions), {'correct', 'error', 'unknown', 'ambiguous'})
            self.assertEqual({key: value[0]['flagged'] for key, value in predictions.items()},
                             {'correct': '0', 'error': '1', 'unknown': 'not answered',
                              'ambiguous': 'not answered'})
            labels = {key: [{'is_erroneous': '1' if key == 'error' else '0'}]
                      for key in predictions}
            counts = reporting.compare(predictions, labels)
            self.assertEqual((counts['correct'], counts['unanswered'], counts['invalid_predictions']),
                             (2, 2, 0))

    def test_no_results_explains_how_to_run(self):
        with tempfile.TemporaryDirectory() as temp, self.assertRaisesRegex(FileNotFoundError, 'main.py'):
            reporting.latest_predictions(Path(temp))

class EvaluationDetailsTests(unittest.TestCase):

    def setUp(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.directory = Path(temporary.name)

    def row(self, identifier, flagged=0, categories='', total=100, score=None):
        return {
            'hospital_id': 'H1', 'record_index': 0, 'invoice_id': identifier,
            'flagged': flagged, 'error_category': categories,
            'expected_total_cents': total, 'billed_total_cents': 100,
            'record_identity_ambiguous': False, 'review_reasons': [],
            'confidence': score,
        }

    def labels(self, records):
        path = self.directory / 'labels/hospital_1_labels.csv'
        path.parent.mkdir(exist_ok=True)
        with path.open('w', newline='', encoding='utf-8') as stream:
            writer = csv.writer(stream)
            writer.writerow(['invoice_id', 'is_erroneous', 'error_categories',
                             'expected_total_cents', 'ambiguity_sensitive'])
            writer.writerows([identifier, flagged, categories, total, 0]
                             for identifier, flagged, categories, total in records)
        return path

    def test_duplicate_safe_categories_and_missing_versus_wrong_totals(self):
        rows = [
            self.row('exact'),
            self.row('overlap', 1, 'premium_omitted|unit_price_mismatch'),
            self.row('missing', 1, total=None),
            self.row('wrong', 1, 'unit_price_mismatch', total=90),
            self.row('duplicate', 1, 'excluded_noise'),
            self.row('duplicate', 1, 'excluded_noise'),
            self.row('duplicate-label', 1, 'excluded_noise'),
            self.row('unlabelled', 1, 'excluded_noise'),
        ]
        path = self.labels([
            ('exact', 0, '', 100),
            ('overlap', 1, 'premium_omitted', 100),
            ('missing', 1, 'unknown_service', 100),
            ('wrong', 1, 'unit_price_mismatch', 100),
            ('duplicate', 1, 'excluded_noise', 100),
            ('duplicate-label', 0, '', 100),
            ('duplicate-label', 1, 'excluded_noise', 100),
        ])
        original_rows, original_labels = copy.deepcopy(rows), path.read_bytes()
        report = reporting.evaluation_details(rows, path)
        self.assertEqual((report['matched_unique_records'], report['excluded_prediction_records']), (4, 4))
        self.assertEqual((report['true_positives'], report['true_negatives'], report['verdict_correct']), (3, 1, 4))
        self.assertEqual((report['provided_totals'], report['exact_totals'], report['missing_totals'],
                          report['incorrect_provided_totals']), (3, 2, 1, 1))
        self.assertEqual(report['exact_category_sets'], 2)
        self.assertEqual(report['expected_total_coverage'], .75)
        self.assertAlmostEqual(report['expected_total_exact_accuracy_on_covered'], 2 / 3)
        categories = report['per_category']
        self.assertNotIn('excluded_noise', categories)
        for category, expected in {
            'premium_omitted': (1, 0, 0),
            'unit_price_mismatch': (1, 1, 0),
            'unknown_service': (0, 0, 1),
        }.items():
            with self.subTest(category=category):
                self.assertEqual(tuple(categories[category][key] for key in
                                       ('true_positives', 'false_positives', 'false_negatives')), expected)
        self.assertEqual(categories['unit_price_mismatch']['precision'], .5)
        self.assertEqual(categories['unknown_service']['recall_including_abstentions'], 0)
        self.assertIsNone(categories['unknown_service']['precision'])
        self.assertEqual(report['evaluation_role'], 'development_only_not_held_out')
        self.assertEqual(rows, original_rows)
        self.assertEqual(path.read_bytes(), original_labels)

    def test_confidence_diagnostic_requires_every_part_of_a_complete_row(self):
        rows = [
            self.row('complete', 1, 'alpha|beta', score=.8),
            self.row('wrong-verdict', 0, 'alpha', score=.8),
            self.row('wrong-category', 1, 'beta', score=.8),
            self.row('missing-total', 1, 'alpha', total=None, score=.8),
            self.row('wrong-total', 1, 'alpha', total=99, score=.8),
        ]
        labels = [('complete', 1, 'beta|alpha', 100)] + [
            (identifier, 1, 'alpha', 100)
            for identifier in ('wrong-verdict', 'wrong-category', 'missing-total', 'wrong-total')
        ]
        for index, invalid_score in enumerate((None, True, -.1, 1.1, '0.8')):
            identifier = f'invalid-score-{index}'
            rows.append(self.row(identifier, score=invalid_score))
            labels.append((identifier, 0, '', 100))
        original = copy.deepcopy(rows)
        diagnostic = reporting.evaluation_details(rows, self.labels(labels))['confidence_diagnostic']
        self.assertEqual(diagnostic['role'], 'development_only_not_independent_calibration')
        self.assertIn('Missing totals fail this completeness proxy', diagnostic['target'])
        self.assertIn('not calibrated probabilities', diagnostic['limitation'])
        self.assertEqual(diagnostic['count'], 5)
        self.assertAlmostEqual(diagnostic['brier_on_strict_complete_row_proxy'], .52)
        self.assertEqual(len(diagnostic['bins']), 1)
        bucket = diagnostic['bins'][0]
        self.assertEqual((bucket['lower'], bucket['upper'], bucket['count']), (.8, .9, 5))
        self.assertAlmostEqual(bucket['mean_score'], .8)
        self.assertEqual(bucket['observed_proxy_success'], .2)
        self.assertEqual(rows, original)

    def test_empty_or_fully_excluded_cohort_has_no_division_by_zero(self):
        for rows, labels in [([], []),
                             ([self.row('duplicate'), self.row('duplicate')],
                              [('duplicate', 0, '', 100)])]:
            with self.subTest(raw_records=len(rows)):
                report = reporting.evaluation_details(rows, self.labels(labels))
                self.assertEqual(report['matched_unique_records'], 0)
                self.assertEqual(report['excluded_prediction_records'], len(rows))
                self.assertEqual(report['per_category'], {})
                self.assertEqual(report['provided_totals'], 0)
                self.assertIsNone(report['decision_coverage'])
                self.assertIsNone(report['expected_total_exact_accuracy_on_covered'])
                diagnostic = report['confidence_diagnostic']
                self.assertEqual(diagnostic['count'], 0)
                self.assertEqual(diagnostic['bins'], [])
                self.assertIsNone(diagnostic['brier_on_strict_complete_row_proxy'])

    def test_export_writes_h1_evaluation_and_verifiable_source_hashes(self):
        labels = self.labels([('shared-id', 0, '', 100)])
        rows = [self.row('shared-id', score=.8),
                {**self.row('shared-id', 1, 'other-hospital-error', score=.2),
                 'hospital_id': 'H2'}]
        output = self.directory / 'run'
        output.mkdir()
        source = output / 'all_results.json'
        source.write_text(json.dumps(rows), encoding='utf-8')
        original_source, original_labels = source.read_bytes(), labels.read_bytes()
        with patch.object(reporting, 'ROOT', self.directory):
            exported, remaining = reporting.export_results(output)
        evaluation = json.loads((output / 'evaluation.json').read_text())
        self.assertEqual(exported, rows)
        self.assertEqual(remaining['eligible_unanswered'], 0)
        self.assertEqual(evaluation['matched_unique_records'], 1)
        self.assertEqual(evaluation['verdict_correct'], 1)
        self.assertEqual(evaluation['per_category'], {})
        self.assertEqual(evaluation['source_sha256'], {
            'all_results.json': hashlib.sha256(original_source).hexdigest(),
            'labels/hospital_1_labels.csv': hashlib.sha256(original_labels).hexdigest(),
        })
        self.assertEqual(source.read_bytes(), original_source)
        self.assertEqual(labels.read_bytes(), original_labels)
        self.assertTrue((output / 'submit_all_data.json').is_file())
        self.assertTrue((output / 'remaining_blockers.json').is_file())
        without_h1 = self.directory / 'other-hospital'
        without_h1.mkdir()
        (without_h1 / 'all_results.json').write_text(json.dumps(rows[1:]), encoding='utf-8')
        with patch.object(reporting, 'ROOT', self.directory):
            reporting.export_results(without_h1)
        self.assertFalse((without_h1 / 'evaluation.json').exists())


# Explainable evidence scores; not probabilities or model self-confidence

class ConfidenceTests(unittest.TestCase):

    def row(self, method='full_catalogue_token_constraints'):
        return {
            'invoice_id': 'confidence-fixture', 'flagged': 0,
            'record_identity_ambiguous': False, 'error_category': '',
            'expected_total_cents': 100, 'billed_total_cents': 100,
            'findings': [], 'review_reasons': [],
            'lines': [{
                'line_id': 'line-1', 'expected_line_total_cents': 100,
                'match': {'service_name': 'Alpha Consultation', 'status': 'accepted',
                          'method': method},
            }],
        }

    def test_output_is_deterministic_immutable_and_explicitly_uncalibrated(self):
        row = self.row()
        original = copy.deepcopy(row)
        result = confidence.score_invoice(row)
        self.assertEqual(result, confidence.score_invoice(row))
        self.assertEqual(row, original)
        self.assertEqual(result['confidence_method'], 'evidence_score_v1_uncalibrated')
        self.assertRegex(result['confidence_note'].lower(), r'(uncalibrated|not.*calibrated)')
        self.assertTrue(result['confidence_reasons'])
        self.assertTrue(all(isinstance(reason, str) for reason in result['confidence_reasons']))
        for field in ('confidence', 'verdict_confidence', 'total_confidence', 'category_confidence'):
            self.assertGreater(result[field], 0)
            self.assertLess(result[field], 1)

    def test_unknown_and_duplicate_records_have_no_numeric_confidence(self):
        for changes in ({'flagged': None}, {'flagged': 1, 'record_identity_ambiguous': True}):
            with self.subTest(changes=changes):
                row = {**self.row(), **changes}
                row['confidence'] = .99  # A stale upstream score must not survive.
                result = confidence.score_invoice(row)
                for field in ('confidence', 'verdict_confidence', 'total_confidence', 'category_confidence'):
                    self.assertIsNone(result[field])
                self.assertTrue(result['confidence_reasons'])

    def test_validated_text_and_price_inferences_remain_below_direct_matches(self):
        direct = confidence.score_invoice(self.row())
        reviewed = confidence.score_invoice(self.row('reviewed_description'))
        ai = confidence.score_invoice(self.row('ai_description_mapping'))
        inferred = confidence.score_invoice(self.row('price_pattern_inference'))
        self.assertGreater(direct['confidence'], ai['confidence'])
        self.assertGreater(reviewed['confidence'], ai['confidence'])
        self.assertGreater(ai['confidence'], inferred['confidence'])
        self.assertNotEqual(direct['confidence_reasons'], inferred['confidence_reasons'])

    def test_more_independent_price_references_cannot_become_direct_evidence(self):
        few, many = self.row('price_pattern_inference'), self.row('price_pattern_inference')
        few['lines'][0]['match']['resolution_evidence'] = {'reference_invoice_ids': ['a', 'b', 'c']}
        many['lines'][0]['match']['resolution_evidence'] = {'reference_invoice_ids': [str(i) for i in range(30)]}
        small = confidence.score_invoice(few)['confidence']
        large = confidence.score_invoice(many)['confidence']
        self.assertGreaterEqual(large, small)
        self.assertLess(large, confidence.score_invoice(self.row())['confidence'])

    def test_one_weak_line_reduces_complete_invoice_confidence(self):
        row = self.row()
        strong = confidence.score_invoice(row)
        row['lines'].append(copy.deepcopy(self.row('price_pattern_inference')['lines'][0]))
        row['lines'][1]['line_id'] = 'line-2'
        row['expected_total_cents'] = row['billed_total_cents'] = 200
        weaker = confidence.score_invoice(row)
        self.assertLess(weaker['total_confidence'], strong['total_confidence'])
        self.assertLess(weaker['confidence'], strong['confidence'])

    def test_review_blocker_reduces_pricing_confidence(self):
        row = self.row()
        full = confidence.score_invoice(row)
        row['review_reasons'] = ['Patient history is unresolved']
        row['expected_total_cents'] = None
        row['lines'][0]['expected_line_total_cents'] = None
        row['flagged'] = 1
        row['error_category'] = 'invoice_total_mismatch'
        row['findings'] = [{'category': 'invoice_total_mismatch'}]
        partial = confidence.score_invoice(row)
        self.assertIsNone(partial['total_confidence'])
        self.assertLess(partial['confidence'], full['confidence'])

    def test_independent_error_supports_verdict_not_unresolved_corrected_total(self):
        row = self.row()
        row.update(flagged=1, error_category='invoice_total_mismatch', expected_total_cents=None,
                   review_reasons=['Unresolved service description on line-1'],
                   findings=[{'category': 'invoice_total_mismatch'}])
        row['lines'][0].update(expected_line_total_cents=None,
                               match={'service_name': None, 'status': 'review'})
        result = confidence.score_invoice(row)
        self.assertEqual(result['verdict_confidence'], .95)
        self.assertIsNone(result['total_confidence'])
        self.assertLessEqual(result['confidence'], .5)
        self.assertGreater(result['verdict_confidence'], result['confidence'])

    def test_strongest_error_does_not_raise_confidence_in_all_error_categories(self):
        row = self.row('price_pattern_inference')
        row.update(flagged=1, error_category='invoice_total_mismatch|unit_price_mismatch',
                   findings=[{'category': 'invoice_total_mismatch'},
                             {'category': 'unit_price_mismatch', 'line_id': 'line-1'}])
        result = confidence.score_invoice(row)
        self.assertEqual(result['verdict_confidence'], .95)
        self.assertLess(result['category_confidence'], result['verdict_confidence'])
        self.assertLessEqual(result['confidence'], result['category_confidence'])
        self.assertLessEqual(result['confidence'], result['total_confidence'])

    def test_service_day_assumption_reduces_pricing_not_independent_arithmetic(self):
        row = self.row()
        row.update(flagged=1, error_category='invoice_total_mismatch',
                   findings=[{'category': 'invoice_total_mismatch'}])
        original = confidence.score_invoice(row)
        row['service_day_assumption'] = {'assumption': 'Use service_date as billing-day label'}
        conditional = confidence.score_invoice(row)
        self.assertEqual(conditional['verdict_confidence'], original['verdict_confidence'])
        self.assertLess(conditional['total_confidence'], original['total_confidence'])
        self.assertLess(conditional['confidence'], original['confidence'])
        self.assertNotEqual(conditional['confidence_reasons'], original['confidence_reasons'])

    def test_quarantine_penalty_requires_actual_excluded_history(self):
        row = self.row()
        baseline = confidence.score_invoice(row)
        row['audit_policy'] = {'quarantine_invalid_history_dates': True}
        self.assertEqual(confidence.score_invoice(row), baseline)
        row['lines'][0]['quarantined_history'] = [{
            'line_id': 'historical-line', 'raw_service_date': 'not-a-date',
            'assumption': 'Invalid dated evidence is excluded, not repaired.',
        }]
        quarantined = confidence.score_invoice(row)
        self.assertLess(quarantined['total_confidence'], baseline['total_confidence'])
        self.assertLess(quarantined['confidence'], baseline['confidence'])
        self.assertTrue(any('quarantine' in reason.lower()
                            for reason in quarantined['confidence_reasons']))

    def test_unresolved_contract_or_history_context_caps_the_pricing_score(self):
        for update in ({'pricing_context': {'review': 'Amendment date/basis unresolved'}},
                       {'history_dependency_reasons': ['Bundle history is incomplete']}):
            with self.subTest(update=update):
                row = self.row()
                row['lines'][0].update(update)
                result = confidence.score_invoice(row)
                self.assertLessEqual(result['total_confidence'], .5)
                self.assertLessEqual(result['confidence'], .5)
                self.assertTrue(any('uncertain' in reason.lower()
                                    for reason in result['confidence_reasons']))

    def test_review_reasons_cap_even_a_populated_corrected_total(self):
        row = self.row()
        row['review_reasons'] = ['Contract unit basis ambiguous on line-1']
        result = confidence.score_invoice(row)
        self.assertLessEqual(result['total_confidence'], .5)
        self.assertLessEqual(result['confidence'], .5)

    def test_exports_clear_stale_numeric_scores_for_duplicate_or_unknown_records(self):
        for identity in ({'flagged': 1, 'record_identity_ambiguous': True}, {'flagged': None}):
            with self.subTest(identity=identity):
                row = {**self.row(), 'hospital_id': 'H2', 'record_index': 0, **identity}
                for field in ('confidence', 'verdict_confidence', 'total_confidence', 'category_confidence'):
                    row[field] = .99
                self.assertIsNone(reporting.submission_confidence(row))
                exported = reporting.all_record_export([row])['rows'][0]
                for field in ('confidence', 'verdict_confidence', 'total_confidence', 'category_confidence'):
                    self.assertIsNone(exported[field])

    def test_raw_similarity_and_model_self_confidence_are_not_probabilities(self):
        row = self.row('ai_description_mapping')
        baseline = confidence.score_invoice(row)
        row['label'] = {'is_erroneous': 1, 'expected_total_cents': 999999}
        row['lines'][0]['match'].update(similarity=.99999, model_confidence=1.0)
        row['lines'][0]['match']['resolution_evidence'] = {'confidence': 1.0}
        self.assertEqual(confidence.score_invoice(row), baseline)

    def test_ranked_fuzzy_alternatives_are_not_all_treated_as_unresolved_matches(self):
        row = self.row()
        row['lines'][0]['match'].pop('method')
        row['lines'][0]['match'].update(similarity=.95, margin=.3,
            candidates=[{'service_name': name, 'similarity': similarity}
                        for name, similarity in [('Alpha Consultation', .95), ('Beta Panel', .65), ('Gamma Session', .4)]])
        result = confidence.score_invoice(row)
        self.assertIsNotNone(result['total_confidence'])
        self.assertGreater(result['confidence'], 0)
        self.assertNotEqual(result['confidence'], .95)

    def test_official_export_uses_precomputed_score_and_fails_closed_without_it(self):
        row = self.row()
        self.assertIsNone(reporting.submission_confidence(row))
        row.update(confidence.score_invoice(row))
        self.assertEqual(reporting.submission_confidence(row), row['confidence'])

    def test_duplicate_submission_uses_unique_latest_date_and_cautious_claim(self):
        columns = ['invoice_id', 'flagged', 'error_category', 'expected_total_cents',
                   'billed_total_cents', 'confidence']
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            (root / 'invoices').mkdir()
            invoices = [
                {'invoice_id': 'dup', 'invoice_date': '2024-01-01'},
                {'invoice_id': 'unique', 'invoice_date': '2024-01-02'},
                {'invoice_id': 'dup', 'invoice_date': '2024-02-01'},
            ]
            (root / 'invoices/hospital_2_invoices.jsonl').write_text(
                ''.join(json.dumps(row) + '\n' for row in invoices))
            base = {'hospital_id': 'H2', 'flagged': 1, 'error_category': 'duplicate_invoice_id',
                    'expected_total_cents': None, 'confidence': None,
                    'record_identity_ambiguous': True}
            rows = [{**base, 'record_index': 0, 'invoice_id': 'dup', 'billed_total_cents': 100},
                    {'hospital_id': 'H2', 'record_index': 1, 'invoice_id': 'unique',
                     'flagged': 0, 'error_category': '', 'expected_total_cents': 200,
                     'billed_total_cents': 200, 'confidence': .8,
                     'record_identity_ambiguous': False},
                    {**base, 'record_index': 2, 'invoice_id': 'dup', 'billed_total_cents': 300}]
            result = reporting.submission_rows(rows, columns, root=root)
        self.assertEqual([row['invoice_id'] for row in result], ['unique', 'dup'])
        self.assertEqual(result[-1], {'invoice_id': 'dup', 'flagged': 1,
            'error_category': 'duplicate_invoice_id', 'expected_total_cents': None,
            'billed_total_cents': 300, 'confidence': .5})

    def test_duplicate_submission_abstains_on_tied_latest_dates(self):
        columns = ['invoice_id', 'flagged', 'error_category', 'expected_total_cents',
                   'billed_total_cents', 'confidence']
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            (root / 'invoices').mkdir()
            invoices = [{'invoice_id': 'dup', 'invoice_date': '2024-01-01'}] * 2
            (root / 'invoices/hospital_2_invoices.jsonl').write_text(
                ''.join(json.dumps(row) + '\n' for row in invoices))
            rows = [{'hospital_id': 'H2', 'record_index': i, 'invoice_id': 'dup',
                     'flagged': 1, 'error_category': 'duplicate_invoice_id',
                     'expected_total_cents': None, 'billed_total_cents': 100 + i,
                     'confidence': None, 'record_identity_ambiguous': True} for i in range(2)]
            self.assertEqual(reporting.submission_rows(rows, columns, root=root), [])


# Public runner and portable exports

class PublicMainTests(unittest.TestCase):

    def test_compare_dispatches_explicit_predictions_without_audit(self):
        predictions = self.directory / 'predictions.csv'
        with patch.object(main, 'compare_main') as compare, \
                patch.object(main.subprocess, 'run') as run, \
                patch.object(main, 'export_results') as export:
            main.main(['--compare', '--predictions', str(predictions)])
        compare.assert_called_once_with(['--predictions', str(predictions)])
        run.assert_not_called()
        export.assert_not_called()

    def test_verify_dispatches_output_and_baseline_without_audit(self):
        baseline = self.directory / 'prior'
        with patch.object(main, 'verify_main') as verify, \
                patch.object(main.subprocess, 'run') as run, \
                patch.object(main, 'export_results') as export:
            main.main(['--verify', str(self.directory), '--baseline', str(baseline)])
        verify.assert_called_once_with([str(self.directory), '--baseline', str(baseline)])
        run.assert_not_called()
        export.assert_not_called()

    def test_incompatible_reporting_options_fail_before_dispatch(self):
        for options in (['--compare', '--verify', str(self.directory)],
                        ['--predictions', 'predictions.csv'], ['--baseline', str(self.directory)]):
            with self.subTest(options=options), redirect_stderr(io.StringIO()), \
                    patch.object(main.subprocess, 'run') as run, \
                    patch.object(main, 'compare_main') as compare, \
                    patch.object(main, 'verify_main') as verify:
                with self.assertRaises(SystemExit) as error:
                    main.main(options)
                self.assertEqual(error.exception.code, 2)
                run.assert_not_called()
                compare.assert_not_called()
                verify.assert_not_called()

    def test_offline_command_keeps_fixed_policy_and_resolves_paths(self):
        args = main.parse_args(['--output-dir', 'relative-results',
                                '--ai-cache-dir', 'relative-cache', '--no-agent-replay'])
        self.assertEqual(main.build_audit_command(args, None), [
            sys.executable, str(ROOT / 'pipeline.py'),
            '--invoice-ai', '--four-blocker-fixes', '--h2-service-date-as-billing-day',
            '--budget-usd', '2', '--ai-cache-dir', str(Path('relative-cache').resolve()),
            '--output-dir', str(Path('relative-results').resolve()), '--offline-ai',
        ])

    def test_main_runs_local_tests_then_offline_pipeline_and_exports_all_records(self):
        self.write_audit_outputs()
        output = io.StringIO()
        with patch.object(main.subprocess, 'run') as run, redirect_stdout(output):
            main.main(['--output-dir', str(self.directory), '--no-agent-replay', '--baseline-only'])
        self.assertEqual(run.call_count, 2)
        self.assertEqual(run.call_args_list[0], call(
            [sys.executable, str(ROOT / 'test.py'), '-q'], cwd=ROOT, check=True,
        ))
        command = run.call_args_list[1].args[0]
        self.assertEqual(command[:2], [sys.executable, str(ROOT / 'pipeline.py')])
        self.assertIn('--offline-ai', command)
        self.assertNotIn('--agent-replay-dir', command)
        self.assertEqual(run.call_args_list[1].kwargs, {'cwd': ROOT, 'check': True})
        self.assertEqual(len(self.read_json('submit_all_data.json')['rows']), 4)
        self.assertEqual(self.read_json('remaining_blockers.json')['eligible_unanswered'], 1)
        context = self.read_json('test_all_context.json')
        self.assertEqual(set(context), {
            'policy_revision', 'offline_ai', 'shared_ledger', 'agent_replay_dir', 'source_hashes',
        })
        self.assertEqual(context['policy_revision'], 'four_blockers_v2')
        self.assertTrue(context['offline_ai'])
        self.assertIsNone(context['agent_replay_dir'])
        self.assertEqual(context['shared_ledger'], str(ROOT / 'audit_output/all_hospitals_ai_2usd/ai'))
        self.assertEqual({path.name for path in self.directory.glob('*.csv')}, {
            'submission.csv',
        })
        self.assertIn('All 4 raw records retained. Eligible unique records answered: 2', output.getvalue())
        self.assertIn('H2: answered 2, not answered 1, duplicate-ID records 1 (1 repeated IDs)', output.getvalue())
        self.assertIn(f'Results: {self.directory.resolve()}', output.getvalue())

    def test_explicit_options_forward_and_json_only_skips_csv(self):
        self.write_audit_outputs()
        cache = self.directory / 'shared-cache'
        clarifications = self.directory / 'clarifications.json'
        options = [
            '--execute-ai', '--output-dir', str(self.directory), '--ai-cache-dir', str(cache),
            '--budget-usd', '1.5', '--unit-clarifications', str(clarifications),
            '--agent-replay-dir', str(self.directory), '--json-only', '--baseline-only',
        ]
        with patch.object(main.subprocess, 'run') as run, \
                patch.object(main, 'portable_csv_export') as export, redirect_stdout(io.StringIO()):
            main.main(options)
        self.assertEqual(run.call_args_list[1].args[0], [
            sys.executable, str(ROOT / 'pipeline.py'),
            '--invoice-ai', '--four-blocker-fixes', '--h2-service-date-as-billing-day',
            '--budget-usd', '1.5', '--ai-cache-dir', str(cache.resolve()),
            '--output-dir', str(self.directory.resolve()),
            '--unit-clarifications', str(clarifications.resolve()),
            '--agent-replay-dir', str(self.directory.resolve()),
        ])
        export.assert_not_called()
        context = self.read_json('test_all_context.json')
        self.assertFalse(context['offline_ai'])
        self.assertEqual(context['shared_ledger'], str(cache.resolve()))
        self.assertEqual(context['agent_replay_dir'], str(self.directory.resolve()))
        self.assertTrue((self.directory / 'submit_all_data.json').is_file())

    def test_source_provenance_hashes_the_active_modules(self):
        hashes = main.source_hashes()
        self.assertEqual(set(hashes), {
            'main.py', 'pipeline.py', 'agents.py', 'rules.py', 'matching.py',
            'contracts.py', 'contract_builder.py', 'ai_client.py', 'reporting.py',
            'confidence.py', 'test.py', 'complete_workflow.py', 'reference_experiment.py',
            'unit_quantity_experiment.py', 'history_quantity_experiment.py', 'merge_h2_results.py',
        })
        for name, digest in hashes.items():
            self.assertEqual(digest, hashlib.sha256((ROOT / name).read_bytes()).hexdigest())

    def test_help_exits_before_running_tests_audit_or_exports(self):
        output = io.StringIO()
        with patch.object(main.subprocess, 'run') as run, \
                patch.object(main, 'export_results') as export, redirect_stdout(output):
            with self.assertRaises(SystemExit) as result:
                main.main(['--help'])
        self.assertEqual(result.exception.code, 0)
        self.assertIn('--execute-ai', output.getvalue())
        run.assert_not_called()
        export.assert_not_called()

    def test_main_file_cli_is_usable_from_another_directory(self):
        result = subprocess.run(
            [sys.executable, str(ROOT / 'main.py'), '--help'],
            cwd=self.directory, check=True, text=True, capture_output=True,
        )
        self.assertIn('--execute-ai', result.stdout)
        self.assertIn('--output-dir', result.stdout)
        self.assertEqual(list(self.directory.iterdir()), [])

    def test_public_modules_resolve_to_active_root_files(self):
        modules = (main, pipeline, agents, rules, matching, contracts, contract_builder, ai_client, reporting, confidence)
        for module in modules:
            with self.subTest(module=module.__name__):
                self.assertEqual(Path(module.__file__).resolve().parent, ROOT)
        self.assertNotIn(str(ROOT / 'scripts'), sys.path)

    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.directory = Path(self.temp.name)

    def write_json(self, filename, value):
        (self.directory / filename).write_text(json.dumps(value), encoding='utf-8')

    def read_json(self, filename):
        return json.loads((self.directory / filename).read_text())

    def rows(self):
        base = {'hospital_id': 'H2', 'record_index': 0, 'invoice_id': 'invoice-1',
                'flagged': 0, 'record_identity_ambiguous': False, 'error_category': '',
                'expected_total_cents': 100, 'billed_total_cents': 100,
                'review_reasons': [], 'findings': [],
                'lines': [{'line_id': 'line-1', 'expected_line_total_cents': 100,
                           'match': {'service_name': 'Alpha Consultation', 'status': 'accepted',
                                     'method': 'full_catalogue_token_constraints'}}]}
        rows = [base,
                {**base, 'record_index': 1, 'invoice_id': 'invoice-2', 'flagged': 1,
                 'error_category': 'invoice_total_mismatch', 'billed_total_cents': 110,
                 'findings': [{'category': 'invoice_total_mismatch'}]},
                {**base, 'record_index': 2, 'invoice_id': 'same,"identifier"', 'flagged': None,
                 'review_reasons': ['Unresolved service description, "unclear"\nسطر آخر', 'Needs review']},
                {**base, 'record_index': 3, 'invoice_id': 'same,"identifier"', 'flagged': 1,
                 'record_identity_ambiguous': True, 'review_reasons': ['Duplicate identifier'],
                 'assessment_basis': 'Source, "quoted"'}]
        return [{**row, **confidence.score_invoice(row)} for row in rows]

    def write_audit_outputs(self):
        rows = self.rows()
        columns = ['invoice_id', 'flagged', 'error_category', 'expected_total_cents', 'billed_total_cents', 'confidence']
        submission = {'columns': columns, 'rows': [{**{key: row[key] for key in columns if key != 'confidence'}, 'confidence': reporting.submission_confidence(row)} for row in rows[:2]]}
        unanswered = [{**{key: row[key] for key in ('hospital_id', 'record_index', 'invoice_id')}, 'reason': ' | '.join(row['review_reasons'])} for row in rows[2:]]
        self.write_json('all_results.json', rows)
        self.write_json('submission_data.json', submission)
        self.write_json('unanswered.json', unanswered)
        self.write_json('summary.json', {'hospitals': [{'hospital_id': 'H2', 'answered': 2, 'unanswered': 1, 'excluded_duplicate_id_records': 1}], 'unanswered_unique_invoices': 1, 'excluded_duplicate_id_records': 1, 'api_budget': {'spent_usd': 0, 'budget_usd': 2}})
        return (rows, submission, unanswered)

    def test_defaults_preserve_cache_budget_and_output_naming(self):
        args = main.parse_args([])
        self.assertFalse(args.execute_ai)
        self.assertFalse(args.json_only)
        self.assertFalse(args.no_agent_replay)
        self.assertIsNone(args.agent_replay_dir)
        self.assertIsNone(args.unit_clarifications)
        self.assertEqual(args.budget_usd, 2)
        self.assertEqual(args.ai_cache_dir, ROOT / 'audit_output/all_hospitals_ai_2usd/ai')
        self.assertEqual(args.output_dir.parent, ROOT / 'audit_output')
        self.assertRegex(args.output_dir.name, '^test_all_\\d{8}_\\d{6}_\\d{6}$')

    def test_default_replay_is_optional_and_can_be_disabled(self):
        with patch.object(main, 'ROOT', self.directory):
            args = main.parse_args([])
            self.assertIsNone(main.resolve_replay_dir(args))
            prior = self.directory / 'audit_output/four_blockers_test_all_verified'
            prior.mkdir(parents=True)
            self.assertEqual(main.resolve_replay_dir(args), prior)
            self.assertIsNone(main.resolve_replay_dir(main.parse_args(['--no-agent-replay'])))
            explicit = main.parse_args(['--agent-replay-dir', str(self.directory)])
            self.assertEqual(main.resolve_replay_dir(explicit), self.directory)

    def test_invalid_replay_options_fail_before_any_subprocess(self):
        invalid_options = [['--agent-replay-dir', str(self.directory / 'missing')], ['--agent-replay-dir', str(self.directory), '--no-agent-replay']]
        for options in invalid_options:
            with self.subTest(options=options), redirect_stderr(io.StringIO()), patch.object(main.subprocess, 'run') as run:
                with self.assertRaises(SystemExit) as error:
                    main.main(options)
                self.assertEqual(error.exception.code, 2)
                run.assert_not_called()

    def test_all_record_schema_preserves_abstentions_and_duplicate_records(self):
        exported = reporting.all_record_export(self.rows())
        self.assertEqual(exported['columns'], ['hospital_id', 'record_index', 'invoice_id', 'status', 'flagged', 'error_category', 'expected_total_cents', 'billed_total_cents', 'confidence', 'reason', 'assumptions', 'verdict_confidence', 'total_confidence', 'category_confidence', 'confidence_method', 'confidence_reasons'])
        rows = exported['rows']
        self.assertEqual([row['record_index'] for row in rows], [0, 1, 2, 3])
        self.assertEqual([row['flagged'] for row in rows], [0, 1, 'not answered', 'not answered'])
        self.assertEqual([row['confidence'] for row in rows],
                         [row['confidence'] for row in self.rows()])
        self.assertEqual([row['confidence'] for row in rows[2:]], [None, None])
        for source, exported in zip(self.rows(), rows):
            for field in ('verdict_confidence', 'total_confidence', 'category_confidence', 'confidence_method'):
                self.assertEqual(exported[field], source[field])
            self.assertEqual(exported['confidence_reasons'], ' | '.join(source['confidence_reasons']))
        self.assertEqual([row['expected_total_cents'] for row in rows], [100, 100, None, None])
        self.assertEqual(rows[2]['invoice_id'], rows[3]['invoice_id'])
        self.assertEqual(rows[2]['reason'], 'Unresolved service description, "unclear"\nسطر آخر | Needs review')
        self.assertEqual(rows[0]['assumptions'], '')
        self.assertEqual(rows[3]['assumptions'], 'Source, "quoted"')

    def test_only_template_csv_is_written_and_round_trips_quotes_and_newlines(self):
        rows, submission, _ = self.write_audit_outputs()
        self.write_json('submit_all_data.json', reporting.all_record_export(rows))
        submission['rows'][0]['invoice_id'] = 'identifier,"quoted"\nسطر آخر'
        self.write_json('submission_data.json', submission)
        reporting.portable_csv_export(self.directory)
        with (ROOT / 'submission_template.csv').open(newline='', encoding='utf-8') as stream:
            template_columns = next(csv.reader(stream))
        with (self.directory / 'submission.csv').open(newline='', encoding='utf-8') as stream:
            reader = csv.DictReader(stream)
            self.assertEqual(reader.fieldnames, template_columns)
            self.assertEqual(reader.fieldnames, submission['columns'])
            self.assertEqual(len(reader.fieldnames), 6)
            self.assertEqual(list(reader), [
                {key: '' if row[key] is None else str(row[key]) for key in template_columns}
                for row in submission['rows']])
        self.assertEqual({path.name for path in self.directory.glob('*.csv')}, {'submission.csv'})
        self.assertFalse((self.directory / 'submit_all.csv').exists())
        self.assertFalse((self.directory / 'unanswered.csv').exists())
        with self.assertRaises(FileExistsError):
            reporting.portable_csv_export(self.directory)

    def test_csv_rejects_extra_or_reordered_template_columns(self):
        for change in ('extra', 'reordered'):
            with self.subTest(change=change):
                _, submission, _ = self.write_audit_outputs()
                if change == 'extra':
                    submission['columns'].append('confidence_reasons')
                else:
                    submission['columns'] = list(reversed(submission['columns']))
                self.write_json('submission_data.json', submission)
                with self.assertRaises(ValueError):
                    reporting.portable_csv_export(self.directory)
                self.assertFalse((self.directory / 'submission.csv').exists())

    def test_failed_tests_or_audit_stop_before_export(self):
        failure = subprocess.CalledProcessError(1, ['synthetic-test-or-audit'])
        for outcomes in ([failure], [None, failure]):
            with self.subTest(failed_process=len(outcomes)), patch.object(main.subprocess, 'run', side_effect=outcomes) as run, patch.object(main, 'export_results') as export:
                with self.assertRaises(subprocess.CalledProcessError):
                    main.main(['--output-dir', str(self.directory), '--no-agent-replay'])
                self.assertEqual(run.call_count, len(outcomes))
                export.assert_not_called()

class VolumeFamilyScopeTests(unittest.TestCase):
    def catalogue(self):
        return [{'service_name': name} for name in (
            'Standard Renal Dialysis Session', 'Advanced Renal Dialysis Session',
            'Preoperative Obstetric Home Visit', 'Routine Cardiac Home Visit')]

    def test_family_is_broad_not_an_identity(self):
        scope = matching.history_family_scope('Unknown MSK Dial Sess', self.catalogue())
        self.assertEqual(len(scope['candidate_services']), 2)
        self.assertNotIn('service_name', scope)
        self.assertIn('outside-catalogue', scope['assumption'])

    def test_consultation_family_is_opt_in_and_not_service_identity(self):
        catalogue = [{'service_name': 'Routine Psychiatric Consultation'}, {'service_name': 'Elective Pulmonary Consultation'}]
        self.assertIsNone(matching.history_family_scope('foc rheum consultation', catalogue))
        scope = matching.history_family_scope('foc rheum consultation', catalogue, single_word_families=True)
        self.assertEqual(len(scope['candidate_services']), 2)
        self.assertNotIn('service_name', scope)

    def test_conference_head_bounds_history_without_imputing_case_identity(self):
        catalogue = [{'service_name': 'Bedside Oncology Case Conference'}, {'service_name': 'Routine Cardiac Consultation'}]
        scope = matching.history_family_scope('conf preop paed', catalogue, single_word_families=True)
        self.assertEqual(scope['candidate_services'], ['Bedside Oncology Case Conference'])
        self.assertEqual(scope['family_token_expansions']['conf'], 'conference')
        self.assertNotIn('service_name', scope)

    def test_multiple_complete_families_are_unioned(self):
        scope = matching.history_family_scope('Dial Sess and Home Visit', self.catalogue())
        self.assertEqual(len(scope['candidate_services']), 4)

    def test_history_family_unique_prefix_is_not_identity(self):
        catalogue = self.catalogue() + [{'service_name': 'Focused Pulmonary Pharmaceutical Dispensing'}]
        scope = matching.history_family_scope('FOC VASC PHARM DISP', catalogue, {'pharm': 'pharmaceutical'})
        self.assertEqual(scope['candidate_services'], ['Focused Pulmonary Pharmaceutical Dispensing'])
        self.assertEqual(scope['family_token_expansions'], {'disp': 'dispensing'})
        self.assertNotIn('service_name', scope)

    def test_history_family_ambiguous_prefix_is_not_expanded(self):
        catalogue = [{'service_name': 'Focused Pulmonary Pharmaceutical Dispensing'},
                     {'service_name': 'Routine Renal Pharmaceutical Disposal'}]
        self.assertIsNone(matching.history_family_scope('PHARM DISP', catalogue, {'pharm': 'pharmaceutical'}))

    def test_incomplete_or_conflicting_family_word_does_not_narrow(self):
        for text in ('Dial', 'Dial Sess Home', 'Unrecognised service'):
            self.assertIsNone(matching.history_family_scope(text, self.catalogue()))

    def test_only_unrelated_discount_is_unblocked_and_assumption_retained(self):
        c = history_contract()
        c['services'] += [{**s, 'unit_basis': 'per_item', 'base_rate_cents': 101, 'daily_cap': None}
                          for s in self.catalogue()]
        set_history_discount(c, (5, 20), (12, 40))
        earlier = sample_invoice('prior', quantity=10, day='2024-01-05')
        unknown = sample_invoice('unknown', service='Unlisted Renal Dialysis Session', quantity=5, day='2024-01-06')
        unknown['line_items'][0]['unit_basis_as_billed'] = 'per_item'
        target = sample_invoice('target', price=81, day='2024-01-07')
        old = audit_history(c, [earlier, unknown, target])
        self.assertIsNone(old[2]['flagged'])
        c['uncertainty_policy']['volume_family_bounds'] = True
        new = audit_history(c, [earlier, unknown, target])
        self.assertEqual(new[2]['flagged'], 0)
        self.assertIsNone(new[1]['lines'][0]['match']['service_name'])
        self.assertIsNone(new[1]['flagged'])
        line = new[2]['lines'][0]
        self.assertTrue(line['history_family_assumptions'])
        self.assertEqual(line['utilisation_bounds']['prior_units_lower'], 10)
        self.assertEqual(line['utilisation_bounds']['prior_units_upper'], 10)
        original = copy.deepcopy(line)
        original.pop('history_family_assumptions')
        self.assertAlmostEqual(confidence._line_evidence(original)[0] - confidence._line_evidence(line)[0], .05)


    def test_related_family_retains_discount_uncertainty(self):
        c = history_contract()
        name = 'Standard Renal Dialysis Session'
        c['services'][0]['service_name'] = name
        c['services'].append({**c['services'][0], 'service_name': 'Advanced Renal Dialysis Session'})
        set_history_discount(c, (5, 20), (12, 40))
        for rule in c['volume_discounts']['rules']:
            rule['service_name'] = name
        c['uncertainty_policy']['volume_family_bounds'] = True
        earlier = sample_invoice('prior', service=name, quantity=10, day='2024-01-05')
        unknown = sample_invoice('unknown', service='Unlisted MSK Dial Sess', quantity=5, day='2024-01-06')
        target = sample_invoice('target', service=name, price=81, day='2024-01-07')
        result = audit_history(c, [earlier, unknown, target])[2]
        self.assertIsNone(result['flagged'])
        self.assertFalse(result['lines'][0].get('history_family_assumptions'))


class ConditionalMergeTests(unittest.TestCase):
    def test_preserves_other_hospitals_and_rejects_repeated_overlay(self):
        row = {'hospital_id': 'H2', 'record_index': 0, 'invoice_id': 'h2', 'flagged': None,
               'review_reasons': ['unit unresolved'], 'confidence_reasons': [], 'confidence': .35,
               'assessment_basis': 'baseline'}
        other = {**row, 'hospital_id': 'H3', 'invoice_id': 'h3'}
        proposal = {'record_index': 0, 'invoice_id': 'h2', 'conditional_flagged': 0,
                    'conditional_errors': [], 'conditional_expected_total_cents': 100,
                    'assumption': 'Billed quantity assumed'}
        merged = merge_h2_results.merge_rows([row, other], [], [proposal])
        self.assertEqual(merged[1], other)
        self.assertIsNone(row['flagged'])
        self.assertEqual(merged[0]['flagged'], 0)
        self.assertEqual(merged[0]['confidence'], .35)
        self.assertFalse(merged[0]['quantity_verified'])
        with self.assertRaises(ValueError):
            merge_h2_results.merge_rows([row], [], [proposal, proposal])


class BilledQuantityExperimentTests(unittest.TestCase):
    def test_nursing_pair_bounds_do_not_identify_service(self):
        services = [{'service_name': n} for n in (
            'Inpatient Dermatologic Nursing Observation',
            'Specialist Dermatologic Nursing Observation',
            'Extended Endocrine Theatre Time',
            'Continuous Metabolic Endoscopic Procedure')]
        scope = matching.history_family_scope('INTERM ENDO NURS OBS', services)
        self.assertEqual(len(scope['candidate_services']), 2)
        self.assertNotIn('Continuous Metabolic Endoscopic Procedure', scope['candidate_services'])
        self.assertEqual(scope['family_token_expansions']['obs'], 'observation')
        self.assertIsNone(matching.history_family_scope('INTERM ENDO OBS', services))
        self.assertIsNone(matching.history_family_scope('NURS OBS endoscopic', services))

    def test_h5_source_and_pricing_context(self):
        draft = json.loads((ROOT / 'contract_agent_final_v4/hospital_5.json').read_text())
        source = (ROOT / 'contracts/hospital_5/network_reimbursement_agreement.txt').read_text()
        clause = next(s for s in source.splitlines() if s.startswith('3.1 '))
        self.assertIn('resulting unit rate multiplied by the billed quantity', clause)
        service = {**self.service, 'service_name': 'Routine Psychiatric Telemetry Monitoring',
                   'base_rate_cents': 2950}
        raw = {**self.raw, 'service_date': '2024-05-01', 'unit_price_cents': 2950,
               'line_total_cents': 17700}
        for facility in ('F-MAIN', 'F-NORTH', 'F-COAST'):
            for tier in ('BRONZE', 'SILVER', 'GOLD'):
                result = unit_quantity_experiment.calculate_billed_quantity(
                    self.contract, draft, {'facility_code': facility, 'plan_tier': tier}, raw, service)
                self.assertEqual(result['conditional_expected_line_total_cents'], 17700)
                self.assertEqual(result['conditional_errors'], [])
                self.assertFalse(result['quantity_verified'])

    def test_paired_lab_abbreviation_is_only_a_family(self):
        services = [{'service_name': 'Continuous Immunologic Laboratory Panel'},
                    {'service_name': 'Supervised Immunologic Sterilisation Service'}]
        scope = matching.history_family_scope('ASSISTED GERIATRIC LAB PNL', services)
        self.assertEqual(scope['candidate_services'], ['Continuous Immunologic Laboratory Panel'])
        self.assertIsNone(matching.history_family_scope('ASSISTED GERIATRIC LAB', services))

    def test_multiplier_rounding_is_stepwise(self):
        context = {'facility_multiplier': {'numerator': 3, 'denominator': 2},
                   'plan_tier_multiplier': {'numerator': 3, 'denominator': 2}}
        service = {**self.service, 'base_rate_cents': 1}
        raw = {**self.raw, 'quantity': 1, 'unit_price_cents': 3, 'line_total_cents': 3}
        with patch.object(contracts, 'pricing_context', return_value=context):
            result = unit_quantity_experiment.calculate_billed_quantity(self.contract, {}, {}, raw, service)
        self.assertEqual(result['effective_rate_cents'], 3)

    def setUp(self):
        self.contract = {'contract_details': {'facility_multiplier': {'numerator': 1, 'denominator': 1},
                                             'plan_tier_multiplier': {'numerator': 1, 'denominator': 1}}}
        self.service = {'service_name': 'Telemetry', 'unit_basis': None, 'base_rate_cents': 10625,
                        'source': {'text': 'GBP 106.25 per hour, per item'}}
        self.raw = {'line_id': 'x', 'quantity': 6, 'unit_basis_as_billed': 'per_hour_per_item',
                    'unit_price_cents': 10625, 'line_total_cents': 63750}

    def calculate(self):
        with patch.object(contracts, 'pricing_context', return_value={}):
            return unit_quantity_experiment.calculate_billed_quantity(self.contract, {}, {}, self.raw, self.service)

    def test_calculates_without_confirming_or_changing_unit(self):
        result = self.calculate()
        self.assertEqual(result['conditional_expected_line_total_cents'], 63750)
        self.assertEqual(result['conditional_errors'], [])
        self.assertFalse(result['quantity_verified'])
        self.assertIsNone(self.service['unit_basis'])

    def test_detects_bad_rate_and_multiplication(self):
        self.raw['unit_price_cents'] = 10000
        self.assertEqual(set(self.calculate()['conditional_errors']), {'unit_price_mismatch', 'line_total_arithmetic'})

    def test_does_not_invent_conversion_or_skip_dependencies(self):
        self.raw['unit_basis_as_billed'] = 'per_hour'
        with self.assertRaises(ValueError):
            self.calculate()
        self.raw['unit_basis_as_billed'] = 'per_hour_per_item'
        self.contract['volume_discounts'] = {'rules': [{'service_name': 'Telemetry', 'quantity': 100}]}
        with self.assertRaises(ValueError):
            self.calculate()

    def test_related_exclusion_presence_allowed_but_excluded_service_rejected(self):
        self.contract['exclusion_windows'] = {'rules': [{'related_service': 'Telemetry', 'excluded_service': 'Lab', 'window_days': 30}]}
        self.assertEqual(self.calculate()['conditional_expected_line_total_cents'], 63750)
        self.contract['exclusion_windows']['rules'][0].update(related_service='Lab', excluded_service='Telemetry')
        with self.assertRaises(ValueError):
            self.calculate()


class IndependentReferenceTests(unittest.TestCase):
    def fixture(self):
        anchors = [{'invoice_id': n + str(i), 'service_name': n, 'unit': u, 'price': p}
                   for n, u, p in [('A', 'per_item', 100), ('B', 'per_hour', 200)] for i in range(3)]
        obs = [{'invoice_id': 'r' + str(i), 'unit': 'per_item', 'price': 100} for i in range(5)]
        return obs, anchors

    def choose(self, obs, anchors):
        return reference_experiment.choose_reference(['A', 'B'], obs, anchors,
                    {'A': [100], 'B': [200]}, {'A': 'per_item', 'B': 'per_hour'})

    def test_clear_anchors_and_other_invoice_agreement(self):
        obs, anchors = self.fixture()
        chosen, evidence = self.choose(obs, anchors)
        self.assertEqual(chosen, 'A')
        self.assertEqual(len(evidence['reference_invoice_ids']), 5)

    def test_duplicate_lines_do_not_create_independent_support(self):
        obs, anchors = self.fixture()
        self.assertIsNone(self.choose([obs[0]] * 20, anchors)[0])
        self.assertIsNone(self.choose(obs, [anchors[0]] * 20 + anchors[3:])[0])

    def test_conflict_or_unwitnessed_rate_abstains(self):
        obs, anchors = self.fixture()
        obs[0].update(unit='per_hour', price=200)
        obs[1].update(unit='per_hour', price=200)
        self.assertIsNone(self.choose(obs, anchors)[0])
        obs, anchors = self.fixture()
        for a in anchors[:3]:
            a['price'] = 999
        self.assertIsNone(self.choose(obs, anchors)[0])


class ClarificationPacketTests(unittest.TestCase):
    def test_unit_requests_group_without_changing_verdicts(self):
        contract = {'services': [{'service_name': 'Telemetry', 'unit_basis': None, 'source': {'line_start': 4}}]}
        rows = [{'hospital_id': 'H2', 'record_index': i, 'invoice_id': str(i), 'flagged': None,
                 'lines': [{'line_id': str(i), 'description': 'MONIT', 'match': {'service_name': 'Telemetry'}}]}
                for i in range(3)]
        original = copy.deepcopy(rows)
        packets = reporting.clarification_packets(rows, contract)
        self.assertEqual(len(packets), 1)
        self.assertEqual(packets[0]['affected_invoice_count'], 3)
        self.assertEqual(rows, original)
        self.assertIn('authoritative', packets[0]['question'])

    def test_description_singleton_never_promotes_or_leads_question(self):
        contract = {'services': [{'service_name': 'Transport', 'unit_basis': 'per_item'}]}
        rows = [{'hospital_id': 'H2', 'record_index': 0, 'invoice_id': 'a', 'flagged': None,
                 'lines': [{'line_id': 'x', 'description': 'SVC', 'match': {'service_name': None,
                            'candidates': [{'service_name': 'Transport'}]}}]}]
        packet = reporting.clarification_packets(rows, contract)[0]
        self.assertEqual(packet['status'], 'awaiting_authoritative_evidence')
        self.assertNotIn('Transport', packet['question'])
        self.assertIsNone(rows[0]['flagged'])
        rows[0]['record_identity_ambiguous'] = True
        self.assertEqual(reporting.clarification_packets(rows, contract), [])
        rows[0]['record_identity_ambiguous'] = False
        rows[0]['flagged'] = 1
        self.assertEqual(reporting.clarification_packets(rows, contract), [])


class CheckoutPortabilityTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.recorded = {}
        for name in pipeline.H1_BASELINE_INPUTS:
            path = self.root / name
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_bytes(name.encode())
            self.recorded['/unavailable/original/repo/' + name] = hashlib.sha256(name.encode()).hexdigest()

    def test_old_absolute_paths_use_current_checkout_and_original_hashes(self):
        with patch.object(pipeline, 'ROOT', self.root):
            pipeline.verify_h1_baseline_inputs(self.recorded)
            relative = {name: self.recorded['/unavailable/original/repo/' + name]
                        for name in pipeline.H1_BASELINE_INPUTS}
            pipeline.verify_h1_baseline_inputs(relative)

    def test_modified_current_input_still_fails(self):
        (self.root / pipeline.H1_BASELINE_INPUTS[0]).write_bytes(b'changed')
        with patch.object(pipeline, 'ROOT', self.root), self.assertRaisesRegex(ValueError, 'inputs changed'):
            pipeline.verify_h1_baseline_inputs(self.recorded)

    def test_missing_extra_and_duplicated_inputs_fail(self):
        missing = dict(self.recorded)
        missing.pop(next(iter(missing)))
        extra = {**self.recorded, '/outside/unknown.json': 'unused'}
        name = pipeline.H1_BASELINE_INPUTS[0]
        duplicate = {**self.recorded, name: self.recorded['/unavailable/original/repo/' + name]}
        for recorded in (missing, extra, duplicate):
            with self.subTest(recorded=list(recorded)), patch.object(pipeline, 'ROOT', self.root), self.assertRaises(ValueError):
                pipeline.verify_h1_baseline_inputs(recorded)

    def test_symlink_outside_checkout_fails(self):
        name = pipeline.H1_BASELINE_INPUTS[0]
        path = self.root / name
        path.unlink()
        with tempfile.TemporaryDirectory() as external:
            target = Path(external) / 'input.json'
            target.write_bytes(name.encode())
            path.symlink_to(target)
            with patch.object(pipeline, 'ROOT', self.root), self.assertRaisesRegex(ValueError, 'escapes'):
                pipeline.verify_h1_baseline_inputs(self.recorded)


if __name__ == '__main__':
    unittest.main()
