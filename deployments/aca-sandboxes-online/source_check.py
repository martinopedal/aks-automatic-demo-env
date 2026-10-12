"""No Azure login, tokens, provider import, or schema suppression."""
import hashlib
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys
import urllib.request
from guard import check_template, require

ROOT=Path(__file__).resolve().parent

def compiler():
    tool=shutil.which('bicep')
    if tool is None:
        # Only after a genuine missing-command check; job-local install, no PATH/config mutation.
        try: subprocess.run(['bicep','--version'],check=True,capture_output=True)
        except FileNotFoundError: pass
        else: raise RuntimeError('COMPILER_DISCOVERY_INCONSISTENT')
        require(sys.platform=='linux', 'INSTALL_PINNED_BICEP_MANUALLY_FOR_THIS_OS')
        config=json.loads((ROOT/'toolchain.json').read_text())
        dest=ROOT/'.build/bicep'; dest.parent.mkdir(exist_ok=True)
        url='https://github.com/Azure/bicep/releases/download/v'+config['bicep']+'/bicep-linux-x64'
        data=urllib.request.urlopen(url,timeout=120).read()
        require(hashlib.sha256(data).hexdigest()==config['linuxX64Sha256'], 'BICEP_BINARY_DIGEST')
        dest.write_bytes(data); dest.chmod(0o700); tool=str(dest)
    version=subprocess.run([tool,'--version'],capture_output=True,text=True,check=True).stdout
    require('version 0.47.16 ' in version, 'BICEP_VERSION_PIN')
    return tool

def compile_source(tool, directory=ROOT):
    result=subprocess.run([tool,'build',str(directory/'main.bicep'),'--stdout'],capture_output=True,text=True)
    # All diagnostics, including BCP081, are exposed, never hidden/schema skipped.
    print(result.stderr, file=sys.stderr, end='')
    require(result.returncode==0, 'NATIVE_COMPILE_FAILED')
    return json.loads(result.stdout), result.stdout

def main():
    tool=compiler()
    result=subprocess.run([tool,'lint',str(ROOT/'main.bicep')],capture_output=True,text=True)
    print(result.stdout,end=''); print(result.stderr,file=sys.stderr,end='')
    require(result.returncode==0, 'NATIVE_LINT_FAILED')
    template,text=compile_source(tool); check_template(template)
    require(template==json.loads((ROOT/'main.json').read_text()), 'CHECKED_IN_ARM_NOT_CURRENT')
    (ROOT/'.build').mkdir(exist_ok=True)
    (ROOT/'.build/template.json').write_text(text,encoding='utf-8')
    tests=subprocess.run([sys.executable,'-m','unittest','discover','-s',str(ROOT/'tests'),'-v'],cwd=ROOT,
                         env={**os.environ,'ACA_BICEP':tool})
    require(tests.returncode==0, 'OFFLINE_TESTS_FAILED')
    print('Offline native lint, exact compiled ARM, and negative contracts passed; no Azure authentication.')

if __name__=='__main__': main()
