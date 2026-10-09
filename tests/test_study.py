"""Meaningful study contracts: no live network or inference."""
import copy
import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from degent.collection import collect, extend, retry_failed
from degent.collection_http import CollectionClient
from degent.config import load_config
from degent.evidence import date_assertion, decorate_observation, period_definitions, period_for, select_units
from degent.model_runner import ModelResponse, expand_wire
from degent.review import document_reviewed, effective_extractions, extraction_identity
from degent.store import append_jsonl, digest, read_jsonl, write_json, write_jsonl
from degent.study import create, extract, prepare, review_document, study_config
from degent.study_evaluation import evaluate, gold_packet
from degent.study_metrics import assessed, report
from degent.taxonomy import approve, propose
from test_capital_one import detail, listing, url


class StudyTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        base = load_config()
        base.update(data_dir=str(self.root/'data'), run_dir=str(self.root/'runs'), host_delay_seconds=0, retry_count=0)
        self.config = study_config(base)
        self.directory = self.root/'runs'/'study'
        self.request_order = []
        create(base, 'study')

    def network(self, responder):
        def request(client, address, **kwargs):
            self.request_order.append(address)
            client.remaining()
            payload = responder(address)
            event = {'url':address, 'reserved_bytes':min(8_000_000,client.byte_limit-client.bytes_used)}
            client.request_count += 1
            client.reserve(event)
            client.bytes_used += len(payload)
            client.finish({**event,'bytes':len(payload)})
            return 200, {}, payload
        return patch.object(CollectionClient,'request',request)

    def inventory(self, count=1, repeated=False):
        def responder(address):
            if '/search-jobs?' in address:
                page = int(address.split('p=')[-1])
                number = 1 if repeated else page
                payload = listing([(url(number),'Software Engineer II','McLean, Virginia, United States')],page=page,pages=count,keyword='')
                return payload.replace(b'data-total-results="1"',f'data-total-results="{count}"'.encode())
            number = int(address.split('/')[-1])
            return detail(number,title='Software Engineer II',description='Basic Qualifications\nSQL required.\nPreferred Qualifications\nPython preferred.')
        with self.network(responder):
            return collect(self.config,'study')

    def documents(self, count):
        self.directory.mkdir(exist_ok=True,parents=True)
        rows, records = [], []
        for i in range(count):
            text = f'Capital One\nCredit Risk Analyst II {i}\nLocation: Virginia, United States\nResponsibilities\nAnalyze credit risk.\nBasic Qualifications\nSQL required.\nPreferred Qualifications\nPython preferred.'
            path=self.root/f'doc{i}.txt';path.write_text(text)
            row={'document_id':f'd{i}','vacancy_id':f'v{i}','observation_id':f'o{i}','employer_id':'capital_one','employer':'Capital One',
                 'brand':'Capital One','title':f'Credit Risk Analyst II {i}','level':'mid','source':'capital_one','verified_employer':'Capital One',
                 'clean_file':str(path),'raw_file':str(path),'clean_sha256':digest(text),'capture_date':'2026-10-08',
                 'capture_at':'20261008200000','stated_publication_at':'2023-06-01','publication_date_source':'JobPosting.datePosted',
                 'current_capture_at':'2026-10-08T20:00:00+00:00','original_url':url(i+1),'employment_type':'full-time',
                 'geography_status':'us_location','content_format':'posting-json','parser_version':'fixture','eligible':True,'body_complete':True}
            selection=select_units(text)
            u=next(u for u in selection['units'] if u['text']=='SQL required.')
            p=next(u for u in selection['units'] if u['text']=='Python preferred.')
            wire={'r':[{'u':u['unit_id'],'span':[0,len(u['text'])],'c':'SQL','s':'required','k':'qualification'},
                       {'u':p['unit_id'],'span':[0,len(p['text'])],'c':'Python','s':'preferred','k':'qualification'}]}
            record, errors=expand_wire(wire,row['document_id'],selection,text)
            self.assertFalse(errors)
            record.update(source_sha256=digest(text),accepted=True,method_signature='fixture',cache_key=f'key{i}',
                          extracted_at='2026-10-08T20:00:00Z',requires_omission_audit=True)
            rows.append(row);records.append(record)
        write_jsonl(self.directory/'observations.jsonl',rows)
        prepare(self.config,'study')
        write_jsonl(self.directory/'requirements.jsonl',records)
        return rows, records

    def test_full_inventory_exceeds_twenty_pages_and_chunks(self):
        self.config['collection_chunk_size']=2
        result=self.inventory(25)
        self.assertEqual(result['status'],'complete')
        self.assertEqual(result['requests_used'],50)
        self.assertEqual(len(read_jsonl(self.directory/'observations.jsonl')),25)
        self.assertTrue(next(iter(result['sources'].values()))['inventory_complete'])
        self.assertTrue(all(r['eligible'] for r in read_jsonl(self.directory/'observations.jsonl')))
        self.assertTrue(all('/search-jobs?' in u for u in self.request_order[:25]))
        self.assertTrue(all('/search-jobs?' not in u for u in self.request_order[25:]))

    def test_unfamiliar_title_is_retrieved_for_review(self):
        def responder(address):
            if '/search-jobs?' in address:
                return listing([(url(1),'Business Strategy Partner','McLean, Virginia, United States')],page=1,pages=1,keyword='')
            return detail(1,title='Business Strategy Partner',description='Analyze credit portfolios and recommend strategy.')
        with self.network(responder): result=collect(self.config,'study')
        self.assertEqual(result['requests_used'],2)
        row=read_jsonl(self.directory/'observations.jsonl')[0]
        self.assertTrue(row['eligible'])
        self.assertEqual(row['scope_decision']['decision'],'ambiguous')

    def test_description_failure_keeps_other_candidates_and_retry_usage(self):
        failed=True
        def responder(address):
            if '/search-jobs?' in address:
                return listing([(url(1),'Software Engineer','Virginia, United States'),(url(2),'Data Scientist','Virginia, United States')],page=1,pages=1,keyword='')
            if address==url(1) and failed: raise RuntimeError('fixture unavailable')
            return detail(int(address.split('/')[-1]),title='Software Engineer',description='SQL required.')
        with self.network(responder):result=collect(self.config,'study')
        self.assertEqual(result['status'],'needs_attention')
        self.assertEqual(len(read_jsonl(self.directory/'observations.jsonl')),1)
        usage=dict(result['session']['usage'])
        retry_failed(self.config,'study')
        failed=False
        with self.network(responder): retried=collect(self.config,'study')
        self.assertEqual(retried['status'],'complete')
        self.assertEqual(len(read_jsonl(self.directory/'observations.jsonl')),2)
        self.assertGreater(retried['session']['usage']['bytes'],usage['bytes'])
        decisions=[r for r in read_jsonl(self.directory/'current-selection.jsonl') if r['site_job_id']=='1']
        self.assertEqual([r['status'] for r in decisions],['unavailable_description','eligible'])

    def test_interruption_resumes_and_extension_keeps_consumed_usage(self):
        def responder(address):
            if '/search-jobs?' in address:
                return listing([(url(1),'Software Engineer','Virginia, United States')],page=1,pages=1,keyword='')
            raise KeyboardInterrupt()
        with self.network(responder): paused=collect(self.config,'study')
        self.assertEqual(paused['pause_reason'],'cancelled')
        usage=dict(paused['session']['usage'])
        extend(self.config,'study',{'bytes':1000,'seconds':5})
        result=self.inventory()
        self.assertEqual(result['status'],'complete')
        self.assertEqual(result['session']['budgets']['bytes'],200001000)
        self.assertGreaterEqual(result['session']['usage']['bytes'],usage['bytes'])

    def test_source_tampering_cannot_enter_selection_or_report(self):
        rows,_=self.documents(1)
        Path(rows[0]['clean_file']).write_text('SQL required. changed')
        prepare(self.config,'study')
        self.assertFalse(read_jsonl(self.directory/'source-units.jsonl'))
        self.assertEqual(report(self.directory,self.config)['distinct_vacancies'],0)

    def test_repeated_page_is_not_a_census(self):
        result=self.inventory(3,repeated=True)
        self.assertEqual(result['status'],'needs_attention')
        self.assertFalse(next(iter(result['sources'].values()))['inventory_complete'])

    def test_confirmed_requisition_aliases_are_separate_observations_one_vacancy(self):
        def responder(address):
            if '/search-jobs?' in address:
                return listing([(url(1),'Software Engineer','Virginia, United States'),(url(2),'Software Engineer','Virginia, United States')],page=1,pages=1,keyword='')
            return detail(int(address.split('/')[-1]),title='Software Engineer',identifier={'value':'R000001'},description='SQL required.')
        with self.network(responder):collect(self.config,'study')
        rows=read_jsonl(self.directory/'observations.jsonl')
        self.assertEqual(len(rows),2)
        self.assertEqual(len({r['vacancy_id'] for r in rows}),1)
        self.assertEqual(len({r['content_version_id'] for r in rows}),1)
        self.assertEqual(len({r['observation_id'] for r in rows}),2)

    def test_repeated_capture_and_wording_change_preserve_version_identity(self):
        self.inventory()
        with self.network(lambda address: listing([(url(1),'Software Engineer II','Virginia, United States')],page=1,pages=1,keyword='') if '/search-jobs?' in address else detail(1,title='Software Engineer II',description='Basic Qualifications\nSQL required.\nPreferred Qualifications\nPython preferred.')):
            collect(self.config,'study',refresh=True)
        rows=read_jsonl(self.directory/'observations.jsonl')
        self.assertEqual(len(rows),2)
        self.assertEqual(rows[0]['document_id'],rows[1]['document_id'])
        with self.network(lambda address: listing([(url(1),'Software Engineer II','Virginia, United States')],page=1,pages=1,keyword='') if '/search-jobs?' in address else detail(1,title='Software Engineer II',description='SQL and Python required.')):
            collect(self.config,'study',refresh=True)
        rows=read_jsonl(self.directory/'observations.jsonl')
        self.assertEqual(len({r['vacancy_id'] for r in rows}),1)
        self.assertEqual(len({r['document_id'] for r in rows}),2)

    def test_short_engineering_body_is_readable_and_distinct_from_review(self):
        self.inventory()
        row=read_jsonl(self.directory/'observations.jsonl')[0]
        self.assertTrue(row['eligible'])
        self.assertEqual(row['description_completeness'],'unreviewed')
        self.assertEqual(row['publication_period_id'],'2023-H1')
        self.assertEqual(row['capture_period_id'],'2026-Q4')

    def test_uncapped_request_extension_is_explicitly_invalid(self):
        self.inventory()
        with self.assertRaises(ValueError): extend(self.config,'study',{'requests':1})

    def test_provider_gate_makes_no_inference_call(self):
        self.documents(1)
        result=extract(self.config,'study')
        self.assertEqual(result['status'],'waiting_for_allowance')
        self.assertFalse((self.directory/'model-outputs.jsonl').exists())

    def test_one_call_usage_cache_and_fresh_review(self):
        rows,_=self.documents(1)
        (self.directory/'requirements.jsonl').unlink()
        identity={'provider':'fixture','model':'fixture','revision':'1'}
        class Runner:
            calls=0
            def count_tokens(self,request):return 120
            def extract(inner,request):
                inner.calls+=1
                u=next(u for u in request['units'] if u['text']=='SQL required.')
                return ModelResponse({'r':[{'u':u['u'],'span':[0,13],'c':'SQL','s':'required','k':'qualification'}]}, {'input_tokens':120,'output_tokens':30})
        runner=Runner();runner.identity=identity
        self.config['inference']={'enabled':True,'identity':identity,'call_limit':4,'input_token_limit':2000,'output_token_limit':10000}
        result=extract(self.config,'study',runner)
        self.assertEqual(runner.calls,1)
        self.assertEqual(result['usage']['output_tokens'],30)
        extract(self.config,'study',runner)
        self.assertEqual(runner.calls,1)
        record=effective_extractions(self.directory)[0]
        self.assertFalse(document_reviewed(record))
        self.assertTrue(record['requires_omission_audit'])
        review_document(self.config,'study',{'document_id':'d0','extraction_id':extraction_identity(record),
                        'omission_audited':True,'description_completeness':'complete','selection_review':'complete'})
        approve(self.directory,{'approved':True,'reviewer':'fixture-human','groups':{},'assignments':{},
            'skills':{'sql':{'name':'SQL','definition':'Structured query language','category':'technical',
                            'supporting_evidence':[{'document_id':'d0','source_sha256':rows[0]['clean_sha256'],'quote':'SQL required.'}]}}})
        prepare(self.config,'study')
        refreshed=effective_extractions(self.directory)[0]
        self.assertTrue(refreshed['renormalized_locally'])
        self.assertFalse(document_reviewed(refreshed))
        extract(self.config,'study',runner)
        self.assertEqual(runner.calls,1)

    def test_explicit_lower_bound_does_not_invent_upper_experience(self):
        source='Basic Qualifications\nAt least two years of SQL experience.'
        selection=select_units(source);u=selection['units'][-1]
        record,errors=expand_wire({'r':[{'u':u['unit_id'],'span':[0,len(u['text'])],'c':'SQL','s':'required','k':'qualification','y':[2,None]}]},'d',selection,source)
        self.assertFalse(errors)
        self.assertEqual(record['requirements'][0]['experience_range'],[2,None])

    def test_invalid_wire_offsets_and_invented_qualifier(self):
        source='Basic Qualifications\nSQL required.';selection=select_units(source)
        u=selection['units'][-1]
        for extra in [{'span':[0,100]},{'q':'expert in SQL'}]:
            record,errors=expand_wire({'r':[{'u':u['unit_id'],'span':[0,13],'c':'SQL','s':'required','k':'qualification',**extra}]},'d',selection,source)
            self.assertTrue(errors)
            self.assertFalse(record['requirements'])

    def test_section_variants_offsets_and_unheaded_requirements(self):
        source='Intro: AI tools required.\nGeneral Responsibilities:\nCollaborate with partners.\nBasic Qualifications:\nSQL required.\nPreferred Qualifications:\nPython preferred.\nThe minimum and maximum full-time annual salaries for this role are listed below.'
        selection=select_units(source)
        self.assertEqual(next(u for u in selection['units'] if u['text']=='SQL required.')['statement_scope'],'qualification')
        self.assertTrue(selection['units'][0]['selected'])
        self.assertFalse(selection['units'][-1]['selected'])
        for u in selection['units']:self.assertEqual(source[u['start']:u['end']],u['text'])

    def test_loan_applications_are_duties_not_recruiting(self):
        source='Responsibilities\nReview credit applications.\nWe use AI to screen job applications and candidates.\nBuild machine learning systems.\nUse AI tools for productivity.'
        units=select_units(source)['units']
        self.assertEqual(units[1]['statement_scope'],'responsibility')
        self.assertEqual(units[2]['statement_scope'],'hiring_process')
        self.assertEqual(units[3]['statement_scope'],'responsibility')
        self.assertEqual(units[4]['statement_scope'],'responsibility')

    def test_ai_engineering_does_not_silently_become_practical_use(self):
        source='Basic Qualifications\nBuild AI agents.\nUse AI agents for productivity.'
        selection=select_units(source)
        rows=[{'u':u['unit_id'],'span':[0,len(u['text'])],'c':'AI agents','s':'required','k':'qualification',
               't':'ai_engineering' if u['text'].startswith('Build') else 'ai_use'} for u in selection['units'][1:]]
        result,errors=expand_wire({'r':rows},'d',selection,source)
        self.assertFalse(errors)
        self.assertIsNone(result['requirements'][0]['concept'])
        self.assertEqual(result['requirements'][0]['review_reason'],'ai_context_needs_review')
        self.assertEqual(result['requirements'][1]['category'],'ai_use')

    def test_dates_unknown_and_detailed_periods(self):
        self.assertEqual(period_for('2021-12-31'),('PRE-2022','PRE-2022'))
        self.assertEqual(period_for('2022-07-01')[0],'2022-H2')
        self.assertEqual(period_for('2025-04-01')[0],'2025-Q2')
        self.assertEqual(date_assertion('2025-01-01T00:00:00','source')['date'],None)
        self.assertEqual(date_assertion('not a date','source')['uncertainty'],'invalid')
        self.assertEqual(next(p for p in period_definitions('2026-10-08') if p['id']=='2026-Q4')['kind'],'partial')

    def test_unrecognized_employment_assertion_is_not_a_stratum(self):
        row={'document_id':'d','employer_id':'capital_one','title':'Software Engineer 4','capture_date':'2026-10-08',
             'geography_status':'us_location','employment_type':'FBENG'}
        decorate_observation(row,self.config,'SQL required.')
        self.assertEqual(row['employment_type'],'unknown')
        self.assertEqual(row['employment_type_evidence']['raw'],'FBENG')
        self.assertEqual(row['level'],'unknown')
        row['employment_type_raw']='FULL_TIME'
        decorate_observation(row,self.config,'SQL required.')
        self.assertEqual(row['employment_type'],'full-time')

    def test_thirty_boundary_counts_vacancies_once_and_stale_evaluation(self):
        rows,records=self.documents(30)
        # A repeated observation and statement do not increase vacancy prevalence.
        append_jsonl(self.directory/'observations.jsonl',[{**rows[0],'observation_id':'another-observation'}])
        records[0]['requirements'].append(copy.deepcopy(records[0]['requirements'][0]))
        write_jsonl(self.directory/'requirements.jsonl',records)
        approve(self.directory,{'approved':True,'reviewer':'fixture-human',
            'groups':{'credit':{'name':'Credit','definition':'Credit duties','function':'analytics','domains':['credit']}},
            'assignments':{r['document_id']:{'source_sha256':r['clean_sha256'],'role_group_id':'credit','evidence':'Analyze credit risk.'} for r in rows}})
        for record in records:
            review_document(self.config,'study',{'document_id':record['document_id'],'extraction_id':extraction_identity(record),
                'omission_audited':True,'description_completeness':'complete','selection_review':'complete'})
        result=report(self.directory,self.config)
        cell=next(r for r in result['requirement_shares'] if r['time_basis']=='capture_period' and r['concept']=='sql' and r['requirement_status']=='required' and r['statement_scope']=='qualification' and r['assessment_basis']=='human_reviewed')
        self.assertEqual((cell['count'],cell['assessed_vacancies'],cell['share_percent']),(30,30,100.0))
        self.assertEqual(result['distinct_vacancies'],30)
        mention=next(r for r in result['mention_summary'] if r['time_basis']=='capture_period' and r['concept']=='sql')
        self.assertEqual((mention['evidence_statement_count'],mention['distinct_mentioning_vacancies']),(31,30))
        self.assertEqual(result['status'],'evidence_limited')

    def test_gold_scores_raw_output_even_after_human_omission_additions(self):
        _,records=self.documents(60)
        packet=gold_packet(self.directory)
        for document in packet['documents']:
            record=next(r for r in records if r['document_id']==document['document_id'])
            document.update(labels=record['requirements'],omission_audited=True)
        doc_id=next(d['document_id'] for d in packet['documents'] if d['split']=='held_out')
        record=next(r for r in records if r['document_id']==doc_id)
        record['requirements']=[]
        write_jsonl(self.directory/'requirements.jsonl',records)
        labels=self.root/'gold.json';write_json(labels,packet)
        before=evaluate(self.directory,labels)
        selection=next(s for s in read_jsonl(self.directory/'source-units.jsonl') if s['document_id']==doc_id)
        units=[u for u in selection['units'] if u['text'] in {'SQL required.','Python preferred.'}]
        additions=[{'u':u['unit_id'],'span':[0,len(u['text'])],'c':'SQL' if 'SQL' in u['text'] else 'Python',
                    's':'required' if 'SQL' in u['text'] else 'preferred','k':'qualification'} for u in units]
        review_document(self.config,'study',{'document_id':record['document_id'],'extraction_id':extraction_identity(record),
            'omission_audited':True,'description_completeness':'complete','selection_review':'complete','additions':additions})
        after=evaluate(self.directory,labels)
        self.assertEqual(before['statement'],after['statement'])
        self.assertLess(before['statement']['recall'],1)
        self.assertEqual(len(next(r for r in effective_extractions(self.directory) if r['document_id']==doc_id)['requirements']),2)

    def test_exact_quote_does_not_certify_qualifier_attribution(self):
        _,records=self.documents(60)
        packet=gold_packet(self.directory)
        for document in packet['documents']:
            record=next(r for r in records if r['document_id']==document['document_id'])
            document.update(labels=copy.deepcopy(record['requirements']),omission_audited=True)
        doc_id=next(d['document_id'] for d in packet['documents'] if d['split']=='held_out')
        next(r for r in records if r['document_id']==doc_id)['requirements'][0]['qualifier']='SQL'
        write_jsonl(self.directory/'requirements.jsonl',records)
        labels=self.root/'gold.json';write_json(labels,packet)
        result=evaluate(self.directory,labels)
        self.assertEqual(result['evidence_support'],1)
        self.assertEqual(result['statement']['precision'],1)
        self.assertEqual(result['explicit_attributes']['qualifier']['accuracy'],0)
        self.assertEqual(result['status'],'exploratory')
        self.assertTrue((self.directory/'gold-reviews'/f"{result['gold_sha256']}.json").exists())

    def test_role_assignment_cannot_use_only_measured_qualification(self):
        rows,_=self.documents(1)
        payload={'approved':True,'reviewer':'fixture-human',
            'groups':{'credit':{'name':'Credit','definition':'Credit duties','function':'analytics','domains':['credit']}},
            'assignments':{'d0':{'source_sha256':rows[0]['clean_sha256'],'role_group_id':'credit','evidence':'SQL required.'}}}
        with self.assertRaisesRegex(ValueError,'duty/domain/title'):
            approve(self.directory,payload)
        approve(self.directory,{'approved':True,'reviewer':'fixture-human','groups':{},
            'assignments':{'d0':{'source_sha256':rows[0]['clean_sha256'],'scope_decision':'excluded',
                'evidence':'Analyze credit risk.','exclusion_reason':'Synthetic reviewed exclusion'}},'skills':{}})
        self.assertEqual(report(self.directory,self.config)['distinct_vacancies'],0)

    def test_statement_approvals_do_not_establish_omission_review(self):
        _,records=self.documents(1)
        record=records[0]
        for r in record['requirements']:r['human_review']='approved'
        self.assertFalse(document_reviewed(record))
        with self.assertRaises(ValueError):review_document(self.config,'study',{'document_id':'d0','extraction_id':extraction_identity(record)})

    def test_partial_document_cannot_establish_not_mentioned(self):
        _,records=self.documents(1)
        record=records[0]
        review_document(self.config,'study',{'document_id':'d0','extraction_id':extraction_identity(record),'omission_audited':True,
                        'description_completeness':'partial','selection_review':'complete'})
        reviewed=effective_extractions(self.directory)[0]
        self.assertTrue(document_reviewed(reviewed))
        gate={'status':'exploratory','method_signature':None}
        self.assertFalse(assessed(reviewed,'r','required','human_reviewed',gate,'technical')[0])
        self.assertTrue(assessed(reviewed,'sql','required','human_reviewed',gate,'tool')[1])
        reviewed['capture_completeness']='full_provider_description'
        reviewed['method_signature']='fixture'
        self.assertFalse(assessed(reviewed,'r','required','evaluated_automated',{'status':'passed','method_signature':'fixture'},'technical')[0])

    def test_alternative_is_not_two_unconditional_requirements(self):
        record={'accepted':True,'document_review':'approved','description_completeness':'complete','selection_review':'complete',
                'requirements':[{'concept':'sql','category':'tool','status':'required','statement_scope':'qualification','alternatives':['SQL','Python'],'alternative_logic':'or'}]}
        known,present,reason=assessed(record,'sql','required','human_reviewed',{},'tool')
        self.assertFalse(known);self.assertFalse(present)
        self.assertEqual(reason,'ambiguous_or_unmapped')
        self.assertFalse(assessed(record,'python','required','human_reviewed',{},'technical')[0])

    def test_taxonomy_needs_evidence_and_frozen_definitions(self):
        rows,_=self.documents(2)
        payload={'approved':True,'reviewer':'fixture-human','groups':{'credit':{'name':'Credit analytics','definition':'Credit analytics duties','function':'analytics','domains':['credit']}},
                 'assignments':{'d0':{'source_sha256':rows[0]['clean_sha256'],'role_group_id':'credit','evidence':'Analyze credit risk.'}},'skills':{}}
        approved=approve(self.directory,copy.deepcopy(payload))
        self.assertTrue(approved['version'])
        payload['groups']['credit']['definition']='Engineering'
        with self.assertRaises(ValueError):approve(self.directory,payload)
        bad=copy.deepcopy(approved);bad['approved']=True;bad['assignments']['d0']['source_sha256']='changed'
        with self.assertRaises(ValueError):approve(self.directory,bad)

    def test_embeddings_are_bound_to_duty_profiles(self):
        self.documents(2)
        with self.assertRaises(ValueError):propose(self.directory,{'model_signature':'fixture','profiles':{'d0':{'profile_sha256':'skill-matrix','vector':[1,2]}}})

    def test_held_out_recall_and_frozen_labels(self):
        rows,records=self.documents(60)
        packet=gold_packet(self.directory)
        self.assertEqual(sum(d['split']=='held_out' for d in packet['documents']),40)
        for d in packet['documents']:
            d['labels']=next(r['requirements'] for r in records if r['document_id']==d['document_id'])
            d['omission_audited']=True
        labels=self.root/'labels.json';write_json(labels,packet)
        result=evaluate(self.directory,labels)
        self.assertEqual(result['status'],'passed')
        for record in records:record['requirements']=record['requirements'][:1]
        write_jsonl(self.directory/'requirements.jsonl',records)
        self.assertEqual(evaluate(self.directory,labels)['statement']['recall'],0.5)
        self.assertEqual(evaluate(self.directory,labels)['status'],'exploratory')

    def test_report_requires_approved_groups_and_thirty_assessed(self):
        rows,records=self.documents(2)
        first=report(self.directory,self.config)
        self.assertEqual(first['unassigned_vacancies'],2)
        self.assertFalse(first['requirement_shares'])
        approve(self.directory,{'approved':True,'reviewer':'fixture-human','groups':{'credit':{'name':'Credit','definition':'Credit duties','function':'analytics','domains':['credit']}},
                'assignments':{r['document_id']:{'source_sha256':r['clean_sha256'],'role_group_id':'credit','evidence':'Analyze credit risk.'} for r in rows}})
        for record in records:
            review_document(self.config,'study',{'document_id':record['document_id'],'extraction_id':extraction_identity(record),'omission_audited':True,
                            'description_completeness':'complete','selection_review':'complete'})
        result=report(self.directory,self.config)
        self.assertTrue(result['requirement_shares'])
        self.assertTrue(all(r['share_percent'] is None for r in result['requirement_shares']))
        self.assertEqual({r['time_basis'] for r in result['requirement_shares']},{'capture_period','publication_observed_today'})
        self.assertTrue((self.directory/'role-baseline.html').exists())
