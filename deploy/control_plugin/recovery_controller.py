"""Resume only previously authorized work after a local budget pause.

Provider admission remains the budget gateway's responsibility. This controller
stores routing metadata and uses Hermes's permission-checked injection API;
it cannot manufacture credit or change the daily limit.
"""
import asyncio
import hashlib
import json
import logging
import os
import re
import sys
import threading
import time
from datetime import datetime,timezone,timedelta
from pathlib import Path
from urllib.parse import urlsplit
from urllib.request import Request,urlopen

from .recovery_store import RecoveryStore

MARKER=re.compile(r'^\[Jetson budget resume ([0-9a-f]{32})\]')
LOG=logging.getLogger('jetson.recovery')
INSPECT=('Continue only the previously assigned request in this conversation. Inspect existing '
         'files, saved progress and tool results before acting. Do not blindly repeat completed '
         'changes. Preserve explicitly paused work and the API budget. When complete, return '
         'the verified result and concise evidence.')


def budget_snapshot():
    request=Request('http://jetson-budget:8870/budget',headers={
        'Authorization':'Bearer '+os.environ['JETSON_LOCAL_TOKEN']})
    try:
        with urlopen(request,timeout=3) as response: return json.load(response)
    except Exception:
        return None


def budget_provider(base_url):
    parsed=urlsplit(str(base_url))
    return parsed.scheme=='http' and parsed.hostname=='jetson-budget' and parsed.port==8870


LOCAL_PLATFORMS={'cli','tui','terminal','local'}


def owner(source):
    platform=getattr(getattr(source,'platform',None),'value','')
    if platform in LOCAL_PLATFORMS:
        return True
    allowed={value.strip() for value in os.environ.get('DISCORD_ALLOWED_USERS','').split(',') if value.strip()}
    return platform=='discord' and str(getattr(source,'user_id','')) in allowed


def goal_hash(state): return hashlib.sha256(state.goal.encode()).hexdigest()
def pause_reason(record): return 'jetson-budget:'+record['token']


class RecoveryController:
    def __init__(self,ctx,home,budget=budget_snapshot,clock=time.time):
        self.ctx,self.budget,self.clock=ctx,budget,clock
        self.store=RecoveryStore(Path(home)/'jetson-recovery.sqlite',clock=clock)
        self.runner=None
        self.thread=None
        self.restart_entries=[]
        self.restart_lock=threading.Lock()
        self.startup_loop=None

    @staticmethod
    def goal_manager(sid):
        from hermes_cli.goals import GoalManager
        return GoalManager(session_id=sid)

    @staticmethod
    def cron():
        from cron import jobs
        return jobs

    @staticmethod
    def session_key():
        from gateway.session_context import get_session_env
        return get_session_env('HERMES_SESSION_KEY')

    async def owner_event(self,*,event=None,gateway=None,known_kickoff=False,**kwargs):
        if event is None or gateway is None or not owner(event.source): return None
        self.runner=gateway
        if hasattr(self,'quiet_filter'): self.install_guard(self.quiet_filter,self.notice_route)
        source=event.source
        if str(source.chat_id)==os.environ.get('DISCORD_STATUS_CHANNEL',''): return None
        text=(event.text or '').lstrip()
        if MARKER.match(text): return None
        key=gateway._session_key_for_source(source)
        entry=await gateway.async_session_store.lookup_by_session_key(key)
        sid=getattr(entry,'session_id','')
        command=text.split()[:2]
        stop=bool(command and command[0] in ('/stop','/new','/reset'))
        goal_change=bool(command and command[0]=='/goal' and
                         len(command)>1 and command[1] not in ('status','show','gate'))
        real_request=bool(getattr(event,'message_id',None) and not event.internal and
                          not text.startswith('/') and not known_kickoff)
        platform=getattr(getattr(source,'platform',None),'value','')
        public={'platform':platform or 'discord','chat_id':str(source.chat_id),
                'user_id':str(getattr(source,'user_id','cli')),
                'chat_type':str(getattr(source,'chat_type',''))}
        if getattr(source,'thread_id',None): public['thread_id']=str(source.thread_id)
        if goal_change: self.store.clear_context_restarts(key)
        self.store.bind(key,sid,public,new_request=stop or goal_change or real_request,stopped=stop)

    def api_start(self,*,api_request_id='',session_id='',turn_id='',task_id='',base_url='',
                  user_message='',**kwargs):
        if not budget_provider(base_url): return
        cron=re.match(r'^cron:([0-9a-f]{12}):(.+)$',str(task_id))
        key='' if cron else self.session_key()
        self.store.begin(api_request_id,turn_id,session_id,key=key,
                         job_id=cron.group(1) if cron else '',execution_id=cron.group(2) if cron else '')
        # Native messaging prefixes the original user message with its sender.
        marker=re.search(r'\[Jetson budget resume ([0-9a-f]{32})\]',str(user_message))
        if marker:
            record=self.store.get(marker.group(1))
            if record and record['sid']==session_id and record.get('key')==key:
                self.store.remove(record['token'])

    def api_success(self,*,api_request_id='',**kwargs):
        self.store.finish(api_request_id)

    def api_error(self,*,api_request_id='',status_code=None,error=None,base_url='',**kwargs):
        if not budget_provider(base_url): return
        message=str(error.get('message','')) if isinstance(error,dict) else str(error or '')
        why=next((code for code in ('budget_exhausted','operator_paused') if code in message),None)
        if not why or (status_code!=402 and '402' not in message):
            self.store.finish(api_request_id)
            return
        budget=self.budget()
        if not budget: return
        record=self.store.defer_error(api_request_id,budget,why)
        if record: self.park_goal(record)

    def aux_finish(self,*,error=None,**kwargs):
        if error:
            self.api_error(error=error,**kwargs)
        else:
            self.api_success(**kwargs)

    def park_goal(self,record):
        if record.get('job_id'): return record
        manager=self.goal_manager(record['sid'])
        state=manager.state
        if state and (state.status=='active' or
                      (state.status=='paused' and state.paused_reason==pause_reason(record))):
            updated=self.store.update(record,mode='goal',goal_hash=goal_hash(state))
            if updated:
                manager.pause(reason=pause_reason(record))
                record=updated
        return record

    async def goal_preflight(self,runner,session_entry,source):
        if not owner(source): return False
        manager=self.goal_manager(session_entry.session_id)
        if not manager.state or manager.state.status!='active': return False
        budget=self.budget()
        if not budget: return False  # gateway remains the authoritative fail-closed admission
        maximum=budget.get('max_request_reservation_usd')
        if not budget['paused'] and isinstance(maximum,(int,float)) and budget['remaining_usd']>=maximum:
            return False
        why='operator_paused' if budget['paused'] else 'budget_exhausted'
        record=self.store.defer_goal(runner._session_key_for_source(source),session_entry.session_id,budget,why)
        if not record: return False
        record=self.park_goal(record)
        if not record.get('notified'):
            await runner._send_goal_status_notice(source,'⏸ Paid work saved until its budget pause clears.')
            self.store.update(record,notified=True)
        return True

    def capture_goal_wait(self,runner,session_entry,source):
        """Persist native wait barriers; this release has no timed-goal poller."""
        if not owner(source): return
        manager=self.goal_manager(session_entry.session_id)
        state=manager.state
        if not state or state.status!='active': return
        until=getattr(state,'waiting_until',0) or 0
        if not (until or getattr(state,'waiting_on_pid',None) or
                getattr(state,'waiting_on_session',None)): return
        budget=self.budget()
        if not budget: return
        record=self.store.defer_goal(runner._session_key_for_source(source),
                                     session_entry.session_id,budget,'goal_wait')
        if record:
            self.store.update(record,mode='goal',goal_hash=goal_hash(state),
                              next_try=max(self.clock()+15,until))

    def busy(self):
        if self.runner and any(getattr(self.runner,'_active_agents',{}).values()): return True
        return any(job.get('run_claim') or job.get('fire_claim') for job in self.cron().list_jobs(include_disabled=True))

    @staticmethod
    def fresh_restart(entry):
        from gateway.run import _is_fresh_gateway_interruption,_auto_continue_freshness_window
        return _is_fresh_gateway_interruption(entry.last_resume_marked_at or entry.updated_at,
                                             window_secs=_auto_continue_freshness_window())

    @staticmethod
    def native_admissions(sid):
        from hermes_cli.goals import _get_session_db
        from hermes_state_runtime import list_session_admissions
        db=_get_session_db()
        if db is None: raise RuntimeError('Conversation metadata unavailable')
        lineage=db.get_compression_lineage(sid)
        return list_session_admissions(db,session_id=lineage[0] if lineage else sid,pending_only=False)

    def report_restart_blocker(self,runner,source):
        if self.startup_loop is None or self.startup_loop.is_closed(): return
        coroutine=runner._send_goal_status_notice(source,
            '⏸ Goal paused after repeated service interruptions. Saved progress is intact; inspect it, then use /goal resume.')
        try:
            future=asyncio.run_coroutine_threadsafe(coroutine,self.startup_loop)
            def finished(result):
                try: result.result()
                except Exception: LOG.warning('Restart blocker notice could not be delivered')
            future.add_done_callback(finished)
        except Exception:
            coroutine.close()
            LOG.warning('Restart blocker notice could not be scheduled')

    def capture_restart(self,runner,entry,generation,budget):
        from .restart_recovery import safe_candidate
        route=self.store.route(entry.session_key)
        if not route or route['sid']!=entry.session_id or route['generation']!=generation or route['stopped']: return
        source=runner._restored_source(entry)
        if not owner(source) or not runner._resume_owner_authorized(entry.session_key,source): return
        if any(record.get('key')==entry.session_key for record in self.store.pending()): return
        manager=self.goal_manager(entry.session_id)
        if not safe_candidate(entry,self.native_admissions(entry.session_id),manager.state,self.fresh_restart(entry)): return
        record=self.store.defer_restart(entry.session_key,entry.session_id,generation,budget or {'day':''})
        if not record: return
        record=self.store.update(record,mode='goal',goal_hash=goal_hash(manager.state),next_try=self.clock()+15)
        if not record: return
        if self.store.context_restart(entry.session_key,goal_hash(manager.state))>=3:
            self.store.update(record,phase='blocked',blocker='Repeated service interruptions; inspect progress before /goal resume')
            manager.pause(reason=pause_reason(record))
            self.report_restart_blocker(runner,source)
        LOG.info('Confirmed interrupted standing goal saved for owner-checked recovery')

    @staticmethod
    def transfer_goal(old_sid,new_sid,state):
        from hermes_cli.goals import GoalState,save_goal,GoalManager
        copied=GoalState.from_json(state.to_json())
        copied.clear_wait()
        save_goal(new_sid,copied)
        GoalManager(old_sid).pause(reason='Transferred after native context reset')

    async def recover_context_reset(self,runner,event,before,old_sid,response):
        prefix='This conversation (including attachments) has grown too large'
        if not before or not owner(event.source) or not str(response or '').startswith(prefix): return False
        key=runner._session_key_for_source(event.source)
        current=self.store.route(key)
        if not current or current['generation']!=before['generation'] or current['sid']!=old_sid:
            return False  # an owner stop/new request superseded this turn
        entry=await runner.async_session_store.lookup_by_session_key(key)
        new_sid=getattr(entry,'session_id','')
        if not new_sid or new_sid==old_sid: return False
        current=self.store.route(key)
        if not current or current['generation']!=before['generation'] or current['stopped']: return False
        manager=self.goal_manager(old_sid)
        state=manager.state
        if not state or state.status!='active' or self.goal_manager(new_sid).state: return False
        count=self.store.context_restart(key,goal_hash(state))
        self.transfer_goal(old_sid,new_sid,state)
        self.store.bind(key,new_sid,current['origin'],new_request=True)
        if count>=3:
            self.goal_manager(new_sid).pause(reason='Repeated context resets; inspect saved progress before resuming')
            await runner._send_goal_status_notice(event.source,'⏸ Goal paused after three context resets. Saved files are intact; inspect progress before /goal resume.')
            return False
        budget=self.budget() or {'day':''}
        record=self.store.defer_goal(key,new_sid,budget,'context_reset')
        if not record: return False
        self.store.update(record,mode='goal',goal_hash=goal_hash(state),admission_sid=old_sid,
                          next_try=self.clock()+15)
        await runner._send_goal_status_notice(event.source,'↻ Goal retained after history reset; resuming from saved files.')
        return True

    def verified_origin(self,record):
        # Use the latest connector-verified routing envelope, rather than a
        # restored SessionEntry whose thread labels may predate later messages.
        # The native injection API revalidates this source and its private proof.
        from hermes_cli.goals import _get_session_db
        from hermes_state_runtime import list_session_admissions
        db=_get_session_db()
        if db is None: raise RuntimeError('Conversation metadata unavailable')
        admission_sid=record.get('admission_sid') or record['sid']
        lineage=db.get_compression_lineage(admission_sid)
        sid=lineage[0] if lineage else admission_sid
        rows=list_session_admissions(db,session_id=sid,pending_only=False)
        for row in reversed(rows):
            envelope=row.get('payload',{}).get('native_text_v1',{})
            if envelope.get('route')!=record['key'] or 'provenance' not in envelope: continue
            source=dict(envelope['source'])
            expected=record['origin']
            if any(str(source.get(field,''))!=str(expected.get(field,''))
                   for field in ('platform','chat_id','user_id','thread_id')):
                raise RuntimeError('Conversation route changed')
            source.pop('message_id',None)
            return source
        raise RuntimeError('No verified conversation route')

    async def before_execute(self,runner,event):
        self.runner=runner
        marker=MARKER.match((event.text or '').lstrip())
        if not marker: return True
        if not owner(event.source): return False
        record=self.store.get(marker.group(1))
        if not record: return False
        if record['phase']=='blocked': return False
        if not self.auto_enabled():
            self.store.update(record,phase='waiting',next_try=self.clock()+300)
            return False
        key=runner._session_key_for_source(event.source)
        entry=await runner.async_session_store.lookup_by_session_key(key)
        if key!=record.get('key') or getattr(entry,'session_id','')!=record['sid']:
            self.store.remove(record['token'])
            return False
        if not self.store.eligible(dict(record,next_try=0),self.budget()):
            self.store.update(record,phase='waiting',attempts=0,next_try=self.clock()+30)
            return False
        if self.busy():
            self.store.update(record,phase='waiting',attempts=0,next_try=self.clock()+30)
            return False
        prompt=INSPECT
        if record['mode']=='goal':
            manager=self.goal_manager(record['sid'])
            state=manager.state
            if not state or goal_hash(state)!=record.get('goal_hash'):
                self.store.remove(record['token']); return False
            if record['why']=='goal_wait' and manager.is_waiting():
                self.store.update(record,phase='waiting',attempts=0,next_try=self.clock()+15)
                return False
            if state.status=='paused' and state.paused_reason==pause_reason(record):
                manager.resume(reset_budget=False)
            elif state.status!='active' or record['phase'] not in ('queued','executing'):
                self.store.remove(record['token']); return False
            prompt=(manager.next_continuation_prompt() or '')+'\n'+INSPECT
        else:
            current=self.goal_manager(record['sid']).state
            if current and current.status=='active':
                # A new or manually resumed standing goal supersedes an ordinary
                # request that was saved while the standing goal was inactive.
                self.store.remove(record['token']); return False
        self.store.update(record,phase='executing',next_try=self.clock()+300)
        event.text=marker.group(0)+'\n'+prompt
        event._jetson_budget_resume=True  # trusted process-only signal for quiet delivery
        return True

    def auto_enabled(self):
        from hermes_cli.config import load_config
        config=load_config() or {}
        entry=((config.get('plugins') or {}).get('entries') or {}).get('jetson-controls') or {}
        return entry.get('allow_gateway_injection') is True

    def cron_definition(self,job):
        fields=('prompt','script','schedule','model','provider','base_url','workdir','skills','deliver','origin')
        return hashlib.sha256(json.dumps({k:job.get(k) for k in fields},sort_keys=True).encode()).hexdigest()

    def tick_cron(self,record,budget):
        jobs=self.cron()
        job=jobs.get_job(record['job_id'])
        if not job:
            self.store.remove(record['token']); return
        reason=pause_reason(record)
        if job.get('state')=='paused' and job.get('paused_reason')!=reason:
            self.store.remove(record['token']); return
        if job.get('run_claim') or job.get('fire_claim'): return
        if record.get('definition') and record['definition']!=self.cron_definition(job):
            self.store.remove(record['token']); return
        if job.get('last_status') not in ('error','failed'):
            if job.get('last_status') in ('ok','delivery_failed'):
                self.store.remove(record['token'])
            return
        if record['phase']=='waiting':
            terminal=job.get('state') in ('completed','cancelled')
            if job.get('state')=='cancelled':
                self.store.remove(record['token']); return
            if not terminal: jobs.pause_job(job['id'],reason=reason)
            record=self.store.update(record,phase='parked',definition=self.cron_definition(job))
        elif record['phase']=='parked' and job.get('state') not in ('paused','completed'):
            # A manual resume/edit supersedes the saved automatic retry.
            self.store.remove(record['token']); return
        if not record or not self.store.eligible(record,budget) or not self.auto_enabled() or self.busy(): return
        if job.get('state')=='completed':
            if job.get('schedule',{}).get('kind')!='once':
                self.store.update(record,phase='blocked',blocker='Completed finite recurring job needs a new occurrence')
                return
            jobs.rearm_oneshot(job['id'],(datetime.now(timezone.utc)+timedelta(minutes=1)).isoformat())
        jobs.trigger_job(job['id'],extra_prompt=INSPECT+'\n\nOriginal assigned job:\n'+str(job.get('prompt') or ''))
        self.store.remove(record['token'])
        LOG.info('Budget-paused schedule rearmed through native scheduler')

    def tick(self):
        budget=self.budget()
        with self.restart_lock:
            entries,self.restart_entries=self.restart_entries,[]
        for runner,entry,generation in entries:
            try: self.capture_restart(runner,entry,generation,budget)
            except Exception as error:
                LOG.warning('Interrupted-goal recovery unavailable (%s)',type(error).__name__)
        for record in self.store.pending():
            try:
                if record.get('job_id'):
                    self.tick_cron(record,budget)
                    continue
                if record['mode']=='goal':
                    manager=self.goal_manager(record['sid'])
                    state=manager.state
                    if (not state or state.status in ('done','cleared') or
                        goal_hash(state)!=record.get('goal_hash') or
                        (state.status=='paused' and state.paused_reason!=pause_reason(record))):
                        self.store.remove(record['token']); continue
                    if record['why']=='goal_wait' and manager.is_waiting(): continue
                if not self.auto_enabled() or not self.store.eligible(record,budget) or self.busy(): continue
                if record['attempts']>=3:
                    self.store.update(record,phase='blocked',blocker='Gateway did not acknowledge three recovery attempts')
                    continue
                origin=self.verified_origin(record)
                message='[Jetson budget resume '+record['token']+']'
                attempts=record['attempts']+1
                self.store.update(record,phase='queued',attempts=attempts,next_try=self.clock()+300)
                accepted=self.ctx.inject_message(message,origin=origin)
                if not accepted:
                    self.store.update(record,phase='blocked' if attempts>=3 else 'waiting')
                break
            except Exception as error:
                attempts=record['attempts']+1
                self.store.update(record,phase='blocked' if attempts>=3 else record['phase'],
                                  attempts=attempts,next_try=self.clock()+300,blocker=type(error).__name__)
                LOG.warning('Budget recovery deferred (%s)',type(error).__name__)

    def install_guard(self,quiet_filter,notice_route):
        module=sys.modules.get('gateway.run')
        cls=getattr(module,'GatewayRunner',None)
        if cls is None: return False
        if getattr(cls,'_jetson_execution_filter',False): return True
        original,original_goal=cls._handle_message,cls._post_turn_goal_continuation
        original_candidates=getattr(cls,'_resume_pending_candidates',None)
        controller=self
        if callable(original_candidates):
            def candidates(runner,*args,**kwargs):
                entries=original_candidates(runner,*args,**kwargs)
                LOG.info('Native interrupted-session enumeration observed (%s)',
                         'withheld' if entries is None else len(entries))
                if entries is not None:
                    controller.runner=runner
                    try: controller.startup_loop=asyncio.get_running_loop()
                    except RuntimeError: controller.startup_loop=None
                    notice_route(runner)
                    with controller.restart_lock:
                        for entry in entries:
                            route=controller.store.route(entry.session_key)
                            if route and not route['stopped']:
                                controller.restart_entries.append((runner,entry,route['generation']))
                return entries
            cls._resume_pending_candidates=candidates
        async def guarded(runner,event):
            if not await controller.before_execute(runner,event): return None
            if owner(event.source): notice_route(runner)
            key=runner._session_key_for_source(event.source)
            before=controller.store.route(key) if owner(event.source) else None
            entry=await runner.async_session_store.lookup_by_session_key(key) if before else None
            old_sid=getattr(entry,'session_id','')
            response=await original(runner,event)
            if await controller.recover_context_reset(runner,event,before,old_sid,response): return None
            if response and owner(event.source) and runner._turn_failed():
                key=runner._session_key_for_source(event.source)
                saved=next((r for r in controller.store.pending() if r.get('key')==key),None)
                if saved and saved['why'] in ('operator_paused','budget_exhausted'):
                    if saved['why']=='operator_paused':
                        return 'Paid work is paused. Your request is saved. Use /jetson resume to continue, or /stop here to cancel.'
                    return 'The daily API allowance is exhausted. Your request is saved for after the next Toronto midnight. Use /stop here to cancel.'
            try:
                return await quiet_filter(runner,event,response)
            except Exception as error:
                LOG.warning('Quiet response filter unavailable (%s)',type(error).__name__)
                return response
        async def goal_guarded(runner,*,session_entry,source,final_response):
            if await controller.goal_preflight(runner,session_entry,source): return None
            result=await original_goal(runner,session_entry=session_entry,source=source,final_response=final_response)
            controller.capture_goal_wait(runner,session_entry,source)
            return result
        cls._handle_message,cls._post_turn_goal_continuation=guarded,goal_guarded
        cls._jetson_execution_filter=True
        LOG.info('Interrupted-goal lifecycle guard installed')
        return True

    def start(self,quiet_filter,notice_route):
        if self.thread: return
        self.quiet_filter,self.notice_route=quiet_filter,notice_route
        def watch():
            while True:
                installed=False
                ready=False
                try:
                    # Install lifecycle guards before startup enumerates interrupted
                    # sessions; injection readiness is published after that phase.
                    installed=self.install_guard(quiet_filter,notice_route)
                    ready=bool(self.ctx._manager.has_gateway_message_injector)
                    if installed and ready:
                        self.tick()
                except Exception as error:
                    LOG.warning('Budget recovery monitor unavailable (%s)',type(error).__name__)
                time.sleep(15 if installed and ready else 1)
        self.install_guard(quiet_filter,notice_route)
        self.thread=threading.Thread(target=watch,name='jetson-budget-recovery',daemon=True)
        self.thread.start()
