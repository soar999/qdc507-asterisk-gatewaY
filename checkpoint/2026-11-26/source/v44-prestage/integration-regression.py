"""Sequential single-device test: no actual NAS commands, no real ADB."""
import tempfile,pathlib,subprocess,copy
from nas_recovery_adapter import Adapter,CONTRACTS
from nas_watch_bridge import WatchBridge
from lifecycle import maintain,read_epoch,LifecycleError,write_marker
from adb_coordination import gate,GateBusy
G='12345678-1234-1234-1234-123456789abc:1-3.1:1:20'
G2=G[:-2]+'21'
def check(name,cond):
    assert cond,name
    print('PASS='+name,flush=True)
def expect_stop(fn):
    try:fn()
    except (LifecycleError,GateBusy):return True
    return False
with tempfile.TemporaryDirectory() as d:
    p=pathlib.Path(d);g=p/'gate';e=p/'epoch';g.touch();gen=[G];events=[];busy=[False];bad=[False];result_mode=['good'];stage_ok=[True];verify_ok=[True];pin_ok=[True]
    def initial():return maintain(g,e,kind='usb',preflight=lambda:True,run_recovery=lambda:True,verify=lambda:True,generation=lambda:gen[0],restage=lambda:True)
    initial()
    def blocked():
        assert e.read_text().startswith('BLOCKED:')
        try:
            with gate(g,False):raise AssertionError('shared lock admitted')
        except GateBusy:pass
    def run(argv,timeout):
        blocked();events.append(('run',argv,timeout));k=next(k for k,v in CONTRACTS.items() if v[1]==argv)
        lines=CONTRACTS[k][3];mode=result_mode[0]
        if mode=='partial':lines=lines[:-1]
        if mode=='substring':lines=tuple('fake '+x for x in lines)
        return subprocess.CompletedProcess(argv,2 if mode=='rc2' else 0,'\n'.join(lines)+'\n','')
    def stage(k):blocked();events.append(('stage',k));return stage_ok[0]
    def verify(k):blocked();events.append(('verify',k));return verify_ok[0]
    def pin(p,h):
        if not pin_ok[0]:raise LifecycleError('PIN_DRIFT')
    a=Adapter(g,e,run=run,generation=lambda:gen[0],idle=lambda k:not busy[0],stage=stage,verify=verify,pin=pin)
    for k in ('usb','coldboot'):
        events.clear();old=read_epoch(e);new=a.execute(k)
        check(k.upper()+'_EXACT_ORIGINAL_ENTRY_AND_POST_GUARD_RESTAGE',events[0][1]==CONTRACTS[k][1] and events[1:]==[('stage',k),('verify',k)] and old!=new)
    old=read_epoch(e);busy[0]=True;events.clear();check('ACTIVE_CALL_REFUSES_MAINTENANCE_WITHOUT_EPOCH_CHANGE',expect_stop(lambda:a.execute('usb')) and read_epoch(e)==old and not events);busy[0]=False
    pin_ok[0]=False;check('PIN_DRIFT_REFUSES_BEFORE_RECOVERY',expect_stop(lambda:a.execute('usb')) and read_epoch(e)==old and not events);pin_ok[0]=True
    for failure in ('partial','substring','rc2'):
        result_mode[0]=failure;events.clear();check('RECEIPT_REJECT_'+failure.upper(),expect_stop(lambda:a.execute('usb')) and e.read_text().startswith('BLOCKED:') and len(events)==1);initial()
    result_mode[0]='good'
    stage_ok[0]=False;check('STAGE_FAILURE_STAYS_BLOCKED',expect_stop(lambda:a.execute('coldboot')) and e.read_text().startswith('BLOCKED:'));stage_ok[0]=True;initial()
    verify_ok[0]=False;check('FINAL_VERIFY_FAILURE_STAYS_BLOCKED',expect_stop(lambda:a.execute('coldboot')) and e.read_text().startswith('BLOCKED:'));verify_ok[0]=True;initial()
    check('RESET_WITHOUT_AUTHORIZATION_REFUSED',expect_stop(lambda:a.execute('adb-reset')))
    old=read_epoch(e);initial();events.clear();check('STALE_RESET_REFUSED',expect_stop(lambda:a.execute('adb-reset',expected_epoch=old,reset_authorize=lambda:True)) and not events)
    state={};saved=[];logs=[];notices=[];cooldown=[True];probes=[]
    def probe():probes.append(1);return (not bad[0],int(bad[0]),'closed' if bad[0] else 'ok')
    def save(s):saved.append(copy.deepcopy(s))
    w=WatchBridge(g,e,generation=lambda:gen[0],idle=lambda:not busy[0],probe=probe,pause=lambda:None,save=save,log=logs.append,queue=lambda k,o:notices.append(k),retry_due=lambda s:not s.get('reset_attempted'),cooldown=lambda:cooldown[0],adapter=a)
    with gate(g,False):w.cycle(state)
    check('SKIP_HEARTBEAT_WITHOUT_PROBE_OR_STATE_CHANGE',not probes and not state and logs[-1].startswith('WATCH_V2_4_COORDINATION_SKIP'))
    bad[0]=True;events.clear();w.cycle(state)
    check('FIRST_FAILURE_ALERT_NO_RESET',state['fail_cycles']==1 and notices==['ALERT'] and not events)
    cooldown[0]=False;w.cycle(state)
    check('COOLDOWN_NO_RESET_OR_MARKER_CHANGE',not events and read_epoch(e))
    cooldown[0]=True;old=read_epoch(e);w.cycle(state)
    check('CONFIRMED_FAILURE_GUARDED_RESET_ONCE',state['reset_attempted'] and len([x for x in events if x[0]=='run'])==1 and old!=read_epoch(e))
    check('ATTEMPT_PERSISTED_BEFORE_RESET',any(x.get('reset_attempted') for x in saved))
    events.clear();w.cycle(state);w.cycle(state)
    check('NEW_EPOCH_FAILURES_DO_NOT_BYPASS_ONE_SHOT',not events and state['reset_attempted'])
    bad[0]=False;w.cycle(state);check('ONE_HEALTHY_PROBE_NOT_RECOVERED',state['alarm_active'] and state['reset_attempted']);w.cycle(state)
    check('TWO_HEALTHY_PROBES_CLEAR_ALARM',not state['alarm_active'] and not state['reset_attempted'] and notices==['ALERT','RECOVERED'])
    # Fail closed after genuinely attempted recovery; no ADB probe through BLOCKED.
    bad[0]=True;w.cycle(state);result_mode[0]='rc2';w.cycle(state);before=len(probes);w.cycle(state)
    check('FAILED_RESET_STAYS_BLOCKED_NO_PROBE',e.read_text().startswith('BLOCKED:') and before==len(probes))
print('OFFLINE_ONLY=1')
