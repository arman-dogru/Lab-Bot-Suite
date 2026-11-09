import importlib.util
import threading
import unittest
from pathlib import Path
from types import SimpleNamespace

spec=importlib.util.spec_from_file_location('jetson_workload',Path(__file__).resolve().parents[1]/'deploy/control_plugin/workload.py')
workload=importlib.util.module_from_spec(spec)
spec.loader.exec_module(workload)


class WorkloadTests(unittest.TestCase):
    def test_waits_without_running_then_releases_shared_slot(self):
        actions=[]
        lease=SimpleNamespace(release=lambda:actions.append('release'))
        busy=SimpleNamespace(reason=workload.CAPACITY)
        answers=iter([(None,busy),(None,busy),(lease,None)])
        def run(job,**kwargs): actions.append('run'); return 'result'
        result=workload.run_serial({'id':'job'},run,lambda job:next(answers),
                                   lambda job:actions.append('cancel'),sleep=lambda seconds:actions.append('wait'))
        self.assertEqual(result,'result')
        self.assertEqual(actions,['wait','wait','run','release'])

    def test_cancelled_wait_never_executes_or_acquires(self):
        event=threading.Event(); calls=[]
        def sleep(seconds): event.set()
        result=workload.run_serial({'id':'job'},lambda *a,**k:calls.append('run'),
            lambda job:(None,SimpleNamespace(reason=workload.CAPACITY)),
            lambda job:calls.append('cancel'),sleep=sleep,transport_cancel=event)
        self.assertTrue(result)
        self.assertEqual(calls,['cancel'])

    def test_uncertain_registry_fails_closed(self):
        calls=[]
        with self.assertRaisesRegex(RuntimeError,'COORDINATION_UNAVAILABLE'):
            workload.run_serial({'id':'job'},lambda *a,**k:calls.append('run'),
                lambda job:(None,SimpleNamespace(reason='COORDINATION_UNAVAILABLE')),
                lambda job:None,sleep=lambda seconds:calls.append('wait'))
        self.assertEqual(calls,[])

    def test_execution_error_still_releases_slot(self):
        calls=[]
        lease=SimpleNamespace(release=lambda:calls.append('released'))
        def run(*a,**kwargs): raise ValueError('fixture failure')
        with self.assertRaises(ValueError):
            workload.run_serial({'id':'job'},run,lambda job:(lease,None),lambda job:None)
        self.assertEqual(calls,['released'])
