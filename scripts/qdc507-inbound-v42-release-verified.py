#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Isolated QDC507 inbound-only Pre-audio v4.2 rollout (strict CLI verification).

--stage: validates production, makes an INERT plan/AGI wrapper in LAB (no ADB/device activity)
--apply: checks exact stage and zero active calls, installs new inbound-only
         controller and wrapper, atomically changes ONLY inbound dialplan,
         reloads; automatically restores original dialplan on verification failure
--rollback: safely restores the saved exact dialplan when idle (no ADB/device calls)
--status: read-only rollout state

Original outbound _7X. route, AGI wrapper, preaudio.py, device.sh, Guard16,
SMS, and all module/driver files are not modified.

No claim is made that real device stability is proven by offline testing.
"""
import argparse
import datetime
import difflib
import hashlib
import json
import os
import pathlib
import re
import shutil
import stat
import subprocess
import sys
import tempfile

LAB = pathlib.Path('/volume2/docker/asterisk/qdc507-16k-lab')
LIVE = pathlib.Path('/volume2/docker/asterisk/qdc507-current/v0/extensions.conf')
C = 'qdc507-hardware'
SRC_CAND = LAB / 'qdc507-preaudio-v42-candidate.py'
SHA_CAND = '7981c83576cccd981558448796e4c5cc50b40af84668a079c78ff3f59efdefd7'
SHA_ORIG = '2f810a3dab4cbfdc35eb3d92e90f7db474f2c5689785dc7e5835d91959d3b690'
REMOTE_DIR = '/opt/qdc16k-preaudio-manual'
REMOTE_INBOUND = REMOTE_DIR + '/preaudio-inbound-v42.py'
REMOTE_WRAPPER = REMOTE_DIR + '/agi-wrapper-inbound-v42'
REMOTE_ORIG_WRAPPER = REMOTE_DIR + '/agi-wrapper'
REMOTE_ORIG_CODE = REMOTE_DIR + '/preaudio.py'
ROOT_WRAPPER = LAB / 'qdc507-inbound-v42-wrapper.staged'
STAGE_CONF = LAB / 'qdc507-inbound-v42.staged.conf'
STAGE_DIFF = LAB / 'qdc507-inbound-v42.diff'
MANIFEST = LAB / 'qdc507-inbound-v42.stage.json'
APPLIED = LAB / 'qdc507-inbound-v42.applied.json'
WAIT_CONTEXT = 'qdc16k-inbound-v42-wait'
CANCEL_CONTEXT = 'qdc16k-inbound-v42-cancel'
LABEL = 'qdc507-inbound-v42'

class SafetyStop(Exception): pass

def need(x, what):
    if not x: raise SafetyStop(what)

def sha(b): return hashlib.sha256(b).hexdigest()

def run(argv, timeout=20):
    p = subprocess.run(argv, stdin=subprocess.DEVNULL, stdout=subprocess.PIPE,
                       stderr=subprocess.PIPE, timeout=timeout, universal_newlines=True)
    if p.returncode != 0:
        raise SafetyStop('COMMAND_FAILED '+repr(argv[:4])+' stderr='+p.stderr[:250])
    return p.stdout

def cli(cmd): return run(['docker','exec',C,'asterisk','-rx',cmd])
def docker_cat(path): return run(['docker','exec','-u','0',C,'cat',path]).encode()

def validate_host():
    need(LAB.is_dir() and not LAB.is_symlink(), 'LAB_INVALID')
    need(LIVE.is_file() and not LIVE.is_symlink(), 'LIVE_CONF_INVALID')
    need(SRC_CAND.is_file() and not SRC_CAND.is_symlink() and sha(SRC_CAND.read_bytes())==SHA_CAND,
         'V42_CANDIDATE_SHA_MISMATCH')
    need(sha((LAB/'preaudio-original.py').read_bytes()) == SHA_ORIG, 'ORIGINAL_LAB_SHA_MISMATCH')
    need(docker_cat(REMOTE_ORIG_CODE) and sha(docker_cat(REMOTE_ORIG_CODE))==SHA_ORIG,
         'PRODUCTION_ORIGINAL_CONTROLLER_CHANGED')
    need(run(['docker','inspect','-f','{{.State.Running}}',C]).strip() == 'true', 'CONTAINER_STOPPED')
    need(run(['docker','exec',C,'cat','/etc/qdc507-mode/current']).strip() == '16k', 'MODE_NOT_16K')
    mounts = json.loads(run(['docker','inspect','-f','{{json .Mounts}}',C]))
    need(any(m.get('Type')=='bind' and m.get('Source')==str(LIVE.parent)
             and m.get('Destination')=='/etc/asterisk' for m in mounts),
         'EXPECTED_AST_CONFIG_BIND_MOUNT_MISSING')

def idle():
    s=cli('core show channels count')
    need(re.search(r'(?m)^\s*0 active channels\s*$',s) and
         re.search(r'(?m)^\s*0 active calls\s*$',s), 'NONZERO_ACTIVE_CHANNELS')

def context(text):
    a=list(re.finditer(r'(?m)^\[incoming-mobile\][ \t]*\r?$',text))
    need(len(a)==1,'INCOMING_CONTEXT_NOT_UNIQUE')
    start=a[0].end()
    m=re.search(r'(?m)^\[[^\]\r\n]+\][ \t]*\r?$',text[start:])
    return start, start+m.start() if m else len(text)

def build_config(text):
    nl='\r\n' if '\r\n' in text else '\n'
    for ctx in (WAIT_CONTEXT, CANCEL_CONTEXT):
        need(('['+ctx+']') not in text, 'INBOUND_V42_CONTEXT_ALREADY_EXISTS_'+ctx)
    beg,end=context(text)
    old=text[beg:end]
    ext=list(re.finditer(r'(?m)^[ \t]*exten[ \t]*=>[ \t]*([^,\n]+)[ \t]*,[ \t]*1[ \t]*,',old))
    need(ext,'NO_INBOUND_EXTENSIONS')
    dial=re.compile(r'(?m)^(?P<indent>[ \t]*)same[ \t]*=>[ \t]*n[ \t]*,[ \t]*Dial\(PJSIP/100,60,U\(qdc-audio-start\^\$\{UNIQUEID\}\)\)[ \t]*\r?$')
    hangup=re.compile(r'^[ \t]*same[ \t]*=>[ \t]*n[ \t]*,[ \t]*Hangup\(\)[ \t]*\r?$')
    names={'s':0,'_X.':0}
    segments=[]; cursor=0
    for i,m in enumerate(ext):
        lo=m.start(); hi=ext[i+1].start() if i+1<len(ext) else len(old)
        segments.append(old[cursor:lo])
        sec=old[lo:hi]; cursor=hi
        name=m.group(1).strip()
        if name not in names:
            segments.append(sec); continue
        names[name]+=1
        need('QDC507incomingcall' in sec and 'Set(CALLERID(name)=)' in sec,
             'INBOUND_SIGNATURE_MISSING_'+name)
        need('QDC_PA_' not in sec and 'qdc16k-' not in sec,
             'INBOUND_ALREADY_PREAUDIO_'+name)
        hit=list(dial.finditer(sec))
        need(len(hit)==1,'EXPECTED_OLD_DIAL_'+name)
        d=hit[0]; lineend=sec.find('\n',d.end()); lineend=len(sec) if lineend<0 else lineend+1
        trailing=sec[lineend:].splitlines(keepends=True)
        j=0
        while j<len(trailing) and (not trailing[j].strip() or trailing[j].lstrip().startswith(';')):
            j+=1
        need(j<len(trailing) and hangup.fullmatch(trailing[j].rstrip('\r\n')),
             'EXPECTED_HANGUP_IMMEDIATELY_AFTER_DIAL_'+name)
        indent=d.group('indent')
        def n(x): return indent+'same => n,'+x+nl
        b = (
           n('Set(QDC_PA_MODE=${TRIM(${FILE(/etc/qdc507-mode/current)})})')+
           n('GotoIf($["${QDC_PA_MODE}"="16k"]?pa16)')+
           n('Dial(PJSIP/100,60,U(qdc-audio-start^${UNIQUEID}))')+
           n('Hangup()')+
           n('(pa16),Set(QDC_PA_ID=${UNIQUEID})').replace('same => n,(pa16),','same => n(pa16),')+
           n('Set(CHANNEL(hangup_handler_push)='+CANCEL_CONTEXT+',s,1(${QDC_PA_ID}))')+
           n('Set(AGISIGHUP=yes)')+
           n('Set(AGIEXITONHANGUP=no)')+
           n('Set(QDC_PA_SUBMITTED=0)')+
           n('AGI('+REMOTE_WRAPPER+',submit,${QDC_PA_ID})')+
           n('GotoIf($["${QDC_PA_SUBMITTED}"!="1"]?pa-fail)')+
           n('Set(QDC_PA_READY=0)')+
           n('AGI('+REMOTE_WRAPPER+',wait,${QDC_PA_ID})')+
           n('GotoIf($["${QDC_PA_READY}"!="1"]?pa-fail)')+
           n('Dial(PJSIP/100,60,U('+WAIT_CONTEXT+'^${QDC_PA_ID}))')+
           n('Gosub('+CANCEL_CONTEXT+',s,1(${QDC_PA_ID}))')+
           n('(pa-fail),Hangup()').replace('same => n,(pa-fail),','same => n(pa-fail),')
        )
        segments.append(sec[:d.start()]+b+''.join(trailing[:j])+''.join(trailing[j+1:]))
    segments.append(old[cursor:])
    need(names=={'s':1,'_X.':1}, 'INBOUND_EXPECTED_TWO_EXTENSIONS')
    new_block=''.join(segments)
    need(old.count('Dial(PJSIP/100,60,U(qdc-audio-start^${UNIQUEID}))')==2,
         'EXPECTED_TWO_ORIGINAL_INBOUND_DIALS')
    need(new_block.count('Dial(PJSIP/100,60,U('+WAIT_CONTEXT+'^${QDC_PA_ID}))')==2,
         'EXPECTED_TWO_V42_INBOUND_DIALS')
    head=text[:beg]+new_block+text[end:]
    definitions=(
      nl+'['+WAIT_CONTEXT+']'+nl+
      'exten => s,1,Set(GOSUB_RESULT=ABORT)'+nl+
      ' same => n,Set(AGISIGHUP=yes)'+nl+
      ' same => n,Set(AGIEXITONHANGUP=no)'+nl+
      ' same => n,Set(QDC_PA_READY=0)'+nl+
      ' same => n,AGI('+REMOTE_WRAPPER+',wait,${ARG1})'+nl+
      ' same => n,GotoIf($["${QDC_PA_READY}"!="1"]?done)'+nl+
      ' same => n,Set(GOSUB_RESULT=)'+nl+
      ' same => n(done),Return()'+nl+
      nl+'['+CANCEL_CONTEXT+']'+nl+
      'exten => s,1,Set(QDC_PA_CLEAN=0)'+nl+
      ' same => n,AGI('+REMOTE_WRAPPER+',cancel,${ARG1})'+nl+
      ' same => n,NoOp(QDC16K inbound v42 cleanup=${QDC_PA_CLEAN})'+nl+
      ' same => n,Return()'+nl
    )
    return head.rstrip('\r\n') + nl + definitions

def build_wrapper(b):
    s=b.decode('utf-8')
    need(s.count(REMOTE_ORIG_CODE)==1, 'WRAPPER_EXPECTED_ONE_PREAUDIO_PY')
    need('QDC_LOCAL_STATE=' in s and 'QDC_DEVICE_SCRIPT=' in s and 'QDC_ADB=' in s,
         'WRAPPER_EXPECTED_PREAUDIO_ENVIRONMENT')
    return s.replace(REMOTE_ORIG_CODE,REMOTE_INBOUND).encode('utf-8')

def file_write_once(path,b,mode=0o600):
    need(not path.exists() and not path.is_symlink(), 'EXISTING_STAGE_FILE_'+path.name)
    fd=os.open(str(path),os.O_WRONLY|os.O_CREAT|os.O_EXCL,mode)
    with os.fdopen(fd,'wb') as f: f.write(b); f.flush(); os.fsync(f.fileno())

def stage():
    validate_host()
    need(not APPLIED.exists(), 'ALREADY_APPLIED_MANIFEST_PRESENT')
    raw=LIVE.read_bytes(); need(2000<len(raw)<200000,'LIVE_CONFIG_SIZE')
    source=raw.decode('utf-8')
    compiled=cli('dialplan show incoming-mobile')
    need(compiled.count('Dial(PJSIP/100,60,U(qdc-audio-start^')==2
         and 'QDC_PA_' not in compiled,'LIVE_NOT_ORIGINAL_INBOUND')
    # Prevent the staging script from accepting an unexpectedly missing outbound.
    all_dp=cli('dialplan show')
    need("'_7X.' =>" in all_dp and 'Dial(Quectel/quectel0/' in all_dp
         and 'AGI('+REMOTE_ORIG_WRAPPER+',submit,' in all_dp,
         'LOADED_OUTBOUND_PREAUDIO_MISSING')
    wrapper=docker_cat(REMOTE_ORIG_WRAPPER)
    new_wrapper=build_wrapper(wrapper)
    updated=build_config(source).encode('utf-8')
    diff=''.join(difflib.unified_diff(source.splitlines(True),updated.decode('utf-8').splitlines(True),
                                      fromfile='live/original',tofile='staged/inbound-v42'))
    spec={'version':1,'source_sha256':sha(raw),'staged_sha256':sha(updated),
          'wrapper_source_sha256':sha(wrapper),'wrapper_staged_sha256':sha(new_wrapper),
          'inbound_python_sha256':SHA_CAND,'original_preaudio_sha256':SHA_ORIG,
          'live_path':str(LIVE),'container':C}
    for p in (STAGE_CONF,STAGE_DIFF,ROOT_WRAPPER,MANIFEST):
        need(not p.exists() and not p.is_symlink(), 'STAGE_FILES_ALREADY_EXIST_'+p.name)
    file_write_once(STAGE_CONF,updated)
    file_write_once(STAGE_DIFF,diff.encode())
    file_write_once(ROOT_WRAPPER,new_wrapper)
    file_write_once(MANIFEST,(json.dumps(spec,indent=2)+'\n').encode())
    print('INBOUND_V42_STAGE_OK=1')
    print('INBOUND_CONTROLLER_ISOLATED_FROM_OUTBOUND=1')
    print('STAGED_SHA256='+sha(updated))
    print('DIFF='+str(STAGE_DIFF))
    print('NO_PRODUCTION_CHANGES=1')

def load_verified_stage():
    validate_host()
    need(MANIFEST.is_file() and not MANIFEST.is_symlink(), 'STAGE_MANIFEST_MISSING')
    j=json.loads(MANIFEST.read_text())
    need(j.get('version')==1 and j.get('container')==C and j.get('live_path')==str(LIVE),
         'STAGE_MANIFEST_INVALID')
    need(sha(LIVE.read_bytes())==j['source_sha256'], 'LIVE_CONF_CHANGED_SINCE_STAGE')
    need(sha(STAGE_CONF.read_bytes())==j['staged_sha256'], 'STAGED_CONF_CHANGED')
    need(STAGE_CONF.read_text()==build_config(LIVE.read_text()), 'STAGED_EDIT_NOT_EXPECTED')
    w=docker_cat(REMOTE_ORIG_WRAPPER)
    need(sha(w)==j['wrapper_source_sha256'],'ORIGINAL_WRAPPER_CHANGED')
    need(sha(ROOT_WRAPPER.read_bytes())==j['wrapper_staged_sha256'] and
         ROOT_WRAPPER.read_bytes()==build_wrapper(w),'STAGED_WRAPPER_CHANGED')
    return j

def copy_exact(source,target,digest,mode):
    # Extra containers paths are new; NEVER overwrite an unexpected file.
    chk=subprocess.run(['docker','exec','-u','0',C,'sh','-c',
                        'test -e "$1" || test -L "$1"','sh',target],
                       stdout=subprocess.PIPE,stderr=subprocess.PIPE)
    if chk.returncode == 0:
        need(sha(docker_cat(target)) == digest,
             'CONTAINER_NEW_PATH_ALREADY_EXISTS_WITH_DIFFERENT_CONTENT_'+target)
        return  # Exact preexisting staged file is safe to reuse.
    run(['docker','cp',str(source),C+':'+target],timeout=40)
    # Copy is verified byte-for-byte; mode is set as root to enable AGI execute.
    run(['docker','exec','-u','0',C,'chmod',format(mode,'o'),target])
    need(sha(docker_cat(target))==digest, 'CONTAINER_COPY_SHA_MISMATCH_'+target)

def replace_live(data):
    st=os.stat(str(LIVE)); temp=None
    try:
        fd,temp=tempfile.mkstemp(prefix='.qdc-inbound-v42-',dir=str(LIVE.parent))
        with os.fdopen(fd,'wb') as f:
            f.write(data);f.flush();os.fsync(f.fileno())
        os.chown(temp,st.st_uid,st.st_gid)
        shutil.copystat(str(LIVE),temp)
        os.chmod(temp,stat.S_IMODE(st.st_mode))
        os.replace(temp,str(LIVE));temp=None
    finally:
        if temp and os.path.exists(temp): os.unlink(temp)

def compiled_ok():
    """Check actual Asterisk CLI strings INCLUDING exact call tokens.

    Avoid relying on substring presence, which falsely accepted a malformed
    `${QDC_PAID}` AGI argument in earlier CLI reporting.
    """
    inbound=cli('dialplan show incoming-mobile')
    exact={
        'INBOUND_SUBMIT_ARGUMENT_CHANGED':
            ('AGI('+REMOTE_WRAPPER+',submit,${QDC_PA_ID})', 2),
        'INBOUND_PRERING_WAIT_ARGUMENT_CHANGED':
            ('AGI('+REMOTE_WRAPPER+',wait,${QDC_PA_ID})', 2),
        'INBOUND_POSTANSWER_WAIT_ARGUMENT_CHANGED':
            ('Dial(PJSIP/100,60,U('+WAIT_CONTEXT+'^${QDC_PA_ID}))', 2),
        'INBOUND_HANGUP_HANDLER_ARGUMENT_CHANGED':
            ('Set(CHANNEL(hangup_handler_push)='+CANCEL_CONTEXT+',s,1(${QDC_PA_ID}))', 2),
        'INBOUND_8K_FALLBACK_CHANGED':
            ('Dial(PJSIP/100,60,U(qdc-audio-start^${UNIQUEID}))', 2),
    }
    for error, pair in exact.items():
        value, count = pair
        need(inbound.count(value)==count, error + ': expected='+str(count))
    need('${QDC_PAID}' not in inbound, 'MALFORMED_AGI_CALL_ID')
    for name, action in ((WAIT_CONTEXT,'wait'),(CANCEL_CONTEXT,'cancel')):
        section=cli('dialplan show '+name)
        need(section.count('AGI('+REMOTE_WRAPPER+','+action+',${ARG1})')==1,
             'ISOLATED_CONTEXT_CALL_ARGUMENT_CHANGED_'+name)
    all_dp=cli('dialplan show')
    need("'_7X.' =>" in all_dp and 'Dial(Quectel/quectel0/' in all_dp
         and 'AGI('+REMOTE_ORIG_WRAPPER+',submit,' in all_dp,
         'OUTBOUND_ROUTING_CHANGED_OR_MISSING')


def apply():
    need(os.geteuid()==0,'ROOT_REQUIRED')
    j=load_verified_stage();idle()
    stamp=datetime.datetime.now(datetime.timezone.utc).strftime('%Y%m%d-%H%M%S')
    backup=LAB/('qdc507-inbound-v42-before-'+stamp+'.conf')
    file_write_once(backup,LIVE.read_bytes())
    # Copy-only actions: neither live dialplan nor existing AGI files touched.
    copy_exact(SRC_CAND,REMOTE_INBOUND,SHA_CAND,0o644)
    copy_exact(ROOT_WRAPPER,REMOTE_WRAPPER,j['wrapper_staged_sha256'],0o755)
    # If running as a non-root user inside container, 755 + 644 is readable.
    # The call-idle predicate is rechecked immediately before touching dialplan.
    idle()
    updated=False
    try:
        replace_live(STAGE_CONF.read_bytes());updated=True
        need(sha(LIVE.read_bytes())==j['staged_sha256'],'WRITTEN_CONF_SHA_MISMATCH')
        cli('dialplan reload')
        compiled_ok()
        print('EXACT_INBOUND_AGI_ARGS_VERIFIED=1')
    except Exception:
        if updated and sha(LIVE.read_bytes())==j['staged_sha256']:
            replace_live(backup.read_bytes())
            cli('dialplan reload')
            print('AUTO_RESTORED_ORIGINAL_INBOUND_DIALPLAN=1',file=sys.stderr)
        raise
    receipt=dict(j,backup_path=str(backup),applied_utc=stamp)
    file_write_once(APPLIED,(json.dumps(receipt,indent=2)+'\n').encode())
    print('INBOUND_V42_APPLIED=1')
    print('OUTBOUND_ORIGINAL_PREAUDIO_SHA256='+SHA_ORIG)
    print('BACKUP='+str(backup))
    print('INBOUND_ONLY_CHANGE=1')
    print('NO_ADB_OR_DEVICE_CALLS=1')

def rollback():
    need(os.geteuid()==0,'ROOT_REQUIRED')
    need(APPLIED.is_file() and not APPLIED.is_symlink(),'NOT_APPLIED')
    j=json.loads(APPLIED.read_text())
    need(j.get('container')==C and j.get('live_path')==str(LIVE),'INVALID_APPLY_RECEIPT')
    b=pathlib.Path(j.get('backup_path',''))
    need(b.parent==LAB and b.is_file() and not b.is_symlink(),'BACKUP_MISSING_OR_INVALID')
    need(sha(b.read_bytes())==j['source_sha256'],'BACKUP_HASH_CHANGED')
    need(sha(LIVE.read_bytes())==j['staged_sha256'],'LIVE_CHANGED_NO_OVERWRITE')
    idle()
    replace_live(b.read_bytes());cli('dialplan reload')
    inbound=cli('dialplan show incoming-mobile')
    need(inbound.count('Dial(PJSIP/100,60,U(qdc-audio-start^')==2
         and 'QDC_PA_' not in inbound,'ROLLBACK_COMPILED_NOT_ORIGINAL')
    APPLIED.unlink()
    print('ORIGINAL_INBOUND_RESTORED=1')
    print('OUTBOUND_UNCHANGED=1')
    print('NO_ADB_OR_DEVICE_CALLS=1')

def status():
    print('STAGE_EXISTS='+str(MANIFEST.is_file()))
    print('APPLIED='+str(APPLIED.is_file()))
    if LIVE.is_file():print('LIVE_SHA256='+sha(LIVE.read_bytes()))
    if SRC_CAND.is_file():print('V42_CANDIDATE_SHA256='+sha(SRC_CAND.read_bytes()))

def main():
    ap=argparse.ArgumentParser(description=__doc__)
    g=ap.add_mutually_exclusive_group(required=True)
    for action in ('stage','apply','rollback','status'):
        g.add_argument('--'+action,action='store_true')
    args=ap.parse_args()
    if args.stage:stage()
    elif args.apply:apply()
    elif args.rollback:rollback()
    else:status()

if __name__=='__main__':
    try:main()
    except (SafetyStop,OSError,ValueError,KeyError,UnicodeError,subprocess.TimeoutExpired) as exc:
        print('V42_RELEASE_STOP='+str(exc),file=sys.stderr)
        sys.exit(2)
