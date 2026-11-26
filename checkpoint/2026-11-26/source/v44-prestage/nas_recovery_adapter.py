"""R7 source-pinned NAS recovery adapter candidate (library, no live CLI).
The caller must have installed coordinated routes and watcher, and supply a
verified idle/admission barrier, exact ADB client and post-recovery verifier.
Never modifies a sealed V22/V221 source. Never automatically retries recovery.
"""
import hashlib, pathlib, stat, re
from lifecycle import maintain, verify_live_epoch, LifecycleError
from adb_coordination import gate, GateBusy
P=pathlib.Path
LAB=P('/volume2/docker/asterisk/qdc507-16k-lab')
BASE=P('/volume2/docker/asterisk/qdc507-current')
PYTHON='/usr/bin/python3.8'
# Exact user-supplied production hashes, not redacted repository bytes.
PINS={
 'usb_entry':(BASE/'recovery/qdc507-usb-recover.sh','fb4025303fa51e8a3cac03039ab516c22f8cba5ba0bdcf902910d9f481e6d6d4'),
 'usb_inner':(LAB/'qdc507-usb-recover-memoryfix-v22.sh','a86b873982209df25a51ee3bf370b2cc651b80d9d4e5a9239cc940aee61826a4'),
 'startup':(LAB/'qdc507-coldboot-startup-v221.py','4c88ca59502c85a022148664ecbcc602c626bda3616944ef75dcd637643caa55'),
 'cold_inner':(LAB/'qdc507-coldboot-memoryfix-v221.py','cb12e806767ede5007b484b598455d60388a1989e09be9b5ac10f0bb48f09756'),
 'adb_reset':(LAB/'qdc507-adb-recover-once-memoryfix-v2.py','9d656bea91419f5a46e3e4a4ac6b85163f61d67929f48030b3e2292e3c5888a6'),
 'audit_inner':(LAB/'qdc507-memoryfix-v22-audit.py','3eae922542c792ec5a9080c5d9a6aa96a96da868ed1bec6a5260ca85715b2469'),
}
# Exact success lines; substring success in diagnostic text is not a receipt.
CONTRACTS={
 'usb':(('usb_entry','usb_inner'),('/bin/sh',str(PINS['usb_entry'][0]),'--recover'),1100,('USB_SIM_SWAP_GUARD16_V2_RECOVERY_COMPLETE=1','V43_POST_USB_RESTAGE_OK=1','SAME_PRODUCTION_CONTAINER_AND_USB_BASELINE_PRESERVED=1')),
 'coldboot':(('startup','cold_inner'),(PYTHON,'-I','-B',str(PINS['startup'][0]),'--boot'),1250,('V221_COLDBOOT_COMPLETE=1',)),
 'adb-reset':(('adb_reset',),(PYTHON,'-I','-B',str(PINS['adb_reset'][0]),'--reset'),180,('ADB_USB_RESET_RECOVERY_OK=1','GUARD16_V2_OK=1')),
}
def check_pin(path, digest):
    s=path.lstat()
    if not stat.S_ISREG(s.st_mode) or s.st_uid!=0 or s.st_nlink!=1 or s.st_mode&0o022:
        raise LifecycleError('UNTRUSTED_PIN_'+path.name)
    if hashlib.sha256(path.read_bytes()).hexdigest()!=digest:
        raise LifecycleError('PIN_DRIFT_'+path.name)

def receipt(result, required):
    return result.returncode==0 and set(required)<=set(result.stdout.splitlines())

class Adapter:
    def __init__(self, g,e, *, run, generation, idle, stage, verify, pin=check_pin):
        self.g,self.e=g,e
        self.run,self.generation,self.idle=run,generation,idle
        self.stage,self.verify,self.pin=stage,verify,pin
    def execute(self, kind, *, expected_epoch=None, reset_authorize=None):
        if kind not in CONTRACTS:raise LifecycleError('INVALID_RECOVERY_KIND')
        if kind=='adb-reset' and (expected_epoch is None or reset_authorize is None):
            raise LifecycleError('RESET_REQUIRES_CURRENT_OBSERVATION_AND_AUTHORIZATION')
        keys,argv,timeout,markers=CONTRACTS[kind]
        def preflight():
            for key in (*keys,'audit_inner'):self.pin(*PINS[key])
            if not self.idle(kind):return False
            if expected_epoch is not None:
                if verify_live_epoch(self.e,self.generation)!=expected_epoch:return False
            # Check cooldown, original idle pins and current confirmed failure;
            # callback persists watcher attempt BEFORE returning true.
            if reset_authorize is not None and not reset_authorize():return False
            return True
        def recovery():
            # The existing helper has its own locks and cooldown. Do not
            # acquire those locks externally, and never weaken its guards.
            for key in keys:self.pin(*PINS[key])
            return receipt(self.run(argv,timeout),markers)
        def stage():
            # maintain already holds the outer exclusive lock, marker BLOCKED.
            # Original V22/V221 has already completed Guard16 restoration.
            return self.stage(kind)
        def verify():
            self.pin(*PINS['audit_inner'])
            return self.verify(kind)
        return maintain(self.g,self.e,kind=kind,preflight=preflight,run_recovery=recovery,
                        verify=verify,generation=self.generation,restage=stage)
