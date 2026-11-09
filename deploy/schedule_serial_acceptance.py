"""Run in Hermes: exercise a real no-model schedule against a held chat slot."""
import json
import time
from datetime import datetime,timezone,timedelta
from pathlib import Path
from cron.jobs import create_job,get_job,list_jobs,update_job,rearm_oneshot
from hermes_cli.active_sessions import try_acquire_active_session
from hermes_cli.config import load_config

root=Path('/workspace/acceptance-serial-native'); root.mkdir(exist_ok=True)
name='deployment-serial-acceptance-20261031'
existing=[j for j in list_jobs(include_disabled=True) if j.get('name')==name]
if existing and existing[-1].get('last_status')=='ok' and (root/'verification.json').exists():
    assert (root/'result.txt').read_text()=='SCHEDULE-SERIAL-PASS'
    print('PASS: prior real scheduler serialization result verified; not repeated')
    raise SystemExit(0)
if existing:
    assert existing[-1].get('state')=='completed' and str(existing[-1].get('last_error')).startswith('Blocked: script path resolves outside the scripts directory'), 'Inspect this failure before retrying'
    assert not (root/'result.txt').exists(),'Do not repeat completed side effects'
for filename in ('release','waiting-confirmed'):
    (root/filename).unlink(missing_ok=True)
script=Path('/opt/data/scripts/acceptance_serial.py'); script.parent.mkdir(exist_ok=True)
script.write_text('from pathlib import Path\nPath("/workspace/acceptance-serial-native/result.txt").write_text("SCHEDULE-SERIAL-PASS")\nprint("SCHEDULE-SERIAL-PASS")\n')
lease,refusal=try_acquire_active_session(session_id='acceptance-held-chat',surface='gateway:discord',config=load_config())
assert lease is not None,refusal
log=Path('/opt/data/logs/agent.log'); offset=log.stat().st_size
try:
    if existing:
        job=update_job(existing[-1]['id'],{'script':str(script)})
        job=rearm_oneshot(job['id'],(datetime.now(timezone.utc)+timedelta(seconds=30)).isoformat())
    else:
        job=create_job(prompt=None,schedule='in 1m',name=name,repeat=1,deliver='local',
                       script=str(script),workdir=str(root),no_agent=True,
                       interpreter='/opt/hermes/.venv/bin/python')
    assert job.get('no_agent') is True
    print('Created one no-model schedule '+job['id'],flush=True)
    deadline=time.monotonic()+150
    needle=('Scheduled job '+job['id']+' waiting for the shared execution slot').encode()
    while True:
        assert not (root/'result.txt').exists(),'Scheduled script overlapped the held slot'
        with log.open('rb') as source:
            source.seek(offset); found=needle in source.read()
        if found: break
        assert time.monotonic()<deadline,'No native worker wait evidence'
        time.sleep(1)
    (root/'waiting-confirmed').write_text(job['id'])
    print('PASS: actual scheduled worker is waiting; no script/model work started',flush=True)
    deadline=time.monotonic()+120
    while not (root/'release').exists() and time.monotonic()<deadline: time.sleep(.5)
    lease.release(); lease=None
    deadline=time.monotonic()+90
    while True:
        state=get_job(job['id'])
        if (root/'result.txt').exists() and state.get('last_status')=='ok': break
        assert time.monotonic()<deadline,'Scheduled worker did not finish after release'
        time.sleep(1)
    assert (root/'result.txt').read_text()=='SCHEDULE-SERIAL-PASS'
    (root/'verification.json').write_text(json.dumps({'job_id':job['id'],'wait_observed':True,
        'executed_after_release':True,'last_status':'ok','no_agent':True}))
    print('PASS: actual scheduled worker ran once after release; last_status=ok',flush=True)
finally:
    if lease: lease.release()
