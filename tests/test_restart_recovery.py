import importlib.util
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace

base=Path(__file__).resolve().parents[1]/'deploy/control_plugin'
spec=importlib.util.spec_from_file_location('restart_eligibility',base/'restart_recovery.py')
module=importlib.util.module_from_spec(spec); spec.loader.exec_module(module)
spec2=importlib.util.spec_from_file_location('restart_store',base/'recovery_store.py')
storage=importlib.util.module_from_spec(spec2); spec2.loader.exec_module(storage)


class RestartEligibilityTests(unittest.TestCase):
    def setUp(self):
        self.entry=SimpleNamespace(resume_pending=True,suspended=False,resume_reason='shutdown_timeout')
        self.goal=SimpleNamespace(status='active')
        self.rows=[{'seq':1,'status':'terminal','outcome':'interrupted'}]
    def test_confirmed_interruption_is_eligible(self):
        self.assertTrue(module.safe_candidate(self.entry,self.rows,self.goal,True))
    def test_unknown_queued_running_or_completed_work_is_never_replayed(self):
        for status in ('unknown','queued','running'):
            self.assertFalse(module.safe_candidate(self.entry,[{'status':status}],self.goal,True))
        self.rows.append({'seq':2,'status':'terminal','outcome':'completed'})
        self.assertFalse(module.safe_candidate(self.entry,self.rows,self.goal,True))
    def test_freshness_suspend_explicit_stop_and_manual_goal_pause_are_preserved(self):
        self.assertFalse(module.safe_candidate(self.entry,self.rows,self.goal,False))
        self.entry.suspended=True
        self.assertFalse(module.safe_candidate(self.entry,self.rows,self.goal,True))
        self.entry.suspended=False; self.entry.resume_reason='user_stop'
        self.assertFalse(module.safe_candidate(self.entry,self.rows,self.goal,True))
        self.entry.resume_reason='shutdown_timeout'; self.goal.status='paused'
        self.assertFalse(module.safe_candidate(self.entry,self.rows,self.goal,True))
    def test_stop_fence_survives_restart_until_a_new_owner_request(self):
        with tempfile.TemporaryDirectory() as folder:
            path=Path(folder)/'state.sqlite'; store=storage.RecoveryStore(path)
            origin={'platform':'discord','chat_id':'thread','user_id':'owner'}
            store.bind('route','sid',origin,new_request=True,stopped=True)
            reopened=storage.RecoveryStore(path); route=reopened.route('route')
            self.assertTrue(route['stopped'])
            self.assertIsNone(reopened.defer_restart('route','sid',route['generation'],{'day':'today'}))
            reopened.bind('route','sid',origin,new_request=True)
            route=reopened.route('route')
            self.assertFalse(route['stopped'])
            self.assertIsNotNone(reopened.defer_restart('route','sid',route['generation'],{'day':'today'}))
