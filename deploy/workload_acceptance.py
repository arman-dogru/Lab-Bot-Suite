"""No-model cross-process check against Hermes's real capacity registry."""
import subprocess
import sys
import tempfile
import time
from pathlib import Path
from hermes_cli.active_sessions import try_acquire_active_session
from control_plugin.workload import run_serial


def acquire(root,session,surface):
    return try_acquire_active_session(session_id=session,surface=surface,
        config={'max_concurrent_sessions':1},registry_home=root)


if len(sys.argv)>1:
    root=Path(sys.argv[1]); (root/'ready').touch()
    def run(job,**kwargs): (root/'ran').write_text('SERIAL-EXECUTION-PASS')
    run_serial({'id':'fixture'},run,lambda job:acquire(root,'probe-cron','cron'),lambda job:None)
else:
    with tempfile.TemporaryDirectory(prefix='jetson-workload-') as folder:
        root=Path(folder)
        lease,refusal=acquire(root,'probe-chat','gateway:discord')
        assert lease is not None,refusal
        child=subprocess.Popen([sys.executable,__file__,folder])
        try:
            deadline=time.monotonic()+30
            while not (root/'ready').exists():
                assert child.poll() is None,'Worker exited early'
                assert time.monotonic()<deadline,'Worker startup timed out'
                time.sleep(.1)
            time.sleep(3)
            assert not (root/'ran').exists(),'Scheduled work overlapped the held chat slot'
            lease.release(); lease=None
            assert child.wait(timeout=30)==0
            assert (root/'ran').read_text()=='SERIAL-EXECUTION-PASS'
            print('PASS: real separate-process scheduled work waits for the chat slot, then runs after release')
        finally:
            if lease: lease.release()
            if child.poll() is None:
                child.terminate(); child.wait(timeout=10)
