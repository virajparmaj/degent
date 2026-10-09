from __future__ import annotations

import copy
import json
import tempfile
import threading
import time
import unittest
import urllib.error
import urllib.request
from pathlib import Path
from unittest.mock import Mock, patch

from degent.classify import classify_role
from degent.cli import extract, fetch
from degent.compare import build_report
from degent.config import DEFAULT_CONFIG, load_config
from degent.dashboard import ROOT, Dashboard, make_server
from degent.review import effective_extractions, requirement_id
from degent.store import append_jsonl, digest, operation_lock, read_jsonl, write_json, write_jsonl


class DashboardTests(unittest.TestCase):
    def test_openrouter_state_exposes_model_without_credentials(self):
        self.config.update(study_version=1, inference={"identity":{"provider":"openrouter","model":"openai/gpt-6-luna"},
            "enabled":False,"paid":True,"api_key":"fake-sensitive-key"})
        write_json(self.directory/'dashboard-config.json',self.config)
        state=self.app.state(self.run)
        self.assertEqual(state['method'],'openrouter')
        self.assertEqual(state['config']['inference']['identity']['model'],'openai/gpt-6-luna')
        self.assertNotIn('fake-sensitive-key',json.dumps(state))

    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.app = Dashboard(self.root)
        self.run = 'fixture-run'
        self.directory = self.app.run_dir(self.run)
        self.directory.mkdir()
        self.config = copy.deepcopy(self.app.base)
        self.config['data_dir'] = str(self.root / 'data')
        self.config['run_dir'] = str(self.root / 'runs')
        write_json(self.directory / 'dashboard-config.json', self.config)
        write_json(self.directory / 'manifest.json', {'run_id':self.run,'queries':[], 'body_attempts':[], 'counts':{}})
        self.source = self.root / 'data' / 'source.txt'
        self.source.parent.mkdir()
        self.text = 'Location: United States\nStripe Credit Risk Analyst II\nResponsibilities\n' + (
            'You will evaluate customer risk and collaborate with business partners.\n' * 8
        ) + 'Qualifications\nProficiency with SQL required.\nPython preferred.'
        self.source.write_text(self.text)
        self.obs = {
            'document_id':'doc1', 'observation_id':'obs1', 'vacancy_id':'vac1',
            'employer':'Stripe', 'employer_id':'stripe', 'brand':'Stripe',
            'period_id':'2025-Q1', 'capture_at':'20250101000000', 'capture_date':'2025-01-01',
            'eligible':True, 'body_complete':True, 'title':'Credit Risk Analyst II',
            'body_status':'complete_candidate', 'role_track':'credit_risk_analytics',
            'role_subtype':'general', 'level':'mid', 'clean_file':str(self.source),
            'raw_file':str(self.root / 'no-raw.bin'), 'clean_sha256':digest(self.text),
            'original_url':'https://stripe.com/jobs/123',
        }
        self.extraction = {'document_id':'doc1','accepted':True,'source_sha256':digest(self.text),
                           'extracted_at':'2026-10-07T12:00:00Z','extraction_method':'rules',
                           'requirements':[
                               {'category':'skill','concept':'sql','phrase':'Proficiency with SQL required.',
                                'evidence':'SQL','status':'required','explicit':True,'negated':False},
                               {'category':'skill','concept':'python','phrase':'Python preferred.',
                                'evidence':'Python','status':'preferred','explicit':True,'negated':False},
                           ]}
        write_jsonl(self.directory / 'observations.jsonl',[self.obs])
        write_jsonl(self.directory / 'requirements.jsonl',[self.extraction])

    def approve(self, index=0, status='unclear', revision=0):
        return self.app.review(self.run, {'requirement_id':requirement_id(self.extraction,index),
                                         'status':status,'note':'Source remains ambiguous.','revision':revision})

    def test_real_counts_ignore_stale_rejected_extractions(self):
        other = {**self.obs,'document_id':'rejected','observation_id':'obs2','eligible':False}
        append_jsonl(self.directory / 'observations.jsonl',[other])
        append_jsonl(self.directory / 'requirements.jsonl',[{**self.extraction,'document_id':'rejected'}])
        state=self.app.state(self.run)
        self.assertEqual(state['counts']['retrieved'],2)
        self.assertEqual(state['counts']['eligible'],1)
        self.assertEqual(state['counts']['documents'],1)
        self.assertEqual(state['counts']['requirements'],2)
        self.assertEqual(state['counts']['pending'],2)

    def test_review_persists_per_requirement_and_changes_report(self):
        decision=self.approve()
        self.assertEqual(decision['revision'],1)
        second=Dashboard(self.root)
        records=second.requirements(self.directory)
        self.assertEqual(records[0]['status'],'unclear')
        self.assertEqual(records[0]['human_review'],'approved')
        self.assertEqual(records[1]['human_review'],'pending')
        result=build_report(self.directory,self.config)
        self.assertFalse(any(row['concept']=='sql' for row in result['requirement_shares']))
        self.assertEqual(len(read_jsonl(self.directory/'reviews.jsonl')),1)
        self.assertEqual(read_jsonl(self.directory/'requirements.jsonl')[0],self.extraction)

    def test_review_rejects_conflicting_revision(self):
        self.approve()
        with self.assertRaisesRegex(ValueError,'another window'):
            self.approve(status='preferred')
        self.assertEqual(len(read_jsonl(self.directory/'reviews.jsonl')),1)

    def test_reextraction_does_not_inherit_previous_approval(self):
        self.approve()
        new={**self.extraction,'extracted_at':'2026-10-08T12:00:00Z'}
        append_jsonl(self.directory/'requirements.jsonl',[new])
        records=self.app.requirements(self.directory)
        self.assertEqual(records[0]['human_review'],'pending')
        self.assertNotEqual(records[0]['id'],requirement_id(self.extraction,0))
        self.assertEqual(len(effective_extractions(self.directory)),1)

    def test_changed_or_outside_source_cannot_be_approved(self):
        self.source.write_text(self.text+'changed')
        with self.assertRaisesRegex(ValueError,'Source content changed'):
            self.approve()
        outside=self.root/'outside.txt'
        outside.write_text('secret')
        write_jsonl(self.directory/'observations.jsonl',[{**self.obs,'clean_file':str(outside)}])
        with self.assertRaisesRegex(ValueError,'outside'):
            self.app.source(self.run,'obs1',0)

    def test_source_pagination_and_anchor_return_different_text(self):
        first=self.app.source(self.run,'obs1',0)
        second=self.app.source(self.run,'obs1',1)
        anchored=self.app.source(self.run,'obs1',0,'SQL')
        self.assertNotEqual(first['text'],second['text'])
        self.assertIn('SQL',anchored['text'])
        self.assertTrue(anchored['verified'])

    def test_records_paginate_filter_and_clamp_page(self):
        data=[{**self.obs,'document_id':str(i),'observation_id':str(i),
               'employer_id':'stripe' if i<5 else 'affirm'} for i in range(8)]
        write_jsonl(self.directory/'observations.jsonl',data)
        result=self.app.records(self.run,'captures',{'employer_id':['stripe'],'page':['99'],'size':['4']})
        self.assertEqual(result['total'],5)
        self.assertEqual(result['page'],1)
        self.assertEqual(len(result['items']),1)
        self.assertEqual(self.app.records(self.run,'captures',{'search':['no-such-role']})['items'],[])

    def test_new_scope_is_saved_and_existing_evidence_preserved(self):
        self.app.create_run({'run_id':'new-scope','employers':['stripe'],'periods':['2025-Q1'],
                             'tracks':['credit_risk_analytics'],'body_attempt_cap':50,'method':'codex'})
        config=self.app.config('new-scope')
        self.assertEqual([e['id'] for e in config['employers']],['stripe'])
        self.assertEqual(config['body_attempt_cap'],50)
        self.assertTrue(config['dashboard_codex'])
        self.assertFalse(classify_role('Fraud Strategy Analyst',self.text,config)['eligible'])
        self.assertTrue(self.source.exists())
        with self.assertRaisesRegex(ValueError,'already exists'):
            self.app.create_run({'run_id':'new-scope'})

    def test_invalid_scope_budget_and_paths_are_rejected(self):
        for payload in ({'run_id':'../escape'}, {'run_id':'a','employers':[]},
                        {'run_id':'b','periods':['made-up']}, {'run_id':'c','body_attempt_cap':151},
                        {'run_id':'d','discovery_request_cap':True}):
            with self.assertRaises(ValueError): self.app.create_run(payload)

    def test_exhausted_and_out_of_order_operations_do_not_launch(self):
        write_json(self.directory/'manifest.json',{'discovery_network_requests':300,
                   'body_attempts':[{}]*150,'counts':{'archive_records':2}})
        with patch('degent.dashboard.subprocess.Popen') as process:
            for operation in ('discover','fetch'):
                with self.assertRaises(ValueError): self.app.start(self.run,operation)
            process.assert_not_called()
        write_jsonl(self.directory/'observations.jsonl',[])
        with self.assertRaisesRegex(ValueError,'No complete eligible'):
            self.app.start(self.run,'extract')

    def test_worker_uses_argument_array_and_enforces_one_operation(self):
        process=Mock()
        process.poll.return_value=None
        with patch('degent.dashboard.subprocess.Popen',return_value=process) as spawn, patch('degent.dashboard.threading.Thread'):
            job=self.app.start(self.run,'report')
            self.assertEqual(job['status'],'running')
            command=spawn.call_args.args[0]
            self.assertIsInstance(command,list)
            self.assertIn('degent',command)
            self.assertEqual(command[-1],'report')
            self.assertTrue(spawn.call_args.kwargs['start_new_session'])
            with self.assertRaisesRegex(ValueError,'already running'):
                self.app.start(self.run,'doctor')

    def test_report_worker_finishes_without_archive_requests(self):
        config_file=self.root/'worker-config.json'
        write_json(config_file,self.config)
        app=Dashboard(ROOT,config_file)
        app.start(self.run,'report')
        deadline=time.monotonic()+10
        while app.job.get('status')=='running' and time.monotonic()<deadline:
            time.sleep(.02)
        self.assertEqual(app.job['status'],'completed',app.job)
        self.assertTrue((self.directory/'report.json').exists())
        self.assertTrue((self.directory/'requirements.csv').exists())
        self.assertEqual(app.state(self.run)['counts']['documents'],1)

    def test_fetch_cap_does_not_allow_one_extra_attempt(self):
        config={**self.config,'body_attempt_cap':1}
        write_json(self.directory/'manifest.json',{'queries':[], 'counts':{},
                   'body_attempts':[{'record_key':'prior','employer_id':'stripe'}],
                   'body_budget_reallocation':{'reassigned_attempts':{}}})
        with patch('degent.cli.select_fetch_records',return_value=[{'source':'wayback','capture_at':'20250101000000','original_url':'https://stripe.com/jobs/123','employer_id':'stripe'}]), patch('degent.cli.ArchiveClient') as client:
            fetch(config,self.run)
            client.return_value.fetch_wayback.assert_not_called()

    def test_codex_failure_cannot_accept_silent_rule_fallback(self):
        write_jsonl(self.directory/'requirements.jsonl',[])
        with patch('degent.cli.codex_extract',return_value=(None,'quota exceeded',1)):
            extract(self.config,self.run,True)
        result=read_jsonl(self.directory/'requirements.jsonl')
        self.assertEqual(len(result),1)
        self.assertFalse(result[0]['accepted'])
        self.assertEqual(self.app.state(self.run)['counts']['documents'],0)

    def test_live_partial_jsonl_tail_does_not_break_snapshot(self):
        file=self.directory/'live.jsonl'
        file.write_text('{"done":1}\n{"partial":')
        self.assertEqual(read_jsonl(file),[{'done':1}])
        file.write_text('{"broken":\n{"done":1}\n')
        with self.assertRaises(json.JSONDecodeError): read_jsonl(file)

    def test_resume_repairs_interrupted_append_and_preserves_complete_tail(self):
        file=self.directory/'live.jsonl'
        file.write_text('{"done":1}\n{"partial":')
        append_jsonl(file,[{'next':2}])
        self.assertEqual(read_jsonl(file),[{'done':1},{'next':2}])
        file.write_text('{"done":1}')
        append_jsonl(file,[{'next':2}])
        self.assertEqual(read_jsonl(file),[{'done':1},{'next':2}])

    def test_run_lock_blocks_another_writer(self):
        with operation_lock(self.directory):
            with self.assertRaisesRegex(RuntimeError,'already writing'):
                with operation_lock(self.directory):
                    pass


class LocalHTTPTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.temp=tempfile.TemporaryDirectory()
        cls.server=make_server(root=Path(cls.temp.name))
        cls.thread=threading.Thread(target=cls.server.serve_forever,daemon=True)
        cls.thread.start()
        cls.base=f'http://127.0.0.1:{cls.server.server_port}'

    @classmethod
    def tearDownClass(cls):
        cls.server.shutdown()
        cls.server.server_close()
        cls.thread.join()
        cls.temp.cleanup()

    def request(self,path,body=None,headers=None):
        payload=json.dumps(body).encode() if body is not None else None
        req=urllib.request.Request(self.base+path,data=payload,headers=headers or {})
        try:
            with urllib.request.urlopen(req) as response:
                return response.status,response.read(),response.headers
        except urllib.error.HTTPError as exc:
            with exc:
                return exc.code,exc.read(),exc.headers

    def test_session_host_origin_token_and_traversal(self):
        status,data,headers=self.request('/api/state')
        self.assertEqual(status,200)
        token=json.loads(data)['token']
        self.assertIn("frame-ancestors 'none'",headers['Content-Security-Policy'])
        self.assertEqual(self.request('/api/state',headers={'Host':'evil.example'})[0],403)
        self.assertEqual(self.request('/api/runs',{'run_id':'http-run'})[0],403)
        valid={'Origin':self.base,'X-Degent-Token':token,'Content-Type':'application/json'}
        self.assertEqual(self.request('/api/runs',{'run_id':'http-run'},valid)[0],200)
        self.assertEqual(self.request('/api/runs',{'run_id':'http-run'},{**valid,'Origin':'https://evil.example'})[0],403)
        self.assertEqual(self.request('/api/export?run=http-run&file=../../config/research.toml')[0],400)
        self.assertEqual(self.request('/../../config/research.toml')[0],404)
        status,body,headers=self.request('/api/export?run=http-run&file=manifest.json')
        self.assertEqual(status,200)
        self.assertEqual(json.loads(body)['run_id'],'http-run')
        self.assertIn('attachment',headers['Content-Disposition'])

    def test_static_assets_are_local_and_served(self):
        for asset,mime in (('/', 'text/html'),('/app.js','text/javascript'),('/styles.css','text/css')):
            status,body,headers=self.request(asset)
            self.assertEqual(status,200)
            self.assertTrue(body)
            if asset!='/app.js': self.assertIn(mime,headers['Content-Type'])
        self.assertNotIn(b'fonts.googleapis',self.request('/styles.css')[1])


if __name__=='__main__': unittest.main()
