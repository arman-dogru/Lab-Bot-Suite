"""Let cron use the same native execution-capacity leases as chat.

The pinned worker calls this body inside its existing fire-claim heartbeat,
so waiting preserves scheduled-job ownership without starting tools or a model.
"""
import logging
import time

LOG=logging.getLogger('jetson.workload')
CAPACITY='MAX_CONCURRENT_SESSIONS'


def run_serial(job,original,acquire,on_cancel,sleep=time.sleep,**kwargs):
    waiting=False
    while True:
        if any(event is not None and event.is_set() for event in
               (kwargs.get('claim_lost'),kwargs.get('transport_cancel'))):
            on_cancel(job)
            return True
        lease,refusal=acquire(job)
        if lease is not None:
            try:
                return original(job,**kwargs)
            finally:
                lease.release()
        reason=getattr(refusal,'reason','UNKNOWN')
        if reason!=CAPACITY:
            raise RuntimeError('Cannot coordinate scheduled execution: '+str(reason))
        if not waiting:
            LOG.info('Scheduled job %s waiting for the shared execution slot',job.get('id',''))
            waiting=True
        sleep(2)


def install():
    from cron import scheduler
    from hermes_cli.active_sessions import try_acquire_active_session
    from hermes_cli.config import load_config
    if getattr(scheduler,'_jetson_serial_capacity',False): return
    original=scheduler._run_one_job_body
    def acquire(job):
        return try_acquire_active_session(
            session_id='cron:'+str(job['execution_id']),surface='cron',
            config=load_config(),metadata={'cron_job_id':str(job['id'])})
    def cancel(job):
        claim=job.get('fire_claim') or {}
        scheduler._record_fire_ownership_lost(job,claim.get('by'),str(job['execution_id']))
    def serial(job,**kwargs):
        return run_serial(job,original,acquire,cancel,**kwargs)
    scheduler._run_one_job_body=serial
    scheduler._jetson_serial_capacity=True
