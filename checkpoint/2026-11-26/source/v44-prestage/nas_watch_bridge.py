"""R7 watcher-cycle adapter. Reuses existing V2.4 state/outbox/cooldown helpers.
No live entrypoint, no ADB command here. All device effects via trusted callbacks.
A skipped sample is liveness only. Reset authorization is rechecked under gate.
"""
from datetime import datetime
from observation import observe, decide
from lifecycle import verify_live_epoch, LifecycleError
from adb_coordination import GateBusy

class WatchBridge:
    def __init__(self,g,e,*,generation,idle,probe,pause,save,log,queue,retry_due,cooldown,adapter):
        self.g,self.e=g,e
        self.generation,self.idle,self.probe,self.pause=generation,idle,probe,pause
        self.save,self.log,self.queue=save,log,queue
        self.retry_due,self.cooldown,self.adapter=retry_due,cooldown,adapter
    def cycle(self,state):
        obs=observe(self.g,self.e,self.generation,self.idle,self.probe,self.pause)
        d=decide(self.g,self.e,self.generation,state,obs,self.retry_due)
        # Existing heartbeat audit accepts WATCH_V2_, but this is NOT health.
        self.log('WATCH_V2_4_COORDINATION_SKIP '+d.log if d.log.startswith('WATCH_V44_SKIPPED') else d.log)
        self.save(state)
        if d.alert:
            try:
                self.queue('ALERT',obs)
                state['alarm_active']=True;self.save(state)
            except Exception:self.log('ALERT_QUEUE_FAILED')
        if d.recovery_notice:
            # preserve original behavior: clear alarm before notifying, never
            # reset again simply because notification failed.
            state['alarm_active']=False;self.save(state)
            try:self.queue('RECOVERED',obs)
            except Exception:self.log('RECOVERY_NOTICE_QUEUE_FAILED')
        if not d.reset_eligible:return d
        def authorize():
            if not self.retry_due(state) or not self.cooldown():return False
            if not self.idle() or self.probe()[0]:return False
            # The new probe may have raced an out-of-band USB reset.
            if verify_live_epoch(self.e,self.generation)!=obs.epoch:return False
            state['reset_attempted']=True
            state['last_reset_attempt']=datetime.now().astimezone().strftime('%Y-%m-%d %H:%M:%S %z')
            state.pop('next_reset_retry_epoch',None)
            self.save(state)
            self.log('GUARDED_RESET_INVOCATION_ONCE=1 V44')
            return True
        try:
            self.adapter.execute('adb-reset',expected_epoch=obs.epoch,reset_authorize=authorize)
            self.log('GUARDED_RESET_V44_VERIFIED=1')
        except (GateBusy,LifecycleError,OSError,RuntimeError) as exc:
            # No blind retry. A failed reset can leave epoch BLOCKED.
            self.log('WATCH_V2_4_RECOVERY_DEFER '+type(exc).__name__)
        return d
