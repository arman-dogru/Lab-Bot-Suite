#!/usr/bin/python3
"""Restore critical files privately in one archive pass; never print credentials."""
import hashlib
import json
import os
import sqlite3
import subprocess
import tarfile
import tempfile
from pathlib import Path

archive=sorted(Path('/var/backups/jetson-bot').glob('*.tar.gz'))[-1]
assert archive.stat().st_mode & 0o077==0
deployed=Path('/var/lib/project-hub/data/hub.sqlite').exists()
hub_base='var/lib/project-hub' if deployed else 'workspace/project-hub'
hub_live=Path('/var/lib/project-hub') if deployed else Path('/srv/jetson-bot/workspace/project-hub')
required={'state/config.yaml':'config.yaml','ledger.sqlite':'ledger.sqlite',
          'state/discord-watch/state.json':'discord-watch.json',
          'state/discord-channels.json':'discord-channels.json',
          'opt/jetson-bot/jetson_bot/budget.py':'budget.py',
          'jetson-bot/systemd-backup/jetson-bot.service':'bot.service'}
private={
    'jetson-bot/xai-key':Path('/etc/jetson-bot/xai-key'),
    'jetson-bot/local-token':Path('/etc/jetson-bot/local-token'),
    'state/runtime.env':Path('/srv/jetson-bot/state/runtime.env'),
    'state/.env':Path('/srv/jetson-bot/state/.env'),
    'state/home/.ssh/jetson-host':Path('/srv/jetson-bot/state/home/.ssh/jetson-host')}
if Path('/srv/jetson-bot/state/jetson-recovery.sqlite').exists():
    required['state/jetson-recovery.sqlite']='recovery.sqlite'
recovery_code={
    'state/plugins/jetson-controls/'+name:Path('/srv/jetson-bot/state/plugins/jetson-controls')/name
    for name in ('recovery_controller.py','recovery_store.py','restart_recovery.py','workload.py','plugin.yaml')}
for name in recovery_code:
    required[name]='recovery-code/'+Path(name).name
if (hub_live/'data/hub.sqlite').exists():
    required[hub_base+'/data/hub.sqlite']='site/data/hub.sqlite'
    required['opt/project-hub/app.py']='site/app.py'
    required['jetson-bot/systemd-backup/jetson-project-hub.service']='hub.service'
if (hub_live/'secrets/webhook.token').exists():
    private[hub_base+'/secrets/webhook.token']=hub_live/'secrets/webhook.token'
optional={hub_base+'/data/hub.sqlite'+suffix:'site/data/hub.sqlite'+suffix for suffix in ('-wal','-shm')}
for index,name in enumerate(private): required[name]='private/'+str(index)

with tempfile.TemporaryDirectory(prefix='jetson-restore-check-') as folder:
    root=Path(folder); found=set(); uploads={}
    with tarfile.open(str(archive),'r|gz') as source:
        for member in source:
            name=member.name
            upload_prefix=hub_base+'/data/uploads/'
            if name.startswith(upload_prefix) and member.isfile():
                stored=name[len(upload_prefix):]
                assert stored and Path(stored).name==stored
                target=root/'site/data/uploads'/stored
                target.parent.mkdir(parents=True,exist_ok=True)
                digest=hashlib.sha256()
                with source.extractfile(member) as reader,target.open('wb') as writer:
                    for chunk in iter(lambda:reader.read(65536),b''):
                        digest.update(chunk); writer.write(chunk)
                os.chmod(str(target),0o600); uploads[stored]=digest.hexdigest()
                continue
            target_name=required.get(name) or optional.get(name)
            if not target_name: continue
            assert member.isfile(),name
            data=source.extractfile(member).read()
            if name in private:
                assert member.mode & 0o077==0 and data.strip()
                assert hashlib.sha256(data).digest()==hashlib.sha256(private[name].read_bytes()).digest(), 'Private restore differs from the current fresh snapshot'
            if name in recovery_code:
                assert data==recovery_code[name].read_bytes(),'Recovery code differs from the current fresh snapshot'
            target=root/target_name
            target.parent.mkdir(parents=True,exist_ok=True)
            target.write_bytes(data); os.chmod(str(target),0o600); found.add(name)
    assert set(required)<=found,'Missing required backup files: '+str(sorted(set(required)-found))
    for name in ('discord-watch.json','discord-channels.json'):
        json.loads((root/name).read_text())
    for name,query in [('ledger.sqlite','SELECT COUNT(*) FROM requests'),
                       ('recovery.sqlite','SELECT COUNT(*) FROM pending')]:
        if not (root/name).exists(): continue
        db=sqlite3.connect(str(root/name))
        try:
            assert db.execute('PRAGMA integrity_check').fetchone()[0]=='ok'
            db.execute(query).fetchone()
        finally: db.close()
    if (root/'site/data/hub.sqlite').exists():
        db=sqlite3.connect(str(root/'site/data/hub.sqlite'))
        try:
            assert db.execute('PRAGMA integrity_check').fetchone()[0]=='ok'
            db.execute('SELECT COUNT(*) FROM projects').fetchone()
            for stored,digest in db.execute('SELECT stored_name,sha256 FROM documents'):
                assert uploads.get(stored)==digest,'Restored upload hash differs'
        finally: db.close()
    subprocess.run(['docker','run','--rm','--network','none','-v',str(root)+':/restore:ro',
        '--entrypoint','/opt/hermes/.venv/bin/python',
        'nousresearch/hermes-agent@sha256:41f1cfdd2486b38ca55453a28785910b498ee184e71eed4f2d7ee3f908aa7236',
        '-c','import hermes_yaml as yaml; from pathlib import Path; '
        'c=yaml.safe_load(Path("/restore/config.yaml").read_text()); '
        'assert c["model"]["provider"]=="custom:jetson-budget"; '
        '[(compile(p.read_text(),str(p),"exec")) for p in '
        '[Path("/restore/budget.py"),Path("/restore/site/app.py"),*Path("/restore/recovery-code").glob("*.py")] if p.exists()]'],check=True)
print('PASS: isolated restoration of configuration, matching private credentials, service definitions, recovery queue, budget ledger, app code/database and uploaded bytes from '+archive.name)
