"""Closed, fail-closed contracts over the actual compiled ARM, not Bicep text."""
import copy
import hashlib
import json
import re
import uuid
from pathlib import Path

ROOT = Path(__file__).resolve().parent
DATA_OWNER = 'c24cf47c-5077-412d-a19c-45202126392c'
CONTRIBUTOR = 'b24988ac-6180-42a0-ab88-20f7382dd24c'
PARAMS = {'resourceGroupName', 'sandboxGroupName', 'location', 'presenterIds',
          'consumerPrincipalId', 'assignmentNames'}

class GateError(ValueError):
    pass

def require(condition, reason):
    if not condition:
        raise GateError(reason)

def keys(value, expected, reason):
    require(isinstance(value, dict) and set(value) == set(expected), reason)

def digest(value):
    return hashlib.sha256(json.dumps(value, sort_keys=True, separators=(',', ':')).encode()).hexdigest()

def guid(value):
    require(isinstance(value, str) and re.fullmatch(r'[0-9a-f]{8}-(?:[0-9a-f]{4}-){3}[0-9a-f]{12}', value), 'GUID_FORMAT')
    require(uuid.UUID(value).int != 0, 'GUID_ZERO')
    return value

def validate_scope(s):
    keys(s, {'target','presenterIds','consumer','source','oidc','scopeApprovalSha256','qualificationUTC'}, 'SCOPE_CLOSED')
    t = s['target']
    keys(t, {'tenantId','subscriptionId','resourceGroupName','sandboxGroupName','region'}, 'TARGET_CLOSED')
    guid(t['tenantId']); guid(t['subscriptionId'])
    require(t['region'] == 'swedencentral', 'LOCATION')
    for field in ('resourceGroupName', 'sandboxGroupName'):
        require(isinstance(t[field], str) and re.fullmatch(r'[a-z][a-z0-9-]{2,62}', t[field]), 'RESOURCE_NAME')
    users = s['presenterIds']
    require(isinstance(users, list) and len(users) == 2, 'TWO_PRESENTERS')
    for user in users: guid(user)
    keys(s['consumer'], {'principalId','clientId'}, 'CONSUMER_CLOSED')
    guid(s['consumer']['principalId']); guid(s['consumer']['clientId'])
    require(len(set(users + [s['consumer']['principalId']])) == 3, 'DUPLICATE_PRINCIPAL')
    keys(s['source'], {'repository','commit'}, 'SOURCE_CLOSED')
    require(s['source']['repository'] == 'martinopedal/aks-automatic-demo-env', 'SOURCE_OWNER')
    require(re.fullmatch(r'[0-9a-f]{40}', s['source']['commit']) is not None, 'SOURCE_COMMIT')
    require(re.fullmatch(r'[0-9a-f]{64}', s['scopeApprovalSha256']) is not None, 'SCOPE_RECEIPT_HASH')
    keys(s['oidc'], {'issuer','audience','subject'}, 'OIDC_CLOSED')
    require(s['oidc']['issuer'] == 'https://token.actions.githubusercontent.com', 'OIDC_ISSUER')
    require(s['oidc']['audience'] == 'api://AzureADTokenExchange', 'OIDC_AUDIENCE')
    require(re.fullmatch(r'repo:martinopedal@\d+/aks-automatic-demo-env@\d+:environment:online', s['oidc']['subject']) is not None, 'OIDC_SUBJECT')
    return s

def graph(s):
    validate_scope(s)
    t = s['target']; sub = '/subscriptions/' + t['subscriptionId']
    rg = sub + '/resourceGroups/' + t['resourceGroupName']
    group = rg + '/providers/Microsoft.App/sandboxGroups/' + t['sandboxGroupName']
    objects = [dict(id=rg, type='Microsoft.Resources/resourceGroups', apiVersion='2025-04-01', location=t['region'], properties={}),
               dict(id=group, type='Microsoft.App/sandboxGroups', apiVersion='2026-07-01', location=t['region'], properties={})]
    grants = [(group, p, DATA_OWNER, 'User') for p in s['presenterIds']]
    grants.append((rg, s['consumer']['principalId'], CONTRIBUTOR, 'ServicePrincipal'))
    for scope, principal, role, ptype in grants:
        name = str(uuid.uuid5(uuid.NAMESPACE_DNS, f'{scope}|{principal}|{role}'.lower()))
        objects.append(dict(id=scope+'/providers/Microsoft.Authorization/roleAssignments/'+name,
                            type='Microsoft.Authorization/roleAssignments', apiVersion='2022-04-01',
                            properties=dict(principalId=principal, principalType=ptype,
                                            roleDefinitionId=sub+'/providers/Microsoft.Authorization/roleDefinitions/'+role)))
    require(len({o['id'].lower() for o in objects}) == 5, 'DUPLICATE_ID')
    return objects

def parameters(s):
    g = graph(s); t = s['target']
    vals = dict(resourceGroupName=t['resourceGroupName'], sandboxGroupName=t['sandboxGroupName'],
                location=t['region'], presenterIds=s['presenterIds'], consumerPrincipalId=s['consumer']['principalId'],
                assignmentNames=[o['id'].split('/')[-1] for o in g[2:]])
    return {'$schema':'https://schema.management.azure.com/schemas/2019-04-01/deploymentParameters.json#',
            'contentVersion':'1.0.0.0','parameters':{k:{'value':copy.deepcopy(v)} for k,v in vals.items()}}

def check_parameters(p, s):
    keys(p, {'$schema','contentVersion','parameters'}, 'PARAMETER_FILE_CLOSED')
    keys(p['parameters'], PARAMS, 'PARAMETER_SET_CLOSED')
    for v in p['parameters'].values(): keys(v, {'value'}, 'NO_PARAMETER_REFERENCE')
    v = p['parameters']; expected = parameters(s)['parameters']
    for k in ('resourceGroupName','sandboxGroupName','location'):
        require(v[k] == expected[k], 'PARAMETER_SCOPE')
    for k in ('presenterIds','consumerPrincipalId'):
        require(v[k] == expected[k], 'UNAPPROVED_PRINCIPAL')
    names = v['assignmentNames']['value']
    require(isinstance(names, list) and len(names)==3 and len(set(names))==3, 'DUPLICATE_ID')
    require(names == expected['assignmentNames']['value'], 'ASSIGNMENT_NAMES')
    require(p == parameters(s), 'PARAMETER_BYTES_CONTRACT')

def expression(name):
    return "[parameters('" + name + "')]"

def check_definitions(defs, root):
    names = PARAMS if root else PARAMS - {'resourceGroupName'}
    keys(defs, names, 'TEMPLATE_PARAMETER_SET')
    for name, definition in defs.items():
        d = copy.deepcopy(definition)
        d.pop('metadata', None)
        expected = {'type':'array' if name in ('presenterIds','assignmentNames') else 'string'}
        if name=='location': expected['allowedValues']=['swedencentral']
        if name in ('presenterIds','assignmentNames'):
            n = 2 if name=='presenterIds' else 3
            expected.update(minLength=n, maxLength=n)
        require(d==expected, 'TEMPLATE_PARAMETER_DEFAULT_OR_TYPE')

def check_header(t, root):
    keys(t, {'$schema','contentVersion','metadata','parameters','resources'}, 'TEMPLATE_CLOSED')
    schema = ('https://schema.management.azure.com/schemas/2018-05-01/subscriptionDeploymentTemplate.json#' if root else 'https://schema.management.azure.com/schemas/2019-04-01/deploymentTemplate.json#')
    require(t['$schema']==schema, 'TEMPLATE_SCOPE')
    require(t['contentVersion']=='1.0.0.0', 'TEMPLATE_VERSION')
    require(t['metadata']['_generator']['version'].startswith('0.47.16.'), 'COMPILER_PIN')
    check_definitions(t['parameters'], root)

def check_template(t):
    check_header(t, True)
    resources=t['resources']
    require(isinstance(resources, list) and len(resources)==2, 'ROOT_OUTSIDE_FIVE')
    rg, sb = resources
    keys(rg, {'type','apiVersion','name','location','properties'}, 'RG_WRAPPER_SCOPE')
    require({k:rg[k] for k in ('type','apiVersion','name','location')} == dict(type='Microsoft.Resources/deployments',apiVersion='2025-04-01',name='aca-online-rg',location='[deployment().location]'), 'RG_WRAPPER_SCOPE')
    keys(rg['properties'], {'expressionEvaluationOptions','mode','parameters','template'}, 'RG_WRAPPER_CLOSED')
    rp=rg['properties']
    require(rp['mode']=='Incremental' and rp['expressionEvaluationOptions']=={'scope':'inner'}, 'DEPLOYMENT_MODE')
    require(rp['parameters']=={'name':{'value':expression('resourceGroupName')},'location':{'value':expression('location')},'enableTelemetry':{'value':False}}, 'AVM_EXTRA_DEPENDENCIES')
    # Hash of the actual published, pinned AVM embedded template, including dormant
    # branches. Null lock/roles defaults + false telemetry guarantee one active leaf.
    require(digest(rp['template'])==json.loads((ROOT/'toolchain.json').read_text())['rgTemplateSha256'], 'AVM_PUBLISHED_TEMPLATE')
    keys(sb, {'type','apiVersion','name','resourceGroup','properties','dependsOn'}, 'SANDBOX_WRAPPER_SCOPE')
    require({k:sb[k] for k in ('type','apiVersion','name','resourceGroup','dependsOn')} == dict(type='Microsoft.Resources/deployments',apiVersion='2025-04-01',name='aca-online-sandbox',resourceGroup=expression('resourceGroupName'),dependsOn=["[subscriptionResourceId('Microsoft.Resources/deployments', 'aca-online-rg')]" ]), 'SANDBOX_WRAPPER_SCOPE')
    sp=sb['properties']; keys(sp, {'expressionEvaluationOptions','mode','parameters','template'}, 'SANDBOX_WRAPPER_CLOSED')
    require(sp['mode']=='Incremental' and sp['expressionEvaluationOptions']=={'scope':'inner'}, 'DEPLOYMENT_MODE')
    require(sp['parameters']=={k:{'value':expression(k)} for k in PARAMS-{'resourceGroupName'}}, 'CHILD_SCOPE_PARAMETERS')
    check_header(sp['template'], False)
    leaves=sp['template']['resources']
    require(isinstance(leaves,list) and len(leaves)==4, 'CHILD_OUTSIDE_FIVE')
    group=leaves[0]
    require(group.get('type')=='Microsoft.App/sandboxGroups' and group.get('apiVersion')=='2026-07-01', 'SANDBOX_TYPE')
    require(group==dict(type='Microsoft.App/sandboxGroups',apiVersion='2026-07-01',name=expression('sandboxGroupName'),location=expression('location'),properties={}), 'SANDBOX_PROPERTIES_OR_IDENTITY')
    group_scope="[resourceId('Microsoft.App/sandboxGroups', parameters('sandboxGroupName'))]"
    for i, role in enumerate(leaves[1:]):
        require(role.get('type')=='Microsoft.Authorization/roleAssignments' and role.get('apiVersion')=='2022-04-01', 'GRANT_TYPE')
        require(role.get('name')==f"[parameters('assignmentNames')[{i}]]", 'ASSIGNMENT_ID_EXPRESSION')
        props=role.get('properties', {})
        role_guid=DATA_OWNER if i<2 else CONTRIBUTOR
        require(props.get('roleDefinitionId')==f"[subscriptionResourceId('Microsoft.Authorization/roleDefinitions', '{role_guid}')]", 'UNAPPROVED_ROLE')
        principal=f"[parameters('presenterIds')[{i}]]" if i<2 else expression('consumerPrincipalId')
        require(props.get('principalId')==principal and props.get('principalType')==('User' if i<2 else 'ServicePrincipal'), 'UNAPPROVED_PRINCIPAL')
        require(role.get('scope')==(group_scope if i<2 else None), 'GRANT_SCOPE')
        expected=dict(type='Microsoft.Authorization/roleAssignments',apiVersion='2022-04-01',name=f"[parameters('assignmentNames')[{i}]]",properties=dict(roleDefinitionId=props['roleDefinitionId'],principalId=principal,principalType=props['principalType']))
        if i<2: expected.update(scope=group_scope,dependsOn=[group_scope])
        require(role==expected, 'GRANT_EXTRA_FIELDS')
    return True

def check_baseline(raw, s):
    expected={o['id'].lower() for o in graph(s)}
    require(set(raw)==expected, 'BASELINE_FIVE_IDS')
    require(all(v['status']==404 and v['errorCode'] in ('ResourceNotFound','ResourceGroupNotFound','RoleAssignmentNotFound') for v in raw.values()), 'BASELINE_NOT_ABSENT')

def check_whatif(raw, s):
    require(raw.get('status')=='Succeeded', 'WHATIF_FAILED_STATUS')
    require(isinstance(raw.get('changes'),list), 'WHATIF_FULL_RAW_REQUIRED')
    expected={o['id'].lower():o for o in graph(s)}
    seen=set()
    # No allowlist for extra deployments: ARM full what-if must expose exactly the
    # five durable leaves. Unsupported/opaque nested expansions require review.
    for change in raw['changes']:
        rid=change.get('resourceId','').lower()
        require(rid in expected, 'WHATIF_OUTSIDE_FIVE')
        require(rid not in seen, 'WHATIF_DUPLICATE_ID'); seen.add(rid)
        require(change.get('changeType')=='Create', 'WHATIF_NOT_CREATE')
        require(change.get('before') in (None,{}), 'WHATIF_EXISTING_RESOURCE')
        after=change.get('after'); require(isinstance(after,dict), 'WHATIF_PAYLOAD_REQUIRED')
        obj=expected[rid]
        require(after.get('type','').lower()==obj['type'].lower(), 'WHATIF_TYPE')
        require(after.get('apiVersion',obj['apiVersion'])==obj['apiVersion'], 'WHATIF_API')
        if 'location' in obj: require(after.get('location')==obj['location'], 'WHATIF_LOCATION')
        require(after.get('properties')==obj['properties'], 'WHATIF_PROPERTIES')
        require(not(set(after)-{'id','name','type','apiVersion','location','properties'}), 'WHATIF_EXTRA_FIELDS')
    require(seen==set(expected), 'WHATIF_MISSING_LEAF')
