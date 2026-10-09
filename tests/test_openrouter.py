import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch, MagicMock
from urllib.error import HTTPError, URLError

from degent.openrouter import OpenRouterRunner, local_settings, model_identity, APPROVED_MODELS, configuration
from degent.model_runner import request_for, expand_wire
from degent.evidence import select_units
from degent.study_metrics import assessed, statement_concepts


class OpenRouterTests(unittest.TestCase):
    def test_literal_dotenv_and_environment_precedence(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory)/'.env'
            path.write_text('OPENROUTER_API_KEY=\"local-secret\"\nOPENROUTER_MODEL=openai/gpt-oss-20b\nOTHER=ignored\n')
            with patch.dict('os.environ', {}, clear=True):
                self.assertEqual(local_settings(path)['OPENROUTER_API_KEY'], 'local-secret')
                self.assertNotIn('OTHER', local_settings(path))
            with patch.dict('os.environ', {'OPENROUTER_API_KEY':'environment-secret'}):
                self.assertEqual(local_settings(path)['OPENROUTER_API_KEY'], 'environment-secret')

    def test_explicit_models_only_and_disabled_allowances(self):
        for model in APPROVED_MODELS:
            self.assertEqual(model_identity(model)['model'], model)
            self.assertFalse(configuration(model)['enabled'])
            self.assertFalse(configuration(model)['spending_authorized'])
        with self.assertRaises(ValueError):
            model_identity('openrouter/auto')

    def test_transport_schema_usage_and_no_fallback(self):
        source='Basic Qualifications\nSQL required.'
        selection=select_units(source)
        request=request_for(selection);request['max_output_tokens']=4096
        unit=next(u for u in selection['units'] if u['text']=='SQL required.')
        wire={'r':[{'u':unit['unit_id'],'span':[0,13],'c':'SQL','s':'required','k':'qualification',
                    'q':None,'t':None,'y':None,'a':None,'op':None}]}
        response=MagicMock()
        response.__enter__.return_value.read.return_value=json.dumps({'id':'generation-1','choices':[{'message':{'content':json.dumps(wire)},'finish_reason':'stop'}],
            'usage':{'prompt_tokens':101,'completion_tokens':29,'cost':.000012,'completion_tokens_details':{'reasoning_tokens':4}}}).encode()
        opener=MagicMock();opener.open.return_value=response
        runner=OpenRouterRunner(api_key='local-secret')
        with patch('degent.openrouter.build_opener', return_value=opener):
            result=runner.extract(request)
        sent=opener.open.call_args.args[0];body=json.loads(sent.data)
        self.assertEqual(body['model'],'openai/gpt-6-luna')
        self.assertNotIn('models',body)
        self.assertFalse(body['provider']['allow_fallbacks'])
        self.assertTrue(body['provider']['require_parameters'])
        schema=body['response_format']['json_schema']['schema']['properties']['r']['items']
        self.assertEqual(set(schema['required']),set(schema['properties']))
        self.assertNotIn('local-secret', repr(runner.identity))
        self.assertEqual(result.usage['input_tokens'],101)
        self.assertEqual(result.usage['cost_usd'],.000012)
        self.assertEqual(expand_wire(result.output,'doc',selection,source)[1],[])

    def test_errors_never_echo_secret_and_redirects_are_rejected(self):
        from degent.openrouter import NoRedirect
        self.assertIsNone(NoRedirect().redirect_request(None,None,None,None,None,None))
        runner=OpenRouterRunner(api_key='local-secret')
        request=request_for(select_units('SQL required.'));request['max_output_tokens']=100
        for error in (URLError('local-secret'),HTTPError('https://openrouter.ai',401,'local-secret',{},None)):
            with patch('degent.openrouter.build_opener') as factory:
                factory.return_value.open.side_effect=error
                with self.assertRaises(RuntimeError) as raised:
                    runner.extract(request)
                self.assertNotIn('local-secret',str(raised.exception))
                self.assertEqual(factory.return_value.open.call_count,1)

    def test_and_vs_or_prevalence_and_mentions(self):
        source='Basic Qualifications\nSQL and Python required.'
        selection=select_units(source);u=next(u for u in selection['units'] if u['text'].startswith('SQL'))
        wire={'r':[{'u':u['unit_id'],'span':[0,len(u['text'])],'c':'SQL','s':'required','k':'qualification','a':['Python'],'op':'and'}]}
        record,errors=expand_wire(wire,'doc',selection,source);self.assertFalse(errors)
        record.update(accepted=True,method_signature='m',capture_completeness='full_provider_description')
        gate={'status':'passed','method_signature':'m'}
        python=record['requirements'][0]['alternative_concepts'][0]['concept']
        self.assertEqual(assessed(record,python,'required','evaluated_automated',gate,'technical')[:2],(True,True))
        record['requirements'][0]['alternative_logic']='or'
        self.assertEqual(assessed(record,python,'required','evaluated_automated',gate,'technical')[:2],(False,False))
        record['requirements'][0]['status']='preferred'
        self.assertEqual(assessed(record,python,'preferred','evaluated_automated',gate,'technical')[:2],(False,False))
        self.assertIn(python,statement_concepts(record['requirements'][0]))
