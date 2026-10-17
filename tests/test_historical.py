"""Offline import, assertions and denominator regressions; no remote calls."""
import copy
import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from degent.collection import collect, extend
from degent.evidence import decorate_observation, select_units
from degent.historical import import_collection, prepare_historical, validate_package
from degent.metadata import metadata_assertions
from degent.model_runner import method_signature
from degent.pay import pay_records
from degent.preparation_audit import frozen_packet_audit, identity_audit
from degent.store import digest, read_json, read_jsonl, write_json, write_jsonl
from degent.study import extract
from degent.study_metrics import assessed, report
from degent.taxonomy import approve


class HistoricalTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.package = self.root / 'package'
        self.package.mkdir()
        self.config = {'timezone': 'America/Chicago', 'study_version': 1,
                       'run_dir': str(self.root / 'runs'), 'watched_concepts': ['sql'],
                       'preparation_only': True, 'comparison_minimum': 1}
        self.text = ('Overview\nCenter 3 (19075), United States of America, McLean, Virginia\n'
                     'Risk Analyst\nResponsibilities\nAnalyze credit risk.\n'
                     'Basic Qualifications\nSQL required.\nBenefits\n401(k) match.\n')
        records, mappings = [], []
        original = self.package / 'original.html'
        original.write_text('<html>preserved source</html>')
        for i in range(3):
            version = 'v0' if i < 2 else 'v1'
            wording = '2020-02-01T12:00:00+00:00' if i < 2 else None
            text = self.text if i < 2 else self.text + 'Modern recruiting copy.'
            record = {'source_record_id': f'archvive:{version}:r{i}', 'company': 'Capital One',
                      'title': 'Risk Analyst', 'url': 'https://example.test/job/1', 'url_aliases': [],
                      'requisition_id': 'R1', 'provider': None, 'provider_posting_id': None,
                      'published_at': '2021-01-01', 'published_at_raw': '2021-1-1',
                      'published_at_provenance': 'JSON-LD datePosted', 'updated_at': None,
                      'updated_at_provenance': None, 'captured_at': f'2026-10-09T0{i}:00:00+00:00',
                      'captured_at_provenance': 'request log retrieval', 'wording_valid_at': wording,
                      'wording_valid_at_provenance': 'verified memento' if wording else None,
                      'locations': ['Add'], 'employment_type': 'DX', 'description': text,
                      'description_completeness': 'unknown', 'evidence_files': ['original.html'],
                      'notes': ['comparison_subset=true' if i < 2 else 'comparison_subset=false']}
            mapping = {'source_record_id': record['source_record_id'], 'version_key': version,
                       'posting_key': 'requisition:R1', 'request_key': f'r{i}',
                       'comparison_subset': i < 2, 'description_sha256': digest(text),
                       'verified_archive_capture_at': wording, 'original_evidence_files': ['original.html'],
                       'original_sha256': digest(original.read_bytes())}
            records.append(record)
            mappings.append(mapping)
        write_jsonl(self.package / 'historical-postings.jsonl', records)
        write_json(self.package / 'manifest.json', {'records': mappings,
            'files_sha256': {p.name: digest(p.read_bytes()) for p in self.package.iterdir()},
            'statistics': {'record_count': 3, 'canonical_version_count': 2,
                           'comparison_subset_record_count': 2}})

    def imported(self):
        return import_collection(self.package, self.root / 'collections', self.config)

    def test_import_is_immutable_idempotent_and_does_not_copy_approval(self):
        destination, rows = self.imported()
        before = (destination / 'observations.jsonl').read_bytes()
        repeated, again = self.imported()
        self.assertEqual(destination, repeated)
        self.assertEqual(rows, again)
        self.assertEqual(before, (destination / 'observations.jsonl').read_bytes())
        self.assertEqual(len(rows), 3)
        self.assertEqual(len({r['document_id'] for r in rows}), 2)
        self.assertEqual(len({r['vacancy_id'] for r in rows}), 2)
        self.assertTrue(all(r['identity_status'] == 'unresolved_historical_candidate' for r in rows))
        self.assertFalse(read_json(destination / 'receipt.json')['approvals_copied'])
        self.assertFalse((destination / 'taxonomy.json').exists())
        Path(rows[0]['clean_file']).write_text('tampered')
        with self.assertRaisesRegex(ValueError, 'Immutable imported evidence'):
            self.imported()

    def test_import_recovers_interruption_after_receipt_before_publication(self):
        rename = Path.rename
        failed = False
        def interrupted(path, target):
            nonlocal failed
            if path.name.startswith('.pending-') and not failed:
                failed = True
                raise OSError('simulated interruption')
            return rename(path, target)
        with patch.object(Path, 'rename', interrupted):
            with self.assertRaises(OSError):
                self.imported()
        destination, rows = self.imported()
        self.assertEqual(len(read_jsonl(destination / 'observations.jsonl')), 3)
        self.assertEqual(self.imported()[1], rows)

    def test_package_rejects_tampering_and_path_escape(self):
        (self.package / 'original.html').write_text('different')
        with self.assertRaisesRegex(ValueError, 'hash mismatch'):
            validate_package(self.package)
        manifest = read_json(self.package / 'manifest.json')
        manifest['files_sha256'] = {'../elsewhere': 'hash'}
        write_json(self.package / 'manifest.json', manifest)
        with self.assertRaisesRegex(ValueError, 'outside package'):
            validate_package(self.package)

    def test_historical_dates_never_use_modern_retrieval_as_wording(self):
        _, rows = self.imported()
        archive, modern = rows[0], rows[2]
        self.assertEqual(archive['capture_period_id'], '2026-Q4')
        self.assertEqual(archive['wording_period_id'], 'PRE-2022')
        self.assertEqual(archive['publication_period_id'], 'UNKNOWN')
        self.assertIn('after_verified_wording', archive['publication_date_evidence']['uncertainty'])
        self.assertEqual(archive['historical_source']['published_at_raw'], '2021-1-1')
        self.assertEqual(modern['wording_period_id'], 'UNKNOWN')
        decorate_observation(archive, self.config, self.text)
        self.assertEqual(archive['description_completeness'], 'unknown')
        self.assertFalse(archive['body_text_complete'])
        self.assertIsNone(archive['wording_validity']['valid_from'])
        self.assertFalse(modern['wording_validity']['historical_eligible'])

    def test_metadata_correction_has_exact_header_and_keeps_raw_assertion(self):
        _, rows = self.imported()
        row = rows[0]
        self.assertEqual(row['locations_raw'], ['Add'])
        self.assertEqual(row['structured_locations'][0]['addressLocality'], 'McLean')
        ref = row['location_assertions'][0]
        self.assertEqual(self.text[ref['start']:ref['end']], ref['quote'])
        self.assertEqual(row['employment_type'], 'unknown')
        self.assertEqual(row['remote_eligibility'], 'unknown')
        row['locations_raw'] = ['Arizona, United States']
        metadata_assertions(row, self.text)
        self.assertEqual(row['location_review'], 'header_supersedes_conflicting_legacy_assertion')
        metadata_assertions(row, 'Benefits\nNew York salary; US equal opportunity employer')
        self.assertEqual(row['geography_status'], 'unknown')

    def test_similarity_and_exact_titles_cannot_merge_vacancies(self):
        _, rows = self.imported()
        other = copy.deepcopy(rows[0])
        other.update(observation_id='other', document_id='other', vacancy_id='other',
                     requisition_id='R2', source_posting_group='requisition:R2', collection_kind='current')
        audit = identity_audit(rows + [other])
        self.assertTrue(all(f['merged'] is False for f in audit['findings']))
        self.assertIn('other', audit['quarantined_observation_ids'])
        other.update(original_url='https://example.test/job/2')
        audit = identity_audit([rows[0], other])
        self.assertEqual(audit['quarantined_observation_ids'], [])
        self.assertTrue(any(f['status'] == 'possible_repost_or_separate_vacancies' for f in audit['findings']))

    def test_selection_retains_expectations_under_benefits_and_full_offsets(self):
        text = ('About Capital One\nCapital One, a Fortune 500 company.\nResponsibilities\n'
                'Build credit models.\nBasic Qualifications\nSQL or Python required.\n'
                'Benefits\n401(k) match.\nMust travel monthly.\n'
                'Capital One is an equal opportunity employer.\nhttps://example.test/privacy\n')
        text += 'Hold direct accountability for a $6.1 billion business.\n'
        text += 'Capital One, a Fortune 500 company. You must use Python.\n'
        result = select_units(text)
        selected = '\n'.join(u['text'] for u in result['units'] if u['selected'])
        self.assertIn('Build credit models.', selected)
        self.assertIn('SQL or Python required.', selected)
        self.assertIn('Must travel monthly.', selected)
        self.assertIn('Hold direct accountability for a $6.1 billion business.', selected)
        self.assertIn('You must use Python.', selected)
        self.assertNotIn('401(k)', selected)
        self.assertNotIn('equal opportunity', selected)
        self.assertNotIn('privacy', selected)
        self.assertTrue(all(text[u['start']:u['end']] == u['text'] for u in result['units']))
        self.assertNotEqual(method_signature({'model':'fixture'}), method_signature({'model':'changed'}))

    def test_pay_attribution_cannot_borrow_job_location_or_pay_period(self):
        text = ('Location: Virginia\nThe full-time annual salaries are:\n'
                'New York City (Hybrid): $100,000 - $120,000\n'
                'Basic Qualifications\nSQL required.\nSalary USD 50 per hour\n'
                'Benefits\nBonus $1,000\n')
        rows = pay_records(text, 'doc', digest(text))
        self.assertEqual(len(rows), 3)
        self.assertEqual(rows[0]['amount_min'], 100000)
        self.assertEqual(rows[0]['amount_max'], 120000)
        self.assertEqual(rows[0]['applicable_location'], 'New York City (Hybrid)')
        self.assertEqual(rows[0]['pay_period'], 'year')
        self.assertIsNone(rows[0]['currency'])
        self.assertIsNone(rows[1]['applicable_location'])
        self.assertEqual(rows[1]['currency'], 'USD')
        self.assertEqual(rows[1]['pay_period'], 'hour')
        self.assertIsNone(rows[2]['pay_period'])
        self.assertTrue(all(text[r['evidence_start']:r['evidence_end']] == r['evidence'] for r in rows))

    def test_unresolved_historical_identity_never_enters_denominator(self):
        _, rows = self.imported()
        directory = self.root / 'runs' / 'review'
        write_jsonl(directory / 'observations.jsonl', rows)
        write_json(directory / 'taxonomy.json', {'version': 'fixture', 'skills': {},
            'groups': {'risk': {'approved': True}},
            'assignments': {r['document_id']: {'role_group_id': 'risk', 'source_sha256': r['clean_sha256']} for r in rows}})
        result = report(directory, self.config)
        self.assertEqual(result['distinct_vacancies'], 0)
        self.assertFalse(result['requirement_shares'])
        self.assertEqual(result['comparison_settings']['minimum_assessed'], 30)
        observed = [c for c in result['coverage'] if c['time_basis'] == 'historical_wording' and c['period'] == 'PRE-2022'][0]
        self.assertEqual(observed['observed_content_versions'], 1)
        self.assertEqual(observed['eligible_distinct_vacancies'], 0)
        self.assertEqual(observed['unresolved_identity_candidates'], 1)
        record = {'accepted': True, 'document_review': 'approved', 'description_completeness': 'unknown',
                  'selection_review': 'complete', 'requirements': []}
        self.assertFalse(assessed(record, 'sql', 'required', 'human_reviewed', {}, 'tool')[0])
        record.update(document_review='pending', capture_completeness='full_provider_description', method_signature='fixture')
        gate = {'status': 'passed', 'method_signature': 'fixture'}
        self.assertFalse(assessed(record, 'sql', 'required', 'evaluated_automated', gate, 'tool')[0])

    def test_preparation_blocks_network_and_inference_before_dispatch(self):
        with self.assertRaisesRegex(ValueError, 'disabled'):
            collect(self.config, 'review')
        with self.assertRaisesRegex(ValueError, 'cannot be extended'):
            extend(self.config, 'review', {})
        class ForbiddenRunner:
            def extract(self, request):
                raise AssertionError('Must not call any model')
        self.assertEqual(extract(self.config, 'review', ForbiddenRunner())['model_calls'], 0)

    def test_builtin_taxonomy_ids_cannot_be_redefined_but_allow_explicit_lineage(self):
        _, rows = self.imported()
        directory = self.root / 'runs' / 'taxonomy-test'
        write_jsonl(directory / 'observations.jsonl', rows)
        definition = {'name': 'Revised SQL boundary', 'definition': 'A different boundary',
                      'category': 'tool', 'aliases': [], 'supporting_evidence': [
                          {'document_id': rows[0]['document_id'], 'source_sha256': rows[0]['clean_sha256'],
                           'quote': 'SQL required.'}]}
        payload = {'approved': True, 'reviewer': 'synthetic-test-fixture', 'skills': {'sql': definition}}
        with self.assertRaisesRegex(ValueError, 'Built-in concept IDs'):
            approve(directory, payload)
        definition['supersedes'] = ['sql']
        payload['skills'] = {'sql-next': definition}
        result = approve(directory, payload)
        self.assertEqual(result['skills']['sql-next']['supersedes'], ['sql'])

    def test_preparation_resume_preserves_baseline_and_never_transfers_labels(self):
        from degent.config import load_config
        from degent.study import study_config
        config = study_config(load_config())
        config.update(run_dir=self.config['run_dir'], data_dir=str(self.root / 'data'))
        baseline = self.root / 'runs' / 'baseline'
        write_json(baseline / 'dashboard-config.json', config)
        write_jsonl(baseline / 'observations.jsonl', [])
        write_json(baseline / 'gold-packet.json', {'documents': [], 'role_pairs': [{'label': True}]})
        before = {p.name: p.read_bytes() for p in baseline.iterdir()}
        with patch('degent.study.prepare', side_effect=OSError('simulated preparation interruption')):
            with self.assertRaises(OSError):
                prepare_historical(config, 'combined', self.package, baseline)
        result = prepare_historical(config, 'combined', self.package, baseline)
        directory = Path(result['run_dir'])
        self.assertEqual(len(read_jsonl(directory / 'observations.jsonl')), 3)
        self.assertEqual(read_json(directory / 'gold-packet.json')['role_pairs'], [])
        decision = directory / 'scope-proposals.jsonl'
        proposals = read_jsonl(decision)
        proposals[0]['human_decision'] = {'note': 'preserve synthetic reviewer edit'}
        write_jsonl(decision, proposals)
        again = prepare_historical(config, 'combined', self.package, baseline)
        self.assertEqual(again['historical_observations'], 3)
        self.assertEqual(len(read_jsonl(directory / 'observations.jsonl')), 3)
        self.assertIsNotNone(read_jsonl(decision)[0]['human_decision'])
        self.assertEqual(before, {p.name: p.read_bytes() for p in baseline.iterdir()})


if __name__ == '__main__':
    unittest.main()
