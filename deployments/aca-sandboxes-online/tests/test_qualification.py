import copy
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch
from guard import ROOT, GateError, graph, check_whatif
from qualify import ARM, ArmNoRedirect, check_claims, check_receipt, seal, write, AzureReadOnly, main
from test_native import fixture, TEST_DIRECTORY

class ProtectedQualification(unittest.TestCase):
    def setUp(self):
        self.s=fixture()
        self.env={'GITHUB_REF_PROTECTED':'true','GITHUB_SHA':'a'*40,'GITHUB_RUN_ID':'1234','GITHUB_RUN_ATTEMPT':'2'}
        self.c=dict(iss=self.s['oidc']['issuer'],aud=self.s['oidc']['audience'],sub=self.s['oidc']['subject'],
                    repository=self.s['source']['repository'],environment='online',ref='refs/heads/main',ref_protected='true',
                    sha='a'*40,run_id='1234',run_attempt='2',workflow_ref=self.s['source']['repository']+'/.github/workflows/aca-sandboxes-online.yml@refs/heads/main',iat=1000,exp=1600)
    def test_current_claims(self): check_claims(self.c,self.s,self.env,1100)
    def test_wrong_cached_group_token_is_not_authority(self):
        for field,value,reason in [('sub','Management','ISSUED_OIDC_SUBJECT'),('sha','b'*40,'ISSUED_OIDC_CURRENT_RUN'),('run_id','old','ISSUED_OIDC_CURRENT_RUN'),('run_attempt','1','ISSUED_OIDC_CURRENT_RUN'),('environment','production','ISSUED_OIDC_PROTECTED_ENVIRONMENT'),('ref_protected','false','ISSUED_OIDC_PROTECTED_ENVIRONMENT'),('exp',1000,'ISSUED_OIDC_FRESHNESS'),('workflow_ref','other','ISSUED_OIDC_WORKFLOW')]:
            c=copy.deepcopy(self.c);c[field]=value
            with self.subTest(field=field),self.assertRaisesRegex(GateError,reason):check_claims(c,self.s,self.env,1100)
    def test_github_protected_branch_context_without_optional_jwt_claim(self):
        c=copy.deepcopy(self.c);c.pop('ref_protected')
        check_claims(c,self.s,self.env,1100)
        env=copy.deepcopy(self.env);env['GITHUB_REF_PROTECTED']='false'
        with self.assertRaisesRegex(GateError,'ISSUED_OIDC_PROTECTED_ENVIRONMENT'):check_claims(c,self.s,env,1100)
    def test_exact_scope_receipt_hash_and_target(self):
        import hashlib
        receipt=dict(response=dict(approve_isolated_setup=True),approvedDurableCount=5,
                     resourceGroup=self.s['target']['resourceGroupName'],sandboxGroup=self.s['target']['sandboxGroupName'],
                     recipe='Native Bicep for this isolated Sandbox package only')
        raw=json.dumps(receipt);self.s['scopeApprovalSha256']=hashlib.sha256(raw.encode()).hexdigest()
        check_receipt(raw,self.s)
        with self.assertRaisesRegex(GateError,'SCOPE_RECEIPT_BYTES'):check_receipt(raw+' ',self.s)
        self.s['target']['sandboxGroupName']='sbg-unapproved'
        with self.assertRaisesRegex(GateError,'SCOPE_RECEIPT_TARGET'):check_receipt(raw,self.s)
    def test_unexpected_raw_is_preserved_before_failed_gate(self):
        with tempfile.TemporaryDirectory(dir=TEST_DIRECTORY) as d:
            p=Path(d);write(p/'raw-whatif.private.json',{'status':'Failed','error':{'code':'AuthorizationFailed'}})
            h=seal(p,'FAILED_READONLY','NATIVE_VALIDATE_FAILED')
            m=json.loads((p/'seal.json').read_text())
            self.assertEqual(m['outcome'],'FAILED_READONLY')
            self.assertEqual(len(h),64)
            self.assertIn('raw-whatif.private.json',m['files'])
            with self.assertRaises(FileExistsError):write(p/'raw-whatif.private.json',{})
    def test_http_failure_complete_raw_before_parse(self):
        class Response:
            status=403
            headers={'x-ms-request-id':'fixture-request'}
            def read(self):return b'{"error":{"code":"AuthorizationFailed"}}'
        with tempfile.TemporaryDirectory(dir=TEST_DIRECTORY) as d,patch('urllib.request.OpenerDirector.open',return_value=Response()):
            api=AzureReadOnly('not-a-real-token',Path(d));status,headers,value=api.request('validate','POST','https://management.azure.com/fixture',{})
            self.assertEqual(status,403)
            self.assertEqual((Path(d)/'001-validate.raw').read_bytes(),Response().read())
            self.assertNotIn('not-a-real-token',(Path(d)/'001-validate.http.json').read_text())
    def test_network_transport_failure_evidence(self):
        with tempfile.TemporaryDirectory(dir=TEST_DIRECTORY) as d,patch('urllib.request.OpenerDirector.open',side_effect=TimeoutError('fixture')):
            api=AzureReadOnly('not-a-real-token',Path(d))
            with self.assertRaisesRegex(GateError,'ARM_TRANSPORT_FAILED'):api.request('baseline','GET','https://management.azure.com/fixture')
            self.assertTrue((Path(d)/'001-baseline-transport.json').exists())
    def test_workflow_actual_no_auth_source_and_protected_read_only(self):
        w=json.loads((ROOT.parents[1]/'.github/workflows/aca-sandboxes-online.yml').read_text())
        source=w['jobs']['source'];live=w['jobs']['qualify']
        self.assertEqual(source['permissions'],{'contents':'read'})
        self.assertNotIn('environment',source)
        self.assertTrue(all('env' not in s for s in source['steps']))
        self.assertEqual(live['environment'],'online')
        self.assertEqual(live['needs'],['source'])
        self.assertEqual(live['runs-on'],['self-hosted','aks-online-demo'])
        self.assertEqual(live['permissions'],{'contents':'read','id-token':'write'})
        self.assertNotIn('inputs',w['on']['workflow_dispatch'])
        self.assertEqual(set(w['jobs']),{'source','qualify'})
        for job in w['jobs'].values():
            for step in job['steps']:
                if 'uses' in step:self.assertRegex(step['uses'],r'@[0-9a-f]{40}$')
                self.assertNotIn('upload-artifact',step.get('uses',''))
                self.assertNotIn('create',step.get('run',''))
                self.assertNotIn('apply',step.get('run',''))

class NativeOperations(unittest.TestCase):
    subscription='/subscriptions/00000000-0000-0000-0000-000000000001'
    result=subscription+'/operationResults/fixture?api-version=2025-04-01'
    monitor=subscription+'/operationStatuses/fixture?api-version=2025-04-01'

    def response(self, status, headers=None, value=None, raw=None):
        class Response:
            def read(self):return self.data
        response=Response();response.status=status;response.headers=headers or {}
        response.data=json.dumps(value,indent=2).encode()+b'\n' if raw is None else raw
        return response

    def native(self, label, state='Succeeded'):
        if label=='validate':
            return dict(name='my-deployment',type='Microsoft.Resources/deployments',
                        id=self.subscription+'/providers/Microsoft.Resources/deployments/my-deployment',
                        properties=dict(provisioningState=state,diagnostics=[],validatedResources=[]))
        return dict(status=state,properties=dict(changes=[
            dict(resourceId=o['id'],changeType='Create',after=o) for o in graph(fixture())]))

    def run_operation(self, label, responses):
        # Real parsing/persistence with controlled HTTP only; no live endpoint.
        with tempfile.TemporaryDirectory(dir=TEST_DIRECTORY) as d, \
             patch('urllib.request.OpenerDirector.open',side_effect=responses) as http, \
             patch('time.sleep') as sleep:
            api=AzureReadOnly('offline-fixture-token',Path(d))
            ok,value=api.operation(label,ARM+self.subscription+'/providers/Microsoft.Resources/deployments/my-deployment/'+('whatIf' if label=='whatif' else label)+'?api-version=2025-04-01',{})
            calls=[(c.args[0].method,c.args[0].full_url) for c in http.call_args_list]
            self.assertEqual(http.call_count,len(responses))
            for i,response in enumerate(responses,1):
                stem=f'{i:03d}-{label}'+('' if i==1 else '-poll')
                self.assertEqual((Path(d)/(stem+'.raw')).read_bytes(),response.data)
                record=json.loads((Path(d)/(stem+'.http.json')).read_bytes())
                self.assertEqual(record['status'],response.status)
                self.assertEqual(record['headers'],response.headers)
                import hashlib
                self.assertEqual(record['rawSha256'],hashlib.sha256(response.data).hexdigest())
                self.assertNotIn('offline-fixture-token',json.dumps(record))
            return ok,value,calls,[c.args[0] for c in sleep.call_args_list]

    def test_documented_native_200_results(self):
        for label in ('validate','whatif'):
            value=self.native(label)
            with self.subTest(label=label):
                ok,actual,calls,delays=self.run_operation(label,[self.response(200,value=value)])
                self.assertTrue(ok);self.assertEqual(actual,value);self.assertEqual(delays,[])
                if label=='whatif':check_whatif(actual,fixture())

    def test_documented_relative_202_location_both_operations(self):
        for label in ('validate','whatif'):
            value=self.native(label)
            responses=[self.response(202,{'Location':self.result,'Retry-After':'7'},raw=b''),
                       self.response(200,{'x-ms-request-id':'fixture'},value=value)]
            with self.subTest(label=label):
                ok,actual,calls,delays=self.run_operation(label,responses)
                self.assertTrue(ok);self.assertEqual(actual,value)
                self.assertEqual(calls[1],('GET',ARM+self.result));self.assertEqual(delays,[7])

    def test_native_validation_is_not_default_success(self):
        for value in ({},None,[],{'properties':{}},{'properties':{'provisioningState':'Failed'}},
                      {'properties':{'provisioningState':'Canceled'}},{'status':'Succeeded'},
                      {'properties':{'provisioningState':42}}, {'properties':{'provisioningState':[]}},
                      {'status':'Succeeded','properties':{'provisioningState':'Failed'}}):
            with self.subTest(value=value):
                ok,actual,_,_=self.run_operation('validate',[self.response(200,value=value)])
                self.assertFalse(ok);self.assertEqual(actual,value)

    def test_native_whatif_requires_result_not_status_receipt(self):
        for value in ({},None,{'status':'Succeeded'},{'status':'Succeeded','changes':[]},
                      {'status':'Succeeded','properties':None},{'properties':{'changes':[]}}):
            with self.subTest(value=value):
                ok,actual,_,_=self.run_operation('whatif',[self.response(200,value=value)])
                self.assertFalse(ok);self.assertEqual(actual,value)

    def test_top_level_and_nested_errors_are_not_success(self):
        for label in ('validate','whatif'):
            for where in ('top','properties'):
                value=self.native(label)
                (value if where=='top' else value['properties'])['error']={'code':'FixtureFailure'}
                with self.subTest(label=label,where=where):
                    ok,actual,_,_=self.run_operation(label,[self.response(200,value=value)])
                    self.assertFalse(ok);self.assertEqual(actual,value)

    def test_nonterminal_native_validation_200_and_202_keep_polling(self):
        for state in ('Accepted','Running','Creating','Updating','Ready'):
            value=self.native('validate')
            responses=[self.response(200,{'Location':self.result,'Retry-After':'3'},self.native('validate',state)),
                       self.response(202,{'Retry-After':'5'},self.native('validate','Running')),
                       self.response(200,value=value)]
            with self.subTest(state=state):
                ok,actual,calls,delays=self.run_operation('validate',responses)
                self.assertTrue(ok);self.assertEqual(actual,value)
                self.assertEqual([c[1] for c in calls[1:]],[ARM+self.result]*2)
                self.assertEqual(delays,[3,5])

    def test_chained_location_headers_replace_previous_poll(self):
        second=self.result.replace('fixture','second')
        for label in ('validate','whatif'):
            responses=[self.response(202,{'Location':self.result,'Retry-After':'1'},raw=b''),
                       self.response(202,{'Location':second,'Retry-After':'4'},raw=b''),
                       self.response(200,value=self.native(label))]
            with self.subTest(label=label):
                ok,actual,calls,delays=self.run_operation(label,responses)
                self.assertTrue(ok);self.assertEqual(calls[2],('GET',ARM+second));self.assertEqual(delays,[1,4])

    def test_async_monitor_success_fetches_separate_actual_result(self):
        for label in ('validate','whatif'):
            responses=[self.response(202,{'Azure-AsyncOperation':ARM+self.monitor,'Location':self.result,'Retry-After':'2'},raw=b''),
                       self.response(200,{'Retry-After':'3'},dict(status='Running')),
                       self.response(200,{'Retry-After':'4'},dict(status='Succeeded')),
                       self.response(200,value=self.native(label))]
            with self.subTest(label=label):
                ok,actual,calls,delays=self.run_operation(label,responses)
                self.assertTrue(ok);self.assertEqual(actual,self.native(label))
                self.assertEqual([c[1] for c in calls[1:]],[ARM+self.monitor,ARM+self.monitor,ARM+self.result])
                self.assertEqual(delays,[2,3,4])

    def test_async_monitor_success_without_result_location_is_not_validation(self):
        with tempfile.TemporaryDirectory(dir=TEST_DIRECTORY) as d,patch('urllib.request.OpenerDirector.open',side_effect=[
            self.response(202,{'Azure-AsyncOperation':ARM+self.monitor},raw=b''),
            self.response(200,value=dict(status='Succeeded'))]),patch('time.sleep'):
            with self.assertRaisesRegex(GateError,'NATIVE_RESULT_URL_REQUIRED'):
                AzureReadOnly('fixture',Path(d)).operation('validate',ARM+'/fixture',{})
            self.assertEqual(len(list(Path(d).glob('*.raw'))),2)

    def test_transient_get_responses_retry_without_resubmitting_post(self):
        for label in ('validate','whatif'):
            responses=[self.response(202,{'Location':self.result,'Retry-After':'1'},raw=b'')]
            responses += [self.response(code,{'Retry-After':'9'},dict(error=dict(code='FixtureTransient'))) for code in (429,500,502,503,504)]
            responses += [self.response(200,value=self.native(label))]
            with self.subTest(label=label):
                ok,actual,calls,delays=self.run_operation(label,responses)
                self.assertTrue(ok);self.assertEqual([c[0] for c in calls],['POST']+['GET']*6)
                self.assertEqual(delays,[1]+[9]*5)

    def test_failed_poll_and_malformed_bytes_remain_full_raw(self):
        for label in ('validate','whatif'):
            for final in (self.response(403,value=dict(error=dict(code='AuthorizationFailed'))),
                          self.response(200,raw=b'not JSON\r\n'),self.response(200,value=self.native(label,'Failed'))):
                with self.subTest(label=label,final=final.data):
                    ok,actual,_,_=self.run_operation(label,[self.response(202,{'Location':ARM+self.result},raw=b''),final])
                    self.assertFalse(ok)

    def test_unsafe_poll_urls_fail_before_any_get(self):
        unsafe=['//management.azure.com'+self.result,'https://example.com'+self.result,
                'http://management.azure.com'+self.result,'https://management.azure.com.evil.invalid'+self.result,
                'https://user@management.azure.com'+self.result,'https://management.azure.com@evil.invalid'+self.result,
                'https://management.azure.com:444'+self.result,'/\\\\evil.invalid/path',
                '/%5c%5cevil.invalid/path',ARM+self.result+'#fragment',ARM+self.result+'\\r\\n'.encode().decode('unicode_escape')]
        for url in unsafe:
            with self.subTest(url=url),tempfile.TemporaryDirectory(dir=TEST_DIRECTORY) as d, \
                 patch('urllib.request.OpenerDirector.open',side_effect=[self.response(202,{'Location':url},raw=b'')]) as http,patch('time.sleep'):
                with self.assertRaisesRegex(GateError,'NATIVE_ASYNC_POLL_URL'):
                    AzureReadOnly('fixture',Path(d)).operation('validate',ARM+'/fixture',{})
                self.assertEqual(http.call_count,1)
                self.assertTrue((Path(d)/'001-validate.raw').exists())

    def test_retry_after_not_clamped_and_deadline_prevents_late_poll(self):
        responses=[self.response(202,{'Location':ARM+self.result,'Retry-After':'45'},raw=b''),
                   self.response(200,value=self.native('validate'))]
        ok,_,_,delays=self.run_operation('validate',responses)
        self.assertTrue(ok);self.assertEqual(delays,[45])
        with tempfile.TemporaryDirectory(dir=TEST_DIRECTORY) as d,patch('urllib.request.OpenerDirector.open',side_effect=[
             self.response(202,{'Location':ARM+self.result,'Retry-After':'301'},raw=b'')]) as http,patch('time.sleep') as sleep:
            with self.assertRaisesRegex(GateError,'NATIVE_ASYNC_DEADLINE'):
                AzureReadOnly('fixture',Path(d)).operation('validate',ARM+'/fixture',{})
            self.assertEqual(http.call_count,1);sleep.assert_not_called()

    def test_nonterminal_without_poll_location_fails_closed(self):
        with tempfile.TemporaryDirectory(dir=TEST_DIRECTORY) as d,patch('urllib.request.OpenerDirector.open',return_value=
             self.response(200,value=self.native('validate','Accepted'))):
            with self.assertRaisesRegex(GateError,'NATIVE_ASYNC_POLL_URL'):
                AzureReadOnly('fixture',Path(d)).operation('validate',ARM+'/fixture',{})

    def test_async_success_does_not_override_failed_actual_result(self):
        for label in ('validate','whatif'):
            responses=[self.response(202,{'Azure-AsyncOperation':self.monitor,'Location':self.result},raw=b''),
                       self.response(200,value=dict(status='Succeeded')),
                       self.response(200,value=self.native(label,'Failed'))]
            with self.subTest(label=label):
                ok,actual,calls,_=self.run_operation(label,responses)
                self.assertFalse(ok);self.assertEqual(actual,self.native(label,'Failed'))
                self.assertEqual(calls[-1],('GET',ARM+self.result))

    def test_validation_warning_diagnostics_do_not_prove_complete_validation(self):
        for diagnostics in ([dict(level='Warning',code='NestedDeploymentExpansionLimitReached')],
                            [dict(level='Error',code='FixtureFailure')],{},[{}]):
            value=self.native('validate');value['properties']['diagnostics']=diagnostics
            with self.subTest(diagnostics=diagnostics):
                ok,actual,_,_=self.run_operation('validate',[self.response(200,value=value)])
                self.assertFalse(ok);self.assertEqual(actual,value)

    def test_redirect_is_preserved_and_never_followed(self):
        import io
        import urllib.error
        import urllib.request
        req=urllib.request.Request(ARM+'/fixture')
        self.assertIsNone(ArmNoRedirect().redirect_request(req,None,302,'fixture',{},'https://external.invalid/fixture'))
        response=urllib.error.HTTPError(ARM+'/fixture',302,'fixture',{'Location':'https://external.invalid/fixture'},io.BytesIO(b'fixture redirect\r\n'))
        with tempfile.TemporaryDirectory(dir=TEST_DIRECTORY) as d,patch('urllib.request.OpenerDirector.open',side_effect=response) as http:
            status,headers,value=AzureReadOnly('fixture',Path(d)).request('validate','POST',ARM+'/fixture',{})
            self.assertEqual(status,302);self.assertEqual(http.call_count,1)
            self.assertEqual((Path(d)/'001-validate.raw').read_bytes(),b'fixture redirect\r\n')
            self.assertEqual(headers['location'],'https://external.invalid/fixture');self.assertIsNone(value)

    def test_invalid_retry_and_elapsed_deadline_are_bounded(self):
        for retry in ('-1','invalid',''):
            with self.subTest(retry=retry),tempfile.TemporaryDirectory(dir=TEST_DIRECTORY) as d, \
                 patch('urllib.request.OpenerDirector.open',side_effect=[self.response(202,{'Location':self.result,'Retry-After':retry},raw=b'')]) as http,patch('time.sleep') as sleep:
                with self.assertRaisesRegex(GateError,'NATIVE_RETRY_AFTER'):
                    AzureReadOnly('fixture',Path(d)).operation('whatif',ARM+'/fixture',{})
                self.assertEqual(http.call_count,1);sleep.assert_not_called()
        with tempfile.TemporaryDirectory(dir=TEST_DIRECTORY) as d, \
             patch('urllib.request.OpenerDirector.open',side_effect=[self.response(202,{'Location':self.result},raw=b'')]) as http, \
             patch('time.monotonic',side_effect=[0,0,0,300]),patch('time.sleep') as sleep:
            with self.assertRaisesRegex(GateError,'NATIVE_ASYNC_DEADLINE'):
                AzureReadOnly('fixture',Path(d)).operation('validate',ARM+'/fixture',{})
            self.assertEqual(http.call_count,1);sleep.assert_called_once_with(2)
            self.assertTrue((Path(d)/'001-validate.raw').exists())

    def test_main_seals_failed_validation_and_complete_native_whatif_offline(self):
        from datetime import datetime, timezone
        import hashlib
        from guard import parameters
        scope=fixture();scope['qualificationUTC']=datetime.now(timezone.utc).isoformat()
        receipt=json.dumps(dict(response=dict(approve_isolated_setup=True),approvedDurableCount=5,
                                resourceGroup=scope['target']['resourceGroupName'],sandboxGroup=scope['target']['sandboxGroupName'],
                                recipe='Native Bicep for this isolated Sandbox package only'))
        scope['scopeApprovalSha256']=hashlib.sha256(receipt.encode()).hexdigest()
        env=dict(GITHUB_REPOSITORY=scope['source']['repository'],GITHUB_SHA=scope['source']['commit'],
                 GITHUB_REF='refs/heads/main',GITHUB_RUN_ID='1234',GITHUB_RUN_ATTEMPT='2',
                 ACA_ONLINE_SCOPE_JSON=json.dumps(scope),ACA_ONLINE_PARAMETERS_JSON=json.dumps(parameters(scope)),
                 ACA_ONLINE_SCOPE_APPROVAL_JSON=receipt)
        failed=self.native('validate','Failed');failed['properties']['error']={'code':'FixtureFailure'}
        raw=self.native('whatif')
        responses=[self.response(404,{'x-ms-request-id':f'baseline-{i}'},dict(error=dict(code='ResourceNotFound'))) for i in range(5)]
        responses += [self.response(200,{'x-ms-request-id':'failed-validation'},failed),
                      self.response(202,{'Location':self.result,'Retry-After':'2'},raw=b''),
                      self.response(200,{'x-ms-request-id':'final-whatif'},raw)]
        with tempfile.TemporaryDirectory(dir=TEST_DIRECTORY) as d, \
             patch('qualify.private_directory',return_value=Path(d)),patch('qualify.exchange',return_value='offline-fixture-token'), \
             patch('urllib.request.OpenerDirector.open',side_effect=responses) as http,patch('time.sleep'),patch('builtins.print'):
            self.assertEqual(main(env),1);self.assertEqual(http.call_count,8)
            directory=Path(d);manifest=json.loads((directory/'seal.json').read_bytes())
            self.assertEqual(manifest['outcome'],'FAILED_READONLY');self.assertEqual(manifest['reason'],'NATIVE_VALIDATE_FAILED')
            self.assertEqual(json.loads((directory/'native-results.private.json').read_bytes()),
                             dict(baselineAccepted=True,validateAccepted=False,whatifAccepted=True))
            self.assertEqual(json.loads((directory/'raw-whatif.private.json').read_bytes()),raw)
            check_whatif(raw,scope)
            raw_files=sorted(directory.glob('*.raw'));self.assertEqual(len(raw_files),8)
            for path,response in zip(raw_files,responses):
                self.assertEqual(path.read_bytes(),response.data)
                self.assertEqual(manifest['files'][path.name],hashlib.sha256(response.data).hexdigest())
                record=json.loads(path.with_suffix('.http.json').read_bytes())
                self.assertEqual(record['status'],response.status);self.assertEqual(record['headers'],response.headers)
            self.assertEqual((directory/'template.json').read_bytes(),(ROOT/'main.json').read_bytes())
            self.assertEqual([c.args[0].method for c in http.call_args_list],['GET']*5+['POST','POST','GET'])

if __name__=='__main__':unittest.main()
