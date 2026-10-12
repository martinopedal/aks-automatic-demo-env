import copy
import json
import os
from pathlib import Path
import shutil
import subprocess
import tempfile
import unittest
import sys
sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
from guard import GateError, ROOT, check_template, check_parameters, parameters, graph, check_whatif, check_baseline, validate_scope


def fixture():
    return dict(target=dict(tenantId='11111111-1111-4111-8111-111111111111',subscriptionId='22222222-2222-4222-8222-222222222222',
                           resourceGroupName='rg-sandbox-example',sandboxGroupName='sbg-sandbox-example',region='swedencentral'),
                presenterIds=['33333333-3333-4333-8333-333333333333','44444444-4444-4444-8444-444444444444'],
                consumer=dict(principalId='55555555-5555-4555-8555-555555555555',clientId='66666666-6666-4666-8666-666666666666'),
                source=dict(repository='martinopedal/aks-automatic-demo-env',commit='a'*40),
                oidc=dict(issuer='https://token.actions.githubusercontent.com',audience='api://AzureADTokenExchange',
                          subject='repo:martinopedal@123456/aks-automatic-demo-env@234567:environment:online'),
                scopeApprovalSha256='b'*64,qualificationUTC='2026-10-12T11:00:00+00:00')

class ActualCompiledTemplates(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.tool=os.environ.get('ACA_BICEP') or shutil.which('bicep')
        result=subprocess.run([cls.tool,'build',str(ROOT/'main.bicep'),'--stdout'],capture_output=True,text=True,check=True)
        cls.template=json.loads(result.stdout)

    def mutation(self, change, reason):
        t=copy.deepcopy(self.template); change(t)
        with self.assertRaisesRegex(GateError, '^'+reason+'$'): check_template(t)

    def test_actual_compiled_positive_exact_five(self):
        check_template(self.template)
        self.assertEqual(self.template,json.loads((ROOT/'main.json').read_text()))
        self.assertEqual(len(graph(fixture())),5)

    def test_preview_warnings_are_not_suppressed(self):
        result=subprocess.run([self.tool,'build',str(ROOT/'main.bicep'),'--stdout'],capture_output=True,text=True,check=True)
        self.assertIn('BCP081',result.stderr)
        self.assertNotIn('#disable', (ROOT/'sandbox.bicep').read_text())

    def test_real_source_mutations_compile_then_fail_intended_reason(self):
        mutations=[
            ("Microsoft.App/sandboxGroups@2026-07-01", "Microsoft.App/sessionPools@2025-07-01", 'SANDBOX_TYPE'),
            ('properties: {}', "properties: { environmentId: '/fake/environment' }", 'SANDBOX_PROPERTIES_OR_IDENTITY'),
            ('properties: {}', "properties: { vnetConnections: [] }", 'SANDBOX_PROPERTIES_OR_IDENTITY'),
            ('properties: {}', "properties: {}\n  identity: { type: 'SystemAssigned' }", 'SANDBOX_PROPERTIES_OR_IDENTITY'),
            ('c24cf47c-5077-412d-a19c-45202126392c', 'b24988ac-6180-42a0-ab88-20f7382dd24c', 'UNAPPROVED_ROLE'),
            ('principalId: presenterIds[0]', 'principalId: consumerPrincipalId', 'UNAPPROVED_PRINCIPAL'),
            ('scope: sandboxGroup', '', 'GRANT_SCOPE'),
            ('name: assignmentNames[0]', 'name: assignmentNames[1]', 'ASSIGNMENT_ID_EXPRESSION'),
        ]
        for old,new,reason in mutations:
            with self.subTest(reason=reason, replacement=new), tempfile.TemporaryDirectory() as d:
                dst=Path(d)
                for name in ('main.bicep','sandbox.bicep','bicepconfig.json'): shutil.copyfile(ROOT/name,dst/name)
                (dst/'sandbox.bicep').write_text((dst/'sandbox.bicep').read_text().replace(old,new,1))
                p=subprocess.run([self.tool,'build',str(dst/'main.bicep'),'--stdout'],capture_output=True,text=True)
                self.assertEqual(p.returncode,0,p.stderr)
                with self.assertRaisesRegex(GateError, '^'+reason+'$'): check_template(json.loads(p.stdout))

    def test_extra_root_leaf_fails(self):
        self.mutation(lambda t:t['resources'].append({'type':'Microsoft.Network/virtualNetworks'}), 'ROOT_OUTSIDE_FIVE')
    def test_extra_child_leaf_fails(self):
        self.mutation(lambda t:t['resources'][1]['properties']['template']['resources'].append({'type':'Microsoft.ManagedIdentity/userAssignedIdentities'}), 'CHILD_OUTSIDE_FIVE')
    def test_rg_outside_approved_scope_fails(self):
        self.mutation(lambda t:t['resources'][1].update(subscriptionId='outside'), 'SANDBOX_WRAPPER_SCOPE')
    def test_complete_mode_fails(self):
        self.mutation(lambda t:t['resources'][1]['properties'].update(mode='Complete'), 'DEPLOYMENT_MODE')
    def test_telemetry_dependency_fails(self):
        self.mutation(lambda t:t['resources'][0]['properties']['parameters']['enableTelemetry'].update(value=True), 'AVM_EXTRA_DEPENDENCIES')
    def test_avm_dormant_identity_injection_fails(self):
        self.mutation(lambda t:t['resources'][0]['properties']['template']['resources']['resourceGroup'].update(identity={}), 'AVM_PUBLISHED_TEMPLATE')
    def test_scope_parameter_default_fails(self):
        self.mutation(lambda t:t['parameters']['resourceGroupName'].update(defaultValue='outside'), 'TEMPLATE_PARAMETER_DEFAULT_OR_TYPE')
    def test_new_connection_fails(self):
        self.mutation(lambda t:t['resources'][1]['properties']['template']['resources'][0]['properties'].update(defaultConnection={}), 'SANDBOX_PROPERTIES_OR_IDENTITY')

class PrivateInputsAndRawEvidence(unittest.TestCase):
    def setUp(self):
        self.s=fixture(); self.p=parameters(self.s)
        self.raw={'status':'Succeeded','changes':[dict(resourceId=o['id'],changeType='Create',before=None,after=o) for o in graph(self.s)]}
    def test_closed_positive(self): check_parameters(self.p,self.s); check_whatif(self.raw,self.s)
    def test_mutated_scope(self):
        self.p['parameters']['resourceGroupName']['value']='rg-outside'
        with self.assertRaisesRegex(GateError,'PARAMETER_SCOPE'): check_parameters(self.p,self.s)
    def test_unapproved_presenter(self):
        self.p['parameters']['presenterIds']['value'][0]='77777777-7777-4777-8777-777777777777'
        # parameters are not permitted to alias the frozen contract object in production.
        s=fixture()
        with self.assertRaisesRegex(GateError,'UNAPPROVED_PRINCIPAL'): check_parameters(self.p,s)
    def test_duplicate_ids(self):
        self.p['parameters']['assignmentNames']['value'][1]=self.p['parameters']['assignmentNames']['value'][0]
        with self.assertRaisesRegex(GateError,'DUPLICATE_ID'): check_parameters(self.p,self.s)
    def test_extra_parameter(self):
        self.p['parameters']['identity']={'value':'new'}
        with self.assertRaisesRegex(GateError,'PARAMETER_SET_CLOSED'): check_parameters(self.p,self.s)
    def test_additional_scope_contract_property(self):
        self.s['allowExtra']=True
        with self.assertRaisesRegex(GateError,'SCOPE_CLOSED'): validate_scope(self.s)
    def test_one_or_three_presenters(self):
        for users in [self.s['presenterIds'][:1], self.s['presenterIds']+[self.s['consumer']['principalId']]]:
            s=copy.deepcopy(self.s); s['presenterIds']=users
            with self.assertRaisesRegex(GateError,'TWO_PRESENTERS'): validate_scope(s)
    def test_no_deployment_actor_data_owner(self):
        self.s['presenterIds'][1]=self.s['consumer']['principalId']
        with self.assertRaisesRegex(GateError,'DUPLICATE_PRINCIPAL'): validate_scope(self.s)
    def test_raw_mutations(self):
        for typ in ('Delete','Modify','Ignore','NoChange','Unsupported'):
            x=copy.deepcopy(self.raw); x['changes'][0]['changeType']=typ
            with self.subTest(typ=typ), self.assertRaisesRegex(GateError,'WHATIF_NOT_CREATE'): check_whatif(x,self.s)
        for reason, mutate in [
            ('WHATIF_OUTSIDE_FIVE',lambda x:x['changes'].append(dict(resourceId='/outside',changeType='Create'))),
            ('WHATIF_MISSING_LEAF',lambda x:x['changes'].pop()),
            ('WHATIF_DUPLICATE_ID',lambda x:x['changes'].append(x['changes'][0])),
            ('WHATIF_PROPERTIES',lambda x:x['changes'][2]['after']['properties'].update(principalId='outside')),
            ('WHATIF_PROPERTIES',lambda x:x['changes'][1]['after']['properties'].update(environmentId='outside')),
            ('WHATIF_EXTRA_FIELDS',lambda x:x['changes'][1]['after'].update(identity={'type':'SystemAssigned'})),
            ('WHATIF_PAYLOAD_REQUIRED',lambda x:x['changes'][1].pop('after')),
            ('WHATIF_FAILED_STATUS',lambda x:x.update(status='Failed')),
            ('WHATIF_FULL_RAW_REQUIRED',lambda x:x.pop('changes')),
        ]:
            x=copy.deepcopy(self.raw); mutate(x)
            with self.subTest(reason=reason), self.assertRaisesRegex(GateError,reason): check_whatif(x,self.s)
    def test_baseline_not_empty_fake_inventory(self):
        with self.assertRaisesRegex(GateError,'BASELINE_FIVE_IDS'): check_baseline({},self.s)
    def test_baseline_forbidden_is_not_absence(self):
        b={o['id'].lower():dict(status=404,errorCode='ResourceNotFound') for o in graph(self.s)}
        check_baseline(b,self.s)
        b[next(iter(b))]['status']=403
        with self.assertRaisesRegex(GateError,'BASELINE_NOT_ABSENT'): check_baseline(b,self.s)
    def test_assignment_uuid_dns_recipe(self):
        self.assertEqual(graph(self.s),graph(copy.deepcopy(self.s)))
        self.assertEqual(len({o['id'] for o in graph(self.s)}),5)

if __name__=='__main__': unittest.main()
