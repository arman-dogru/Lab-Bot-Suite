import concurrent.futures
import importlib.util
import json
import tempfile
import unittest
from pathlib import Path

spec=importlib.util.spec_from_file_location('recovery_store_test',
    Path(__file__).resolve().parents[1]/'deploy/control_plugin/recovery_store.py')
module=importlib.util.module_from_spec(spec)
spec.loader.exec_module(module)
RecoveryStore=module.RecoveryStore


class RecoveryStoreTests(unittest.TestCase):
    def setUp(self):
        self.folder=tempfile.TemporaryDirectory()
        self.path=Path(self.folder.name)/'recovery.sqlite'
        self.now=100
        self.store=RecoveryStore(self.path,clock=lambda:self.now)
        self.budget={'day':'2026-10-30','remaining_usd':0,'paused':False,'max_request_reservation_usd':0.5}
        self.store.bind('route','session',{'platform':'discord','chat_id':'123','user_id':'owner'})

    def tearDown(self): self.folder.cleanup()

    def defer(self,why='budget_exhausted',request='request'):
        self.store.begin(request,'turn','session',key='route')
        return self.store.defer_error(request,self.budget,why)

    def test_daily_reset_restart_and_operator_pause_preserve_pending_work(self):
        pending=self.defer()
        restarted=RecoveryStore(self.path,clock=lambda:self.now)
        pending=restarted.get(pending['token'])
        self.assertFalse(restarted.eligible(pending,dict(self.budget,remaining_usd=20)))
        tomorrow=dict(self.budget,day='2026-10-31',remaining_usd=20)
        self.assertTrue(restarted.eligible(pending,tomorrow))
        self.assertFalse(restarted.eligible(pending,dict(tomorrow,paused=True)))
        self.assertFalse(restarted.eligible(pending,dict(tomorrow,remaining_usd=0.49)))
        self.assertEqual(len(restarted.pending()),1)

    def test_operator_resume_can_continue_same_day_and_unknown_budget_fails_closed(self):
        pending=self.defer('operator_paused')
        available=dict(self.budget,remaining_usd=20)
        self.assertTrue(self.store.eligible(pending,available))
        self.assertFalse(self.store.eligible(pending,None))
        available.pop('max_request_reservation_usd')
        self.assertFalse(self.store.eligible(pending,available))

    def test_stop_and_new_request_fence_late_errors_including_later_calls_of_old_turn(self):
        self.store.begin('in-flight','old-turn','session',key='route')
        self.store.cancel('route')
        self.assertIsNone(self.store.defer_error('in-flight',self.budget,'operator_paused'))
        self.store.begin('late-call','old-turn','session',key='route')
        self.assertIsNone(self.store.defer_error('late-call',self.budget,'operator_paused'))
        self.store.bind('route','session',{'chat_id':'123'},new_request=True)
        self.store.begin('new-call','new-turn','session',key='route')
        self.assertIsNotNone(self.store.defer_error('new-call',self.budget,'operator_paused'))
        self.store.cancel('route')
        self.assertEqual(self.store.pending(),[])

    def test_session_rotation_does_not_resurrect_old_work(self):
        pending=self.defer()
        self.store.bind('route','new-session',{'chat_id':'123'})
        self.assertIsNone(self.store.get(pending['token']))
        self.store.begin('old','later-turn','session',key='route')
        self.assertIsNone(self.store.defer_error('old',self.budget,'operator_paused'))

    def test_concurrent_errors_coalesce_without_storing_private_payloads(self):
        self.store.bind('route','session',{'chat_id':'123','prompt':'PRIVATE-PROMPT','api_key':'PRIVATE-KEY'})
        def fail(index):
            store=RecoveryStore(self.path,clock=lambda:self.now)
            request='request-'+str(index)
            store.begin(request,'same-turn','session',key='route')
            return store.defer_error(request,self.budget,'budget_exhausted')['token']
        with concurrent.futures.ThreadPoolExecutor(max_workers=4) as pool:
            tokens=list(pool.map(fail,range(8)))
        self.assertEqual(len(set(tokens)),1)
        self.assertEqual(len(self.store.pending()),1)
        self.assertNotIn('PRIVATE',json.dumps(self.store.pending()))
        self.assertEqual(self.path.stat().st_mode & 0o777,0o600)

    def test_cron_records_cross_process_updates_and_stale_tokens_are_safe(self):
        self.store.begin('cron-request','cron-turn','cron-session',job_id='job123',execution_id='run123')
        pending=self.store.defer_error('cron-request',self.budget,'budget_exhausted')
        self.assertEqual(pending['target'],'cron:job123')
        other=RecoveryStore(self.path,clock=lambda:self.now)
        other.update(pending,mode='cron',phase='queued',next_try=200)
        self.assertEqual(self.store.get(pending['token'])['phase'],'queued')
        self.store.remove(pending['token'])
        self.assertIsNone(other.update(pending,phase='blocked'))


if __name__=='__main__': unittest.main()
