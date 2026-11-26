"""R5 observation envelope: retain exact epoch under probe lock.
No actual recovery, SMS, or device I/O. Caller provides trusted operations.
"""
from dataclasses import dataclass
from adb_coordination import gate,GateBusy
from lifecycle import verify_live_epoch,LifecycleError
from watch_adapter import transition
import time
@dataclass(frozen=True)
class Observation:
    status: str
    epoch: str=None
    sample: tuple=None

def observe(g,e,generation,idle,probe,pause):
    def once():
        try:
            with gate(g,True):
                before=verify_live_epoch(e,generation)
                if not idle():return Observation('BUSY')
                sample=tuple(probe());after=verify_live_epoch(e,generation)
                if before!=after:return Observation('LIFECYCLE_BLOCKED')
                return Observation('OBSERVED',before,sample)
        except GateBusy:return Observation('BUSY')
        except (OSError,LifecycleError):return Observation('LIFECYCLE_BLOCKED')
    first=once()
    if first.status!='OBSERVED':return first
    if first.sample[0]:return Observation('OK',first.epoch,first.sample)
    pause();second=once()
    if second.status!='OBSERVED':return Observation('SKIP_RETRY_'+second.status)
    if first.epoch!=second.epoch:return Observation('SKIP_RETRY_EPOCH_CHANGED')
    return Observation('RETRY_OK' if second.sample[0] else 'FAIL_CONFIRMED',second.epoch,second.sample)

def decide(g,e,generation,state,observation,retry_due):
    """Revalidate under gate; returned decision is not reset authorization."""
    if observation.epoch is None:
        return transition(state,observation.status,None,epoch=None,retry_due=retry_due)
    try:
        with gate(g,True):
            if verify_live_epoch(e,generation)!=observation.epoch:
                return transition(state,'SKIP_STALE_OBSERVATION',None,epoch=None,retry_due=retry_due)
            return transition(state,observation.status,observation.sample,epoch=observation.epoch,retry_due=retry_due)
    except (GateBusy,OSError,LifecycleError):
        return transition(state,'SKIP_DECISION_GATE',None,epoch=None,retry_due=retry_due)
