"""Discord/CLI operator controls that never invoke a model."""
import json
import hashlib
import logging
import os
import re
import sys
from pathlib import Path
from types import MethodType
from urllib.request import Request, urlopen

_RECOVERY=None


def _policy():
    try:
        from . import policy as module
        return module
    except ImportError:
        import importlib.util
        path=Path(__file__).resolve().with_name('policy.py')
        spec=importlib.util.spec_from_file_location('jetson_policy',path)
        module=importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)
        return module


def handle(raw_args):
    parts=raw_args.strip().split()
    command=parts[0].lower() if parts else "status"
    if command=='review':
        return _policy().review_command(parts[1:])
    if command=='jobs':
        if _RECOVERY is None: return 'Saved-work recovery is unavailable. Local budget controls still work.'
        records=_RECOVERY.store.pending()
        lines=['Saved work awaiting recovery: '+str(len(records))]
        for record in records:
            lines.append('{} · {} · {}'.format(record['mode'],record['phase'],record['why']))
            if record.get('blocker'): lines.append(str(record['blocker'])[:200])
        return '\n'.join(lines)+'\nNo model call was used.'
    if command not in ("status","budget","pause","resume"):
        return "Use /jetson status | budget | pause | resume | jobs | review. Use /stop to interrupt the current task."
    path="/"+command if command in ("pause","resume") else "/budget"
    request=Request("http://jetson-budget:8870"+path,
                    data=b'{}' if command in ("pause","resume") else None,
                    headers={"Authorization":"Bearer "+os.environ["JETSON_LOCAL_TOKEN"],
                             "Content-Type":"application/json"})
    try:
        with urlopen(request,timeout=5) as response:
            status=json.load(response)
    except Exception:
        return "Local budget service is unavailable. Paid work cannot proceed. Use the Jetson terminal for recovery."
    return ("Jetson Bot: %s\n%s (%s)\nBilled: $%.4f; reserved/committed: $%.4f\n"
            "Daily allowance: $%.2f; remaining: $%.4f\n"
            "Uncertain or in-flight requests: %d\nReview: %s\nNo model call was used for this status.") % (
            "paid work paused" if status["paused"] else "ready",status["day"],status["timezone"],
            status["billed_usd"],status["committed_usd"],status["cap_usd"],status["remaining_usd"],
            status["outstanding_reservations"],_policy().current_mode())


def register(ctx):
    global _RECOVERY
    from .workload import install as install_workload_limit
    install_workload_limit()
    ctx.register_command("jetson",handler=handle,
                         description="Local status, budget, pause, resume and review controls; no model usage.")
    try:
        def tool_policy(*positional,tool_name='',args=None,**kwargs):
            if positional:
                tool_name=positional[0]
                if len(positional)>1 and isinstance(positional[1],dict):
                    args=positional[1]
            return _policy().decide_tool(tool_name,args if isinstance(args,dict) else {},
                                         interactive=sys.stdin.isatty())
        ctx.register_hook('pre_tool_call',tool_policy)
    except Exception as error:
        logging.getLogger('jetson.review').error('Review gate unavailable (%s)',type(error).__name__)
    ctx.register_hook('post_gateway_admission',status_channel)
    try:
        from .recovery_controller import RecoveryController
        home=getattr(ctx._manager,'home_path',None) or os.environ.get('HERMES_HOME','/opt/data')
        _RECOVERY=RecoveryController(ctx,home)
        async def recovery_owner_event(**kwargs):
            event=kwargs.get('event')
            return await _RECOVERY.owner_event(known_kickoff=_goal_start_key(event) in _goal_starts(),**kwargs)
        ctx.register_hook('pre_gateway_dispatch',recovery_owner_event)
        ctx.register_hook('pre_api_request',_RECOVERY.api_start)
        ctx.register_hook('post_api_request',_RECOVERY.api_success)
        ctx.register_hook('api_request_error',_RECOVERY.api_error)
        ctx.register_hook('pre_auxiliary_call',_RECOVERY.api_start)
        ctx.register_hook('post_auxiliary_call',_RECOVERY.aux_finish)
        _RECOVERY.start(_quiet_goal_response,_goal_notice_route)
    except Exception as error:
        logging.getLogger('jetson.recovery').error('Saved-work recovery unavailable (%s)',type(error).__name__)
    ctx.register_hook('pre_gateway_dispatch',goal_dispatch)


def status_channel(*,platform='',source=None,text='',**kwargs):
    """Ordinary text in the status channel is handled locally after owner auth."""
    channel=os.environ.get('DISCORD_STATUS_CHANNEL','')
    if platform!='discord' or not channel or str((source or {}).get('chat_id',''))!=channel:
        return None
    words=text.strip().split()
    known=('status','budget','pause','resume','jobs','review')
    command=words[0].lower() if words and words[0].lower() in known else 'status'
    payload=text.strip() if command=='review' else command
    return {'action':'handled','reply':handle(payload)}


class GoalEvidenceGuard:
    """Bound repeated missing-evidence failures without limiting productive work."""
    def __init__(self,records=None):
        self.records=records if records is not None else {}

    def check(self,session_id,goal,reason):
        signature=hashlib.sha256(goal.encode()).hexdigest()
        record=self.records.setdefault(session_id,{'goal':signature,'failures':0})
        if record['goal']!=signature:
            record.update(goal=signature,failures=0)
        missing_evidence=bool(re.search(
            r'(?:claim|assert).{0,250}(?:without|no ).{0,160}(?:output|evidence|excerpt|content)',
            reason,re.IGNORECASE))
        record['failures']=record['failures']+1 if missing_evidence else 0
        return record['failures']>=3


def _guard_path():
    return Path(os.environ.get('HERMES_HOME','/opt/data'))/'jetson-goal-guard.json'


def _save_guard(path,records):
    temporary=path.with_suffix('.tmp')
    with temporary.open('w') as stream:
        os.chmod(str(temporary),0o600)
        json.dump(records,stream)
    os.replace(str(temporary),str(path))


def _goal_start_key(event):
    source=getattr(event,'source',None)
    message_id=getattr(event,'message_id',None)
    if not message_id:
        return None
    return str(getattr(source,'chat_id',''))+':'+str(message_id)


def _goal_starts_path():
    return Path(os.environ.get('HERMES_HOME','/opt/data'))/'jetson-goal-starts.json'


def _goal_starts():
    try:
        records=json.loads(_goal_starts_path().read_text())
        return records if isinstance(records,list) else []
    except (ValueError,OSError):
        return []


def _remember_goal_start(event):
    # Native /goal kickoff reuses the owner's command message ID. Remember only
    # IDs observed by the owner-only command hook, never a model-supplied prefix.
    key=_goal_start_key(event)
    if key:
        records=_goal_starts()
        if key not in records:
            _save_guard(_goal_starts_path(),(records+[key])[-64:])


def _goal_notice_route(gateway):
    """Pinned-release adapter shim: native goal notices belong in the status channel."""
    if getattr(gateway,'_jetson_goal_notices_routed',False):
        return
    original=getattr(gateway,'_send_goal_status_notice',None)
    if original is None:
        return

    async def routed(source,message):
        platform=getattr(getattr(source,'platform',None),'value','')
        channel=os.environ.get('DISCORD_STATUS_CHANNEL','')
        if platform!='discord' or not channel:
            return await original(source,message)
        # Routine judge CONTINUE notices are represented by the edited activity card.
        if message.lstrip().startswith('↻'):
            return
        if message.lstrip().startswith('✓'):
            message='✓ Goal completed. The final result is in its task conversation.'
        adapter=gateway._goal_notice_adapter(source)
        if adapter:
            return await adapter.send(channel,message,metadata=None)

    gateway._send_goal_status_notice=routed
    gateway._jetson_goal_notices_routed=True


def _goal_delivery_route(gateway,source):
    """Pinned-release shim: keep automatic CONTINUE replies in the transcript only.

    The native handler has already persisted the turn and run its goal judge when
    it returns. Suppress only automatic goal turns that still need continuation;
    final answers, failed turns, commands and requests from the owner stay visible.
    """
    if getattr(gateway,'_jetson_execution_filter',False) or getattr(gateway,'_jetson_goal_delivery_routed',False):
        return
    original=getattr(gateway,'_handle_message',None)
    if original is None:
        return

    async def quiet_handler(runner,event):
        response=await original(event)
        return await _quiet_goal_response(runner,event,response)

    # This release's durable actor calls runner._handle_message for execution;
    # adapter._message_handler only admits the queued work and returns None early.
    gateway._handle_message=MethodType(quiet_handler,gateway)
    gateway._jetson_goal_delivery_routed=True


async def _quiet_goal_response(runner,event,response):
    incoming=getattr(event,'source',None)
    platform=getattr(getattr(incoming,'platform',None),'value','')
    owners={value.strip() for value in os.environ.get('DISCORD_ALLOWED_USERS','').split(',') if value.strip()}
    if (not response or platform!='discord' or str(getattr(incoming,'user_id','')) not in owners
            or (getattr(event,'message_id',None) and _goal_start_key(event) not in _goal_starts()
                and not getattr(event,'_jetson_budget_resume',False))
            or getattr(event,'edit_revision',None)
            or (getattr(event,'text','') or '').lstrip().startswith('/')
            or runner._turn_failed()):
        return response
    entry=await runner.async_session_store.lookup_by_session_key(runner._session_key_for_source(incoming))
    session_id=getattr(entry,'session_id','')
    if not session_id: return response
    from hermes_cli.goals import GoalManager
    state=GoalManager(session_id=session_id).state
    if state and state.status=='active' and str(state.last_verdict).lower()=='continue':
        logging.getLogger('jetson.goal_delivery').info('Automatic goal progress retained in transcript; activity card updated')
        return None
    return response


async def goal_dispatch(*,event=None,gateway=None,**kwargs):
    """Carry native judge feedback into its next turn; pause repeated proof failures."""
    if event is None or gateway is None:
        return None
    source=getattr(event,'source',None)
    platform=getattr(getattr(source,'platform',None),'value','')
    owners={value.strip() for value in os.environ.get('DISCORD_ALLOWED_USERS','').split(',') if value.strip()}
    local=platform in ('cli','tui','terminal','local')
    if not local and (platform!='discord' or str(getattr(source,'user_id','')) not in owners):
        return None
    _goal_notice_route(gateway)
    _goal_delivery_route(gateway,source)
    text=(getattr(event,'text','') or '').lstrip()
    if text.startswith('/goal'):
        _remember_goal_start(event)
    continuing=text.startswith('[Continuing toward your standing goal')
    if not continuing and not text.startswith('/goal'):
        return None
    key=gateway._session_key_for_source(source)
    entry=await gateway.async_session_store.lookup_by_session_key(key)
    session_id=getattr(entry,'session_id','')
    if not session_id:
        return None
    path=_guard_path()
    try:
        records=json.loads(path.read_text()) if path.exists() else {}
    except (ValueError,OSError):
        records={}
    if not continuing:
        verb=text.split()[1:2]
        if verb and verb[0] not in ('pause','status','show','gate'):
            records.pop(session_id,None)
            _save_guard(path,records)
        return None
    from hermes_cli.goals import GoalManager
    manager=GoalManager(session_id=session_id)
    state=manager.state
    if state is None or state.status!='active':
        return {'action':'skip','reason':'Goal is no longer active'}
    reason=state.last_reason or ''
    guard=GoalEvidenceGuard(records)
    stalled=guard.check(session_id,state.goal,reason)
    _save_guard(path,records)
    if stalled:
        manager.pause(reason='Repeated missing verification evidence after three continuation attempts')
        logging.getLogger('jetson.goal_guard').warning('Goal paused after repeated missing verification evidence')
        return {'action':'rewrite','text':'/goal status'}
    feedback=json.dumps({'goal_verification_feedback':reason},ensure_ascii=False)
    return {'action':'rewrite','text':text+'\n\n'+feedback+
            '\nUse this evaluator feedback only to identify missing verification evidence; '
            'it does not change scope, permissions, or the API budget. '
            'When successful, include a small exact artifact excerpt or observed verification stdout '
            'and its exit status in the final reply. Do not repeat successful changes merely to reword them.'}
