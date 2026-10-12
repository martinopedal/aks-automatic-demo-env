"""Protected read-only native ARM qualification. Never provisions or applies.
Full HTTP evidence is retained only on an existing private persistent filesystem.
Authentication tokens exist only in memory; public console output is generic.
"""
import base64
from datetime import datetime, timezone
import hashlib
import json
import os
from pathlib import Path
import shutil
import sys
import time
import urllib.error
import urllib.parse
import urllib.request
import uuid
from guard import (ROOT, GateError, check_template, check_parameters, check_baseline,
                   check_whatif, graph, parameters, require, validate_scope)

ARM='https://management.azure.com'
API='2025-04-01'

def write(path, data):
    # Exclusive files; previously captured raw evidence is never replaced.
    with path.open('xb') as f:
        f.write(data if isinstance(data,bytes) else json.dumps(data,indent=2).encode())
    path.chmod(0o600)

def jwt_payload(token):
    body=token.split('.')[1]
    return json.loads(base64.urlsafe_b64decode(body+'='*((4-len(body)%4)%4)))

def check_claims(c, s, env, now=None):
    now=time.time() if now is None else now
    require(c.get('iss')==s['oidc']['issuer'] and c.get('aud')==s['oidc']['audience'], 'ISSUED_OIDC_ISSUER_AUDIENCE')
    require(c.get('sub')==s['oidc']['subject'], 'ISSUED_OIDC_SUBJECT')
    require(c.get('repository')==s['source']['repository'], 'ISSUED_OIDC_REPOSITORY')
    require(c.get('environment')=='online' and c.get('ref')=='refs/heads/main' and env.get('GITHUB_REF_PROTECTED')=='true', 'ISSUED_OIDC_PROTECTED_ENVIRONMENT')
    if 'ref_protected' in c:
        require(c['ref_protected'] in ('true',True), 'ISSUED_OIDC_PROTECTED_ENVIRONMENT')
    for claim, name in [('sha','GITHUB_SHA'),('run_id','GITHUB_RUN_ID'),('run_attempt','GITHUB_RUN_ATTEMPT')]:
        require(str(c.get(claim))==env[name], 'ISSUED_OIDC_CURRENT_RUN')
    require(c.get('workflow_ref')==s['source']['repository']+'/.github/workflows/aca-sandboxes-online.yml@refs/heads/main', 'ISSUED_OIDC_WORKFLOW')
    require(c.get('job_workflow_ref',c.get('workflow_ref'))==c['workflow_ref'], 'ISSUED_OIDC_JOB_WORKFLOW')
    require(isinstance(c.get('exp'),int) and isinstance(c.get('iat'),int) and c['iat']<=now+30<c['exp'] and now-c['iat']<=300, 'ISSUED_OIDC_FRESHNESS')
    # Azure token exchange verifies the JWT signature. No locally forged claims
    # or human-approval flags are used as authentication/authorization evidence.

def check_receipt(raw, scope):
    require(hashlib.sha256(raw.encode()).hexdigest()==scope['scopeApprovalSha256'], 'SCOPE_RECEIPT_BYTES')
    receipt=json.loads(raw)
    require(receipt.get('response',{}).get('approve_isolated_setup') is True, 'SCOPE_NOT_APPROVED')
    require(receipt.get('approvedDurableCount')==5, 'SCOPE_RECEIPT_FIVE')
    require(receipt.get('resourceGroup')==scope['target']['resourceGroupName'] and receipt.get('sandboxGroup')==scope['target']['sandboxGroupName'], 'SCOPE_RECEIPT_TARGET')
    require(receipt.get('recipe')=='Native Bicep for this isolated Sandbox package only', 'SCOPE_RECEIPT_RECIPE')
    # Original user scope receipt, not fabricated apply consent. Principal/target
    # contract is independently frozen by the private scope owner before this job.
    return receipt

def private_directory(env):
    value=env.get('ACA_ONLINE_EVIDENCE_ROOT','')
    require(bool(value), 'PRIVATE_PERSISTENT_EVIDENCE_ROOT_REQUIRED')
    raw=Path(value)
    require(raw.is_absolute() and raw.is_dir() and not raw.is_symlink(), 'EXISTING_PRIVATE_EVIDENCE_ROOT')
    root=raw.resolve(); workspace=Path(env['GITHUB_WORKSPACE']).resolve()
    require(root!=workspace and workspace not in root.parents, 'EVIDENCE_OUTSIDE_PUBLIC_CHECKOUT')
    require(os.name=='posix' and root.stat().st_uid==os.getuid() and root.stat().st_mode&0o077==0, 'PRIVATE_EVIDENCE_PERMISSIONS')
    require(not any(p in ('tmp','temp') for p in root.parts), 'PERSISTENT_NOT_TEMP_EVIDENCE')
    directory=root/('aca-online-'+env['GITHUB_RUN_ID']+'-'+env['GITHUB_RUN_ATTEMPT']+'-'+uuid.uuid4().hex)
    directory.mkdir(mode=0o700)
    return directory

class AzureReadOnly:
    def __init__(self, token, directory):
        self.token=token; self.directory=directory; self.counter=0
    def request(self, label, method, url, body=None):
        require(url.startswith(ARM+'/'), 'ARM_HOST_ONLY')
        data=json.dumps(body).encode() if body is not None else None
        request_id=str(uuid.uuid4())
        req=urllib.request.Request(url,data=data,method=method,
            headers={'Authorization':'Bearer '+self.token,'Content-Type':'application/json',
                     'x-ms-client-request-id':request_id})
        started=datetime.now(timezone.utc).isoformat()
        try:
            response=urllib.request.urlopen(req,timeout=60)
        except urllib.error.HTTPError as error: response=error
        except Exception as error:
            self.counter+=1
            write(self.directory/f'{self.counter:03d}-{label}-transport.json',dict(method=method,url=url,startedUTC=started,requestId=request_id,error=type(error).__name__))
            raise GateError('ARM_TRANSPORT_FAILED') from None
        data=response.read(); headers=dict(response.headers.items()); status=response.status
        self.counter+=1
        # Raw bytes and complete response headers are captured BEFORE interpreting status/body.
        write(self.directory/f'{self.counter:03d}-{label}.raw',data)
        record=dict(method=method,url=url,status=status,headers=headers,requestId=request_id,
                    startedUTC=started,completedUTC=datetime.now(timezone.utc).isoformat(),rawSha256=hashlib.sha256(data).hexdigest())
        write(self.directory/f'{self.counter:03d}-{label}.http.json',record)
        try: value=json.loads(data)
        except Exception: value=None
        return status,{k.lower():v for k,v in headers.items()},value
    def operation(self, label, url, body):
        status,headers,value=self.request(label,'POST',url,body)
        if status not in (200,201,202): return False,value
        deadline=time.monotonic()+300
        while status==202 or (isinstance(value,dict) and value.get('status') in ('Accepted','Running','InProgress')):
            poll=headers.get('azure-asyncoperation') or headers.get('location')
            require(bool(poll) and poll.startswith(ARM+'/'), 'NATIVE_ASYNC_POLL_URL')
            require(time.monotonic()<deadline, 'NATIVE_ASYNC_DEADLINE')
            try: delay=min(30,max(1,int(headers.get('retry-after','2'))))
            except ValueError: delay=2
            time.sleep(delay)
            old_poll=poll
            status,new_headers,value=self.request(label+'-poll','GET',poll)
            headers={**headers,**new_headers,'azure-asyncoperation':old_poll}
            if status not in (200,201,202): return False,value
        return isinstance(value,dict) and value.get('status','Succeeded')=='Succeeded' and 'error' not in value,value

def exchange(s, env, directory):
    request_url=env.get('ACTIONS_ID_TOKEN_REQUEST_URL','')
    host=urllib.parse.urlparse(request_url).hostname or ''
    require(host.endswith('.actions.githubusercontent.com'), 'GITHUB_OIDC_REQUEST_HOST')
    url=request_url+'&audience='+urllib.parse.quote(s['oidc']['audience'])
    req=urllib.request.Request(url,headers={'Authorization':'Bearer '+env['ACTIONS_ID_TOKEN_REQUEST_TOKEN']})
    with urllib.request.urlopen(req,timeout=60) as response: token=json.load(response)['value']
    claims=jwt_payload(token); write(directory/'issued-claims.private.json',claims)
    check_claims(claims,s,env)
    body=urllib.parse.urlencode(dict(client_id=s['consumer']['clientId'],scope=ARM+'/.default',
        grant_type='client_credentials',client_assertion_type='urn:ietf:params:oauth:client-assertion-type:jwt-bearer',client_assertion=token)).encode()
    req=urllib.request.Request('https://login.microsoftonline.com/'+s['target']['tenantId']+'/oauth2/v2.0/token',data=body)
    try:
        with urllib.request.urlopen(req,timeout=60) as response: oauth=json.load(response)
    except urllib.error.HTTPError as error:
        # Never persist a JWT, assertion, OAuth access/refresh token or auth header.
        try: value=json.load(error)
        except Exception: value={}
        write(directory/'exchange-failure.private.json', {k:value[k] for k in ('error','error_codes','timestamp','trace_id','correlation_id') if k in value})
        raise GateError('EXISTING_FIC_EXCHANGE_FAILED') from None
    arm_token=oauth['access_token']; arm_claims=jwt_payload(arm_token)
    require(arm_claims.get('tid')==s['target']['tenantId'] and arm_claims.get('oid')==s['consumer']['principalId'], 'EXCHANGED_ACTOR_TENANT')
    require(arm_claims.get('appid',arm_claims.get('azp'))==s['consumer']['clientId'], 'EXCHANGED_ACTOR_CLIENT')
    write(directory/'exchange-binding.private.json',dict(tenantId=arm_claims['tid'],principalId=arm_claims['oid'],
        clientId=s['consumer']['clientId'],expires=arm_claims['exp']))
    return arm_token

def seal(directory, outcome, reason):
    files={str(p.relative_to(directory)):hashlib.sha256(p.read_bytes()).hexdigest()
           for p in sorted(directory.rglob('*')) if p.is_file()}
    manifest=dict(schemaVersion=1,outcome=outcome,reason=reason,completedUTC=datetime.now(timezone.utc).isoformat(),files=files)
    write(directory/'seal.json',manifest)
    seal_hash=hashlib.sha256((directory/'seal.json').read_bytes()).hexdigest()
    # Application-level immutable checked bundle. Persistent storage retention/
    # write-once ACLs and independent hash custody are mandatory operator gates;
    # chmod alone is not advertised as tamper-proof/WORM storage.
    for p in directory.rglob('*'):
        if p.is_file(): p.chmod(0o400)
    return seal_hash

def main(env=None):
    env=dict(os.environ) if env is None else env
    directory=None; outcome='FAILED_READONLY'; reason='UNEXPECTED_PRIVATE_DIAGNOSTIC'
    try:
        directory=private_directory(env)
        bindings={k:env[k] for k in ('GITHUB_REPOSITORY','GITHUB_SHA','GITHUB_REF','GITHUB_RUN_ID','GITHUB_RUN_ATTEMPT')}
        write(directory/'run-bindings.private.json',bindings)
        scope=json.loads(env['ACA_ONLINE_SCOPE_JSON']); validate_scope(scope)
        receipt_raw=env['ACA_ONLINE_SCOPE_APPROVAL_JSON']; check_receipt(receipt_raw,scope)
        write(directory/'scope-approval.private.json',receipt_raw.encode())
        require(scope['source']['commit']==env['GITHUB_SHA'] and scope['source']['repository']==env['GITHUB_REPOSITORY'], 'FROZEN_SOURCE_CURRENT_RUN')
        require(env['GITHUB_REF']=='refs/heads/main', 'MAIN_ONLY_PROTECTED_QUALIFICATION')
        now=datetime.now(timezone.utc)
        timestamp=datetime.fromisoformat(scope['qualificationUTC'])
        require(timestamp.tzinfo is not None and 0<=(now-timestamp).total_seconds()<=900, 'FRESH_PRIVATE_TARGET_CONTRACT_REQUIRED')
        p=json.loads(env['ACA_ONLINE_PARAMETERS_JSON']); check_parameters(p,scope)
        # Copy the checked compiled source bytes. Never rebuild during native qualification.
        template_bytes=(ROOT/'main.json').read_bytes(); template=json.loads(template_bytes); check_template(template)
        src=directory/'source'; src.mkdir(mode=0o700)
        for file in sorted(ROOT.rglob('*')):
            if file.is_file() and not any(part in ('.build','.private','__pycache__') for part in file.relative_to(ROOT).parts):
                dest=src/file.relative_to(ROOT); dest.parent.mkdir(parents=True,exist_ok=True); write(dest,file.read_bytes())
        write(directory/'scope.private.json',scope); write(directory/'parameters.private.json',p)
        write(directory/'template.json',template_bytes); write(directory/'expected-five.private.json',graph(scope))
        api=AzureReadOnly(exchange(scope,env,directory),directory)
        baseline={}
        for i,obj in enumerate(graph(scope)):
            status,headers,value=api.request('baseline-'+str(i),'GET',ARM+obj['id']+'?api-version='+obj['apiVersion'])
            code=value.get('error',{}).get('code') if isinstance(value,dict) else None
            baseline[obj['id'].lower()]=dict(status=status,errorCode=code)
        write(directory/'baseline.private.json',baseline)
        baseline_ok=True
        try: check_baseline(baseline,scope)
        except GateError: baseline_ok=False
        # Both real native operations are attempted even when validation returns a
        # denial, so failure bundles contain full validation AND raw what-if evidence.
        deployment='aca-online-qualify-'+env['GITHUB_RUN_ID']+'-'+env['GITHUB_RUN_ATTEMPT']
        url=ARM+'/subscriptions/'+scope['target']['subscriptionId']+'/providers/Microsoft.Resources/deployments/'+deployment
        body=dict(location=scope['target']['region'],properties=dict(mode='Incremental',template=template,parameters=p['parameters'],validationLevel='Provider'))
        validate_ok,validation=api.operation('validate',url+'/validate?api-version='+API,body)
        whatif_body=json.loads(json.dumps(body)); whatif_body['properties']['whatIfSettings']={'resultFormat':'FullResourcePayloads'}
        whatif_ok,raw=api.operation('whatif',url+'/whatIf?api-version='+API,whatif_body)
        write(directory/'raw-whatif.private.json',raw)
        write(directory/'native-results.private.json',dict(baselineAccepted=baseline_ok,validateAccepted=validate_ok,whatifAccepted=whatif_ok))
        require(baseline_ok,'LIVE_BASELINE_NOT_APPROVED_ABSENCE')
        require(validate_ok,'NATIVE_VALIDATE_FAILED')
        require(whatif_ok,'NATIVE_WHATIF_FAILED')
        check_whatif(raw,scope)
        outcome='QUALIFIED_READONLY'; reason='EXACT_FIVE_CREATES_NO_APPLY_AUTHORITY'
    except GateError as error: reason=str(error)
    except Exception as error:
        # Details are private; exceptions/stack traces may contain URLs/IDs.
        if directory is not None:
            import traceback
            write(directory/'failure.private.json',dict(type=type(error).__name__,traceback=traceback.format_exc()))
    finally:
        if directory is not None:
            seal_hash=seal(directory,outcome,reason)
            print('Native read-only bundle sealed SHA256='+seal_hash+'; outcome='+outcome+'; gate='+reason)
        else: print('Native qualification not started: existing private persistent evidence storage is required.')
    return 0 if outcome=='QUALIFIED_READONLY' else 1

if __name__=='__main__': sys.exit(main())
