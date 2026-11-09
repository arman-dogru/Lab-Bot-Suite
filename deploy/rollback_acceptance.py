"""Root-only controlled image replacement/rollback; always restore the official pin.

Candidate differs only by a label on the tested official base. This exercises
container replacement and state preservation, not future-version migrations.
"""
import hashlib
import json
import os
import subprocess
import time
from pathlib import Path


def run(*args): return subprocess.check_output(args).decode().strip()
config=Path('/etc/jetson-bot/deployment.env')
original=config.read_bytes()
previous=Path('/etc/jetson-bot/deployment.previous.env')
previous.write_bytes(original); os.chmod(str(previous),0o600)
reference=next(line.split('=',1)[1] for line in original.decode().splitlines() if line.startswith('HERMES_IMAGE='))
base=run('docker','image','inspect',reference,'--format','{{.Id}}')
candidate=run('docker','image','inspect','jetson-bot-rollback-check:20261031','--format','{{.Id}}')
assert candidate!=base
artifact=Path('/srv/jetson-bot/workspace/acceptance-budget-resume/result.txt')
before=hashlib.sha256(artifact.read_bytes()).hexdigest()
log=Path('/srv/jetson-bot/state/logs/gateway.log')


def activate(data,expected):
    run('systemctl','stop','jetson-bot','jetson-budget')
    config.write_bytes(data); os.chmod(str(config),0o600)
    offset=log.stat().st_size
    run('systemctl','start','jetson-budget','jetson-bot')
    deadline=time.monotonic()+180
    while time.monotonic()<deadline:
        if log.stat().st_size<offset: offset=0
        with log.open() as stream:
            stream.seek(offset); text=stream.read()
        if 'discord connected' in text:
            assert run('docker','inspect','jetson-hermes','--format','{{.Image}}')==expected
            assert hashlib.sha256(artifact.read_bytes()).hexdigest()==before
            return
        time.sleep(2)
    raise RuntimeError('Replacement did not reconnect before deadline')


try:
    activate(('HERMES_IMAGE='+candidate+'\n').encode(),candidate)
    print('PASS: replacement image reconnected with persistent state intact',flush=True)
finally:
    activate(original,base)
    print('PASS: official pinned image restored and reconnected',flush=True)
proof={'replacement_image':candidate,'restored_image':base,'state_preserved':True,
       'test_scope':'distinct image with same official base; no version migration',
       'completed_utc':time.strftime('%Y-%m-%dT%H:%M:%SZ',time.gmtime())}
Path('/var/lib/jetson-bot/rollback-test.json').write_text(json.dumps(proof,indent=2)+'\n')
