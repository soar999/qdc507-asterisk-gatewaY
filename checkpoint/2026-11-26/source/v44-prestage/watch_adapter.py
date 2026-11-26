"""Offline watcher transition candidate for V2.4 integration.
No SMS, ADB or reset execution here. Caller owns original persistence, cooldown,
SMS outbox, and verified outer maintenance transaction. Skips are not health.
"""
from dataclasses import dataclass
@dataclass(frozen=True)
class Decision:
    log: str
    recovery_notice: bool=False
    alert: bool=False
    reset_eligible: bool=False

def transition(state, status, observation, *, epoch, retry_due):
    """Mutate only caller's in-memory state, return action eligibility.
    reset_eligible is NOT authorization; revalidate same epoch/idle/failure under
    exclusive gate; journal one-shot BEFORE dispatch. No automatic action here.
    """
    if status not in ('OK','RETRY_OK','FAIL_CONFIRMED'):
        return Decision('WATCH_V44_SKIPPED '+status)
    if observation is None or len(observation)!=3 or bool(observation[0])!=(status!='FAIL_CONFIRMED'):
        raise ValueError('INCONSISTENT_OBSERVATION')
    # Never carry a consecutive-failure count across a recovery epoch.
    if state.get('observed_epoch')!=epoch:
        state['observed_epoch']=epoch;state['fail_cycles']=0;state['ok_streak']=0
        # Keep alarm and reset journal; epoch change is not a success receipt.
    if status in ('OK','RETRY_OK'):
        state['fail_cycles']=0
        if status=='RETRY_OK':
            state['ok_streak']=0
            return Decision('SHELL_RETRY_OK '+str(observation[2]))
        state['ok_streak']=state.get('ok_streak',0)+1
        recovered=state.get('alarm_active',False) and state['ok_streak']>=2
        # Caller clears alarm only after its existing notification policy runs.
        if state['ok_streak']>=2:
            state['reset_attempted']=False
            state.pop('next_reset_retry_epoch',None)
        return Decision('SHELL_OK '+str(observation[2]),recovery_notice=recovered)
    state['ok_streak']=0
    state['fail_cycles']=state.get('fail_cycles',0)+1
    return Decision('SHELL_FAIL_CONFIRMED cycle='+str(state['fail_cycles'])+' '+str(observation[2]),alert=not state.get('alarm_active',False),reset_eligible=state['fail_cycles']>=2 and retry_due(state))

# A skip marker may indicate watcher liveness, never transport health.
def heartbeat_class(line):
    if 'WATCH_V44_SKIPPED ' in line:return 'LIVE_ONLY'
    if 'SHELL_OK ' in line or 'SHELL_RETRY_OK ' in line:return 'LIVE_HEALTHY_OBSERVATION'
    if 'SHELL_FAIL' in line:return 'LIVE_FAILED_OBSERVATION'
    return 'UNKNOWN'
