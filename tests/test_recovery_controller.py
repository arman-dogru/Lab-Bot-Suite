import asyncio
import copy
import importlib
import json
import sys
import tempfile
import types
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

package=types.ModuleType('jetson_recovery_test_package')
package.__path__=[str(Path(__file__).resolve().parents[1]/'deploy/control_plugin')]
sys.modules[package.__name__]=package
recovery=importlib.import_module(package.__name__+'.recovery_controller')


class Goal:
    def __init__(self):
        self.state=SimpleNamespace(goal='Finish the assigned fixture',status='active',paused_reason=None)
        self.reset=None
        self.waiting=False
    def is_waiting(self): return self.waiting
    def pause(self,reason): self.state.status,self.state.paused_reason='paused',reason
    def resume(self,reset_budget=True):
        self.reset=reset_budget
        self.state.status,self.state.paused_reason='active',None
    def next_continuation_prompt(self): return 'Continue the existing goal and verify its artifacts.'


class Cron:
    def __init__(self): self.jobs={}; self.actions=[]
    def get_job(self,key): return self.jobs.get(key)
    def list_jobs(self,include_disabled=False): return list(self.jobs.values())
    def pause_job(self,key,reason=None):
        self.actions.append(('pause',key))
        self.jobs[key].update(state='paused',enabled=False,paused_reason=reason)
    def rearm_oneshot(self,key,at):
        self.actions.append(('rearm',key))
        self.jobs[key].update(state='scheduled',enabled=True)
    def trigger_job(self,key,extra_prompt=None):
        self.actions.append(('trigger',key,extra_prompt))
        self.jobs[key].update(state='scheduled',enabled=True,paused_reason=None)


class RecoveryControllerTests(unittest.TestCase):
    def setUp(self):
        self.temp=tempfile.TemporaryDirectory()
        self.now=100
        self.budget={'day':'2026-10-31','paused':True,'remaining_usd':20,'max_request_reservation_usd':0.5}
        self.sent=[]
        self.ctx=SimpleNamespace(inject_message=lambda text,**kw:self.sent.append((text,kw)) or True)
        self.controller=recovery.RecoveryController(self.ctx,self.temp.name,budget=lambda:self.budget,clock=lambda:self.now)
        self.goal=Goal()
        self.cron=Cron()
        self.controller.goal_manager=lambda sid:self.goal
        self.controller.cron=lambda:self.cron
        self.controller.session_key=lambda:'route'
        self.controller.auto_enabled=lambda:True
        self.controller.busy=lambda:False
        self.controller.verified_origin=lambda record:record['origin']
        self.controller.store.bind('route','sid',{'platform':'discord','chat_id':'thread','user_id':'owner'})
        self.env=patch.dict('os.environ',{'DISCORD_ALLOWED_USERS':'owner','DISCORD_STATUS_CHANNEL':'status'})
        self.env.start()
        self.loop=asyncio.new_event_loop()

    def tearDown(self):
        self.loop.close(); self.env.stop(); self.temp.cleanup()

    def start_request(self,request='request',turn='turn',**extra):
        self.controller.api_start(api_request_id=request,session_id='sid',turn_id=turn,
                                  base_url='http://jetson-budget:8870/v1',**extra)

    def fail(self,why='operator_paused',request='request'):
        self.controller.api_error(api_request_id=request,status_code=402,
            error={'message':why},base_url='http://jetson-budget:8870/v1')
        return self.controller.store.pending()[0]

    def event(self,token,user='owner'):
        source=SimpleNamespace(platform=SimpleNamespace(value='discord'),user_id=user,chat_id='thread')
        return SimpleNamespace(source=source,text='[Jetson budget resume '+token+']',
                               message_id='native-plugin-uuid',internal=True)

    def runner(self,sid='sid'):
        async def lookup(key): return SimpleNamespace(session_id=sid)
        return SimpleNamespace(_session_key_for_source=lambda source:'route',
            async_session_store=SimpleNamespace(lookup_by_session_key=lookup))

    def test_owner_pause_then_resume_uses_existing_session_and_preserves_goal_turn_budget(self):
        self.start_request()
        record=self.fail()
        self.assertEqual(record['mode'],'goal')
        self.assertEqual(self.goal.state.paused_reason,recovery.pause_reason(record))
        self.controller.tick()
        self.assertEqual(self.sent,[])
        self.budget['paused']=False
        self.controller.tick()
        self.assertEqual(len(self.sent),1)
        event=self.event(record['token'])
        self.assertTrue(self.loop.run_until_complete(self.controller.before_execute(self.runner(),event)))
        self.assertFalse(self.goal.reset)
        self.assertTrue(event._jetson_budget_resume)
        self.assertIn('Inspect existing',event.text)
        self.start_request(request='resumed',turn='resumed-turn',user_message='[Operator] '+event.text)
        self.assertEqual(self.controller.store.pending(),[])

    def test_cancelled_rotated_and_unauthorized_markers_do_not_execute(self):
        self.start_request(); record=self.fail()
        self.budget['paused']=False
        self.assertFalse(self.loop.run_until_complete(self.controller.before_execute(self.runner(),self.event(record['token'],'stranger'))))
        self.controller.store.cancel('route')
        self.assertFalse(self.loop.run_until_complete(self.controller.before_execute(self.runner(),self.event(record['token']))))
        self.controller.store.bind('route','sid',{'platform':'discord','chat_id':'thread','user_id':'owner'},new_request=True)
        self.start_request(request='next',turn='next-turn'); record=self.fail(request='next')
        self.assertFalse(self.loop.run_until_complete(self.controller.before_execute(self.runner('new-sid'),self.event(record['token']))))

    def test_explicit_goal_pause_survives_budget_reset(self):
        self.start_request(); self.fail()
        self.goal.pause('user-paused')
        self.budget.update(paused=False,day='2026-11-01')
        self.controller.tick()
        self.assertEqual(self.sent,[])
        self.assertEqual(self.goal.state.paused_reason,'user-paused')
        self.assertEqual(self.controller.store.pending(),[])

    def test_native_timed_wait_is_saved_and_wakes_only_after_barrier_and_budget_allow(self):
        self.goal.state.waiting_until=130
        self.goal.waiting=True
        self.controller.capture_goal_wait(self.runner(),SimpleNamespace(session_id='sid'),self.event('0'*32).source)
        record=self.controller.store.pending()[0]
        self.assertEqual(record['why'],'goal_wait')
        self.assertEqual(self.goal.state.status,'active')
        self.budget['paused']=False
        self.now=129; self.controller.tick(); self.assertEqual(self.sent,[])
        self.now=131; self.controller.tick(); self.assertEqual(self.sent,[])
        self.goal.waiting=False
        self.budget['paused']=True; self.controller.tick(); self.assertEqual(self.sent,[])
        self.budget['paused']=False; self.controller.tick(); self.assertEqual(len(self.sent),1)
        self.assertTrue(self.loop.run_until_complete(self.controller.before_execute(self.runner(),self.event(record['token']))))

    def test_native_wait_cancellation_and_manual_pause_prevent_wakeup(self):
        self.goal.state.waiting_until=130
        source=self.event('0'*32).source
        self.controller.capture_goal_wait(self.runner(),SimpleNamespace(session_id='sid'),source)
        self.controller.store.cancel('route')
        self.now=150; self.budget['paused']=False
        self.controller.tick(); self.assertEqual(self.sent,[])
        self.controller.capture_goal_wait(self.runner(),SimpleNamespace(session_id='sid'),source)
        self.goal.pause('user-paused')
        self.now=180; self.controller.tick()
        self.assertEqual(self.sent,[])
        self.assertEqual(self.controller.store.pending(),[])

    def test_native_wait_barrier_is_rechecked_at_actual_execution(self):
        self.goal.state.waiting_until=130
        self.controller.capture_goal_wait(self.runner(),SimpleNamespace(session_id='sid'),self.event('0'*32).source)
        self.now=150; self.budget['paused']=False
        self.controller.tick()
        record=self.controller.store.pending()[0]
        self.goal.waiting=True
        self.assertFalse(self.loop.run_until_complete(self.controller.before_execute(self.runner(),self.event(record['token']))))
        self.assertEqual(self.controller.store.get(record['token'])['phase'],'waiting')

    def reset_fixture(self):
        goals={'sid':self.goal}
        def manager(sid):
            if sid not in goals:
                goals[sid]=Goal(); goals[sid].state=None
            return goals[sid]
        def transfer(old,new,state):
            manager(new).state=copy.deepcopy(state)
            manager(old).pause('Transferred after native context reset')
        self.controller.goal_manager=manager
        self.controller.transfer_goal=transfer
        notices=[]
        def runner(sid):
            instance=self.runner(sid)
            async def send(source,text): notices.append(text)
            instance._send_goal_status_notice=send
            return instance
        return manager,runner,notices

    def test_native_context_reset_preserves_goal_and_uses_original_verified_admission(self):
        manager,runner,notices=self.reset_fixture()
        before=self.controller.store.route('route')
        self.budget['paused']=False
        response='This conversation (including attachments) has grown too large to send'
        recovered=self.loop.run_until_complete(self.controller.recover_context_reset(
            runner('new-sid'),self.event('0'*32),before,'sid',response))
        self.assertTrue(recovered)
        self.assertEqual(manager('sid').state.status,'paused')
        self.assertEqual(manager('new-sid').state.goal,'Finish the assigned fixture')
        record=self.controller.store.pending()[0]
        self.assertEqual(record['admission_sid'],'sid')
        self.assertEqual(record['sid'],'new-sid')
        self.now+=16; self.controller.tick()
        self.assertTrue(self.loop.run_until_complete(self.controller.before_execute(runner('new-sid'),self.event(record['token']))))
        self.assertEqual(len(notices),1)

    def test_context_reset_cannot_resurrect_a_stopped_or_manually_paused_goal(self):
        manager,runner,notices=self.reset_fixture()
        before=self.controller.store.route('route')
        self.controller.store.cancel('route')
        response='This conversation (including attachments) has grown too large'
        self.assertFalse(self.loop.run_until_complete(self.controller.recover_context_reset(
            runner('new-sid'),self.event('0'*32),before,'sid',response)))
        before=self.controller.store.route('route')
        self.goal.pause('user-paused')
        self.assertFalse(self.loop.run_until_complete(self.controller.recover_context_reset(
            runner('new-sid'),self.event('0'*32),before,'sid',response)))
        self.assertEqual(self.controller.store.pending(),[])
        self.assertEqual(notices,[])

    def test_three_context_resets_pause_only_the_affected_goal_and_counter_survives_restart(self):
        manager,runner,notices=self.reset_fixture()
        response='This conversation (including attachments) has grown too large'
        old='sid'
        for number in range(1,4):
            new='successor-'+str(number)
            before=self.controller.store.route('route')
            recovered=self.loop.run_until_complete(self.controller.recover_context_reset(
                runner(new),self.event('0'*32),before,old,response))
            self.assertEqual(recovered,number<3)
            old=new
        self.assertEqual(manager(old).state.status,'paused')
        self.assertIn('Repeated context resets',manager(old).state.paused_reason)
        self.assertEqual(self.controller.store.pending(),[])
        reopened=recovery.RecoveryStore(self.controller.store.path)
        self.assertEqual(reopened.context_restart('route',recovery.goal_hash(manager(old).state)),4)

    def test_stop_during_context_reset_lookup_prevents_transfer(self):
        manager,runner,notices=self.reset_fixture()
        before=self.controller.store.route('route')
        instance=runner('new-sid')
        async def lookup(key):
            self.controller.store.cancel('route')
            return SimpleNamespace(session_id='new-sid')
        instance.async_session_store.lookup_by_session_key=lookup
        self.assertFalse(self.loop.run_until_complete(self.controller.recover_context_reset(
            instance,self.event('0'*32),before,'sid','This conversation (including attachments) has grown too large')))
        self.assertIsNone(manager('new-sid').state)
        self.assertEqual(self.controller.store.pending(),[])

    def test_late_queued_event_cannot_unblock_a_blocked_job(self):
        self.start_request(); record=self.fail()
        self.controller.store.update(record,phase='blocked')
        self.budget['paused']=False
        self.assertFalse(self.loop.run_until_complete(self.controller.before_execute(self.runner(),self.event(record['token']))))
        self.assertEqual(self.controller.store.get(record['token'])['phase'],'blocked')

    def restart_fixture(self):
        instance=self.runner()
        source=self.event('0'*32).source
        instance._restored_source=lambda entry:source
        instance._resume_owner_authorized=lambda key,origin:True
        self.controller.fresh_restart=lambda entry:True
        self.controller.native_admissions=lambda sid:[{'seq':1,'status':'terminal','outcome':'interrupted'}]
        entry=SimpleNamespace(session_key='route',session_id='sid',resume_pending=True,
                              suspended=False,resume_reason='shutdown_timeout')
        return instance,entry

    def test_confirmed_restart_interruption_uses_bounded_owner_scoped_recovery(self):
        instance,entry=self.restart_fixture()
        self.controller.capture_restart(instance,entry,0,self.budget)
        record=self.controller.store.pending()[0]
        self.assertEqual(record['why'],'restart_interrupted')
        self.assertEqual(record['mode'],'goal')
        self.budget['paused']=False; self.now+=16
        self.controller.tick()
        self.assertEqual(len(self.sent),1)
        self.assertTrue(self.loop.run_until_complete(self.controller.before_execute(instance,self.event(record['token']))))

    def test_restart_recovery_preserves_unknown_manual_pause_and_stop_fences(self):
        instance,entry=self.restart_fixture()
        self.controller.native_admissions=lambda sid:[{'seq':1,'status':'unknown','outcome':None}]
        self.controller.capture_restart(instance,entry,0,self.budget)
        self.assertEqual(self.controller.store.pending(),[])
        self.controller.native_admissions=lambda sid:[{'seq':1,'status':'terminal','outcome':'interrupted'}]
        self.goal.pause('user-paused')
        self.controller.capture_restart(instance,entry,0,self.budget)
        self.assertEqual(self.controller.store.pending(),[])
        self.goal.state.status='active'
        self.controller.store.cancel('route')
        self.controller.capture_restart(instance,entry,1,self.budget)
        self.assertEqual(self.controller.store.pending(),[])

    def test_repeated_restart_failures_pause_and_report_only_that_goal(self):
        instance,entry=self.restart_fixture(); notices=[]
        self.controller.report_restart_blocker=lambda *args:notices.append(True)
        for number in range(3):
            self.controller.capture_restart(instance,entry,0,self.budget)
            record=self.controller.store.pending()[0]
            if number<2: self.controller.store.remove(record['token'])
        self.assertEqual(self.goal.state.status,'paused')
        self.assertEqual(self.controller.store.pending()[0]['phase'],'blocked')
        self.assertEqual(notices,[True])

    def test_permission_and_busy_job_checks_do_not_spend_or_unpause(self):
        self.start_request(); record=self.fail()
        self.budget['paused']=False
        self.controller.auto_enabled=lambda:False
        self.controller.tick()
        self.assertEqual(self.sent,[])
        self.assertFalse(self.loop.run_until_complete(self.controller.before_execute(self.runner(),self.event(record['token']))))
        self.controller.auto_enabled=lambda:True
        self.controller.busy=lambda:True
        self.assertFalse(self.loop.run_until_complete(self.controller.before_execute(self.runner(),self.event(record['token']))))
        self.assertEqual(self.goal.state.status,'paused')

    def test_daily_reset_waits_for_new_day_and_preserves_plain_requests(self):
        self.goal.pause('user-paused')
        self.budget.update(paused=False,remaining_usd=0)
        self.start_request(); record=self.fail('budget_exhausted')
        self.assertEqual(record['mode'],'plain')
        self.budget['remaining_usd']=20
        self.controller.tick(); self.assertEqual(self.sent,[])
        self.budget['day']='2026-11-01'
        self.controller.tick(); self.assertEqual(len(self.sent),1)
        self.assertTrue(self.loop.run_until_complete(self.controller.before_execute(self.runner(),self.event(record['token']))))
        self.assertEqual(self.goal.state.paused_reason,'user-paused')

    def test_repeated_dispatch_failures_block_only_the_saved_job(self):
        self.start_request(); self.fail()
        self.budget['paused']=False
        attempts=[]
        self.ctx.inject_message=lambda *a,**k:attempts.append(True) or False
        for _ in range(4):
            self.controller.tick(); self.now+=301
        self.assertEqual(len(attempts),3)
        self.assertEqual(self.controller.store.pending()[0]['phase'],'blocked')

    def test_foreign_provider_and_unrelated_errors_never_create_recovery_work(self):
        self.controller.api_start(api_request_id='foreign',session_id='sid',turn_id='foreign',
                                  base_url='https://api.example.com/v1')
        self.controller.api_error(api_request_id='foreign',status_code=402,error={'message':'operator_paused'},
                                  base_url='https://api.example.com/v1')
        self.start_request()
        self.controller.api_error(api_request_id='request',status_code=503,error={'message':'network failed'},
                                  base_url='http://jetson-budget:8870/v1')
        self.assertEqual(self.controller.store.pending(),[])

    def test_goal_judging_is_parked_locally_when_headroom_is_insufficient(self):
        notices=[]
        async def send(source,message): notices.append(message)
        runner=self.runner(); runner._send_goal_status_notice=send
        self.budget.update(paused=False,remaining_usd=0.1)
        source=self.event('0'*32).source
        self.assertTrue(self.loop.run_until_complete(self.controller.goal_preflight(runner,SimpleNamespace(session_id='sid'),source)))
        self.assertEqual(self.goal.state.status,'paused')
        self.assertEqual(len(notices),1)

    def cron_error(self,terminal=True):
        key='123456abcdef'
        self.cron.jobs[key]={'id':key,'prompt':'Verify the originally assigned fixture',
            'schedule':{'kind':'once' if terminal else 'cron'},'state':'completed' if terminal else 'scheduled',
            'enabled':not terminal,'last_status':'error','paused_reason':None}
        self.controller.api_start(api_request_id='cron-request',session_id='cron-sid',turn_id='cron-turn',
            task_id='cron:'+key+':execution',base_url='http://jetson-budget:8870/v1')
        record=self.fail(request='cron-request')
        return key,record

    def test_failed_one_shot_rearms_through_native_api_with_original_scope(self):
        key,record=self.cron_error()
        self.controller.tick()
        self.assertEqual(self.cron.actions,[])
        self.budget['paused']=False
        self.controller.tick()
        self.assertEqual([action[0] for action in self.cron.actions],['rearm','trigger'])
        self.assertIn('Verify the originally assigned fixture',self.cron.actions[-1][2])
        self.assertEqual(self.controller.store.pending(),[])

    def test_recurring_schedule_is_parked_and_explicit_manual_pause_wins(self):
        key,record=self.cron_error(terminal=False)
        self.controller.tick()
        self.assertEqual(self.cron.jobs[key]['paused_reason'],recovery.pause_reason(record))
        self.cron.jobs[key]['paused_reason']='user-paused'
        self.budget['paused']=False
        self.controller.tick()
        self.assertEqual([action[0] for action in self.cron.actions],['pause'])
        self.assertEqual(self.controller.store.pending(),[])

    def test_definition_changes_cancel_saved_schedule_retry(self):
        key,record=self.cron_error()
        self.controller.tick()
        self.cron.jobs[key]['prompt']='A replacement request'
        self.budget['paused']=False
        self.controller.tick()
        self.assertEqual(self.cron.actions,[])
        self.assertEqual(self.controller.store.pending(),[])

    def test_execution_guard_runs_before_native_handler_even_when_ingress_hooks_are_skipped(self):
        self.start_request(); record=self.fail()
        self.controller.store.cancel('route')
        calls=[]
        class FakeRunner:
            _active_agents={}
            def _turn_failed(self): return False
            async def _handle_message(self,event): calls.append('model'); return 'answer'
            async def _post_turn_goal_continuation(self,**kwargs): calls.append('judge')
            def _session_key_for_source(self,source): return 'route'
        FakeRunner.async_session_store=self.runner().async_session_store
        async def quiet(runner,event,response): return response
        fake_module=SimpleNamespace(GatewayRunner=FakeRunner)
        with patch.dict(sys.modules,{'gateway.run':fake_module}):
            self.assertTrue(self.controller.install_guard(quiet,lambda runner:None))
            self.assertIsNone(self.loop.run_until_complete(FakeRunner()._handle_message(self.event(record['token']))))
        self.assertEqual(calls,[])

    def test_canonical_origin_uses_verified_route_without_copying_conversation_text(self):
        source={'platform':'discord','chat_id':'thread','user_id':'owner','chat_name':'Current thread'}
        rows=[{'payload':{'text':'PRIVATE request text','native_text_v1':
                {'route':'route','source':source,'provenance':{}}}}]
        db=SimpleNamespace(get_compression_lineage=lambda sid:[sid])
        modules={'hermes_cli.goals':SimpleNamespace(_get_session_db=lambda:db),
                 'hermes_state_runtime':SimpleNamespace(list_session_admissions=lambda *a,**k:rows)}
        record={'sid':'sid','key':'route','origin':{'platform':'discord','chat_id':'thread','user_id':'owner'}}
        with patch.dict(sys.modules,modules):
            result=recovery.RecoveryController.verified_origin(self.controller,record)
            self.assertEqual(result['chat_name'],'Current thread')
            self.assertNotIn('PRIVATE',json.dumps(result))
            source['user_id']='stranger'
            with self.assertRaises(RuntimeError):
                recovery.RecoveryController.verified_origin(self.controller,record)


if __name__=='__main__': unittest.main()
