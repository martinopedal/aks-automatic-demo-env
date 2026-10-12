import copy
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch
from guard import ROOT, GateError
from qualify import check_claims, check_receipt, seal, write, AzureReadOnly
from test_native import fixture

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
        with tempfile.TemporaryDirectory() as d:
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
        with tempfile.TemporaryDirectory() as d,patch('urllib.request.urlopen',return_value=Response()):
            api=AzureReadOnly('not-a-real-token',Path(d));status,headers,value=api.request('validate','POST','https://management.azure.com/fixture',{})
            self.assertEqual(status,403)
            self.assertEqual((Path(d)/'001-validate.raw').read_bytes(),Response().read())
            self.assertNotIn('not-a-real-token',(Path(d)/'001-validate.http.json').read_text())
    def test_network_transport_failure_evidence(self):
        with tempfile.TemporaryDirectory() as d,patch('urllib.request.urlopen',side_effect=TimeoutError('fixture')):
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

if __name__=='__main__':unittest.main()
