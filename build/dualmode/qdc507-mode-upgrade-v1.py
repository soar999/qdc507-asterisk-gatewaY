#!/usr/bin/env python3
"""Existing cutover upgrade entry: prepare/verify plus explicit maintenance apply/rollback.
Prepare/verify never stop/start/rename production or invoke ADB. Apply requires
interactive root confirmation; never replays the initial 8k cutover. Each upgrade
retains its own immutable plan and real journal, plus the stopped prior container.
Failures retain the journal and never automatically stop/remove another container.
"""
import argparse, copy, hashlib, importlib.util, json, os, re, shutil, sys
from pathlib import Path
HERE=Path(__file__).resolve().parent
ROOT=Path('/volume2/docker/asterisk/qdc507-16k-lab')
PIN8='9899154c0869a7e7a62112c2cace1e3ebdf2d22a245371559fab15e912de320f'
PIN16='c3e9638d45da696096240a8c455b64a0d3c747a4bcfc41fbb22d75453c2b9fdf'

def need(ok, why):
 if not ok: raise RuntimeError(why)
def sha(p):return hashlib.sha256(Path(p).read_bytes()).hexdigest()
def load(p,n):
 s=importlib.util.spec_from_file_location(n,str(p));m=importlib.util.module_from_spec(s);s.loader.exec_module(m);return m
def read(p):
 need(p.is_file() and not p.is_symlink(),'UNSAFE_FILE_'+p.name)
 return json.loads(p.read_text())
def write(p,data):
 with p.open('x') as f:json.dump(data,f,sort_keys=True,indent=2);f.write('\n');f.flush();os.fsync(f.fileno())
 os.chmod(str(p),0o600)
def api():
 need(sha(HERE/'qdc507-mode-cutover-verify-v2.py')=='3972815c97d37ddaef666701c3045472c27649e78fd13f86e277ba87f011ffce','ORIGINAL_VERIFY_API_CHANGED')
 c=load(HERE/'qdc507-mode-cutover-v1.py','upgrade_cutover')
 v=load(HERE/'qdc507-mode-cutover-verify-v2.py','upgrade_semantic')
 need(sha(HERE/'qdc507-mode-cutover-plan-v1.py')==v.ORIGINAL_SHA,'ORIGINAL_PLAN_API_CHANGED')
 return c,v

def inventory(root):
 result={}
 for p in sorted(root.rglob('*')):
  need(not p.is_symlink(),'PROFILE_SYMLINK')
  if p.is_file():result[str(p.relative_to(root))]=sha(p)
 return result

def live_contract(c,v,plan,container):
 """Keep V2 strict semantic parser; compare actual mounts/devices, not list order."""
 d=v.normalized_draft(plan['draft'],c.DUAL)['docker_run']
 expected={}
 for item in d['mounts']:
  fields=dict(x.split('=',1) if '=' in x else (x,True) for x in item.split(','))
  need(fields.get('type')=='bind','UNSUPPORTED_MOUNT')
  expected[fields['dst']]=(fields['src'],not bool(fields.get('readonly')))
 actual={m['Destination']:(m['Source'],bool(m['RW'])) for m in container['Mounts']}
 need(actual==expected,'LIVE_MOUNTS_DRIFT')
 h=container['HostConfig'];cfg=container['Config']
 devices=sorted(x['PathOnHost']+':'+x['PathInContainer']+':'+x['CgroupPermissions'] for x in h['Devices'])
 need(devices==d['devices'],'LIVE_DEVICES_DRIFT')
 need(sorted(h.get('CapAdd') or [])==d['capabilities'],'LIVE_CAPS_DRIFT')
 need(h.get('NetworkMode')=='host' and not h.get('Privileged') and
      h.get('RestartPolicy',{}).get('Name') in ('no',''),'LIVE_SECURITY_DRIFT')
 need(cfg['Image']==c.DUAL and cfg.get('Entrypoint')==['/usr/local/sbin/qdc507-mode-boot.sh'] and
      cfg.get('Cmd')==d['entry_and_cmd'],'LIVE_BOOT_DRIFT')

def fingerprint(container):
 # Retain all configuration, not timestamps/PID/RestartCount. Docker list order is irrelevant.
 cfg=copy.deepcopy(container['Config']);host=copy.deepcopy(container['HostConfig'])
 for k in ('Binds','CapAdd','CapDrop','Devices'):
  if isinstance(host.get(k),list):host[k]=sorted(host[k],key=lambda x:json.dumps(x,sort_keys=True))
 return {'Id':container['Id'],'Image':container['Image'],'Config':cfg,'HostConfig':host,
         'Mounts':sorted(container['Mounts'],key=lambda x:x['Destination'])}

def baseline(c,v):
 state=c.state_read();need(state and state.get('phase')=='COMPLETE','CUTOVER_NOT_COMPLETE')
 _,live=c.verify_containers(state)
 need(not (c.PLAN/'mode-switch.pending.json').exists() and not (c.PLAN/'mode/.switching-current').exists(),'MODE_SWITCH_PENDING')
 original_api=load(HERE/'qdc507-mode-cutover-plan-v1.py','original_dispatch_contract')
 for action in ('start','stop'):
  need((c.PLAN/'dispatch'/(action+'.sh')).read_text()==original_api.build_dispatch(action),'ORIGINAL_DISPATCH_DRIFT')
 plan=read(c.PLAN/'plan.json')
 if not plan.get('upgrade_format'):
  need(sha(c.PLAN/'plan.json')=='03e6f87a2919ffbd04dd8bb4f47eaf409546abf2545c8e63d1bf1ea0de3f888f','ORIGINAL_FROZEN_PLAN_DRIFT')
 live_contract(c,v,plan,live)
 current_image=json.loads(c.command(['docker','image','inspect',c.DUAL]))[0]
 need(current_image['Id']==live['Image'],'FORMAL_IMAGE_TAG_ID_DRIFT')
 history=Path(plan.get('historical_snapshot_root',str(c.PLAN)))
 for name,h in plan.get('snapshots',{}).items():
  need(sha(history/'rollback'/name)==h,'HISTORICAL_ROLLBACK_SNAPSHOT_DRIFT')
 mode=(c.PLAN/'mode/current').read_text().strip();need(mode in ('8k','16k'),'MODE_INVALID')
 for mode_name,files in plan['source_profile_hashes'].items():
  need(inventory(c.PLAN/'profiles'/mode_name)==files,'UNTRACKED_PROFILE_OR_CONTENT_DRIFT_'+mode_name)
  for name,h in files.items():
   p=c.PLAN/'profiles'/mode_name/name
   need(p.is_file() and not p.is_symlink() and sha(p)==h,'SOURCE_PROFILE_DRIFT_'+mode_name+'_'+name)
 mounts={m['Destination']:Path(m['Source']) for m in live['Mounts']}
 for name,path in [('quectel.conf',mounts['/etc/asterisk']/'quectel.conf'),
                   ('modules.conf',mounts['/etc/asterisk/modules.conf'])]:
  need(sha(path)==plan['source_profile_hashes'][mode][name],'LIVE_MODE_CONFIG_DRIFT_'+name)
 # c3e is the accepted deployed 16k runtime, even if the legacy image slot is ef6c.
 h=c.command(['docker','exec',c.LIVE,'sha256sum','/usr/lib/asterisk/modules/chan_quectel.so']).split()[0]
 need(h=={'8k':PIN8,'16k':PIN16}[mode],'LIVE_ACCEPTED_DRIVER_DRIFT')
 need(c.sms_state()[0]=='active','SMS_TIMER_NOT_ACTIVE')
 usb=c.import_script(c.ROOT/'qdc507-usb-map-v1.py','upgrade_usb')
 mapping=usb.discover();probe=usb.running_container_probe(mapping)
 need(all(probe.get(k)=='YES' for k in ('at_live_char_device_matches','pcm_live_char_device_matches')),'USB_MAPPING_STALE')
 return plan,live,mode,mapping

def candidate(c,tag,plan,live):
 need(tag!=c.DUAL and re.fullmatch(r'[a-z0-9][a-z0-9._/:\-]+',tag),'NEW_INDEPENDENT_IMAGE_TAG_REQUIRED')
 image=json.loads(c.command(['docker','image','inspect',tag]))[0]
 need(image['Architecture']=='amd64','IMAGE_ARCH_CHANGED')
 cfg=image['Config'];need(cfg.get('User') in ('',None,'root','0','0:0'),'IMAGE_NOT_ROOT')
 need((cfg.get('Entrypoint') or [])+(cfg.get('Cmd') or [])==plan['draft']['image_original_entrypoint']+plan['draft']['image_original_cmd'],'IMAGE_BOOT_CHANGED')
 for k in ('Env','WorkingDir'):
  need(cfg.get(k)==live['Config'].get(k),'IMAGE_CONFIG_DRIFT_'+k)
 paths=['/opt/qdc507/modes/8k/chan_quectel.so','/opt/qdc507/modes/16k/chan_quectel.so']
 pins=[PIN8,PIN16]
 for name in ('agi-wrapper','preaudio.py','device.sh','prepare.py','entrypoint.sh'):
  paths.append('/opt/qdc16k-preaudio-manual/'+name);pins.append(sha(HERE/'qdc507-preaudio-runtime'/name))
 paths.append('/usr/local/bin/entrypoint.sh');pins.append(sha(HERE/'qdc507-preaudio-runtime/entrypoint.sh'))
 out=c.command(['docker','run','--rm','--pull=never','--network','none','--entrypoint','sha256sum',image['Id']]+paths)
 need([line.split()[0] for line in out.splitlines()]==pins,'CANDIDATE_PAYLOAD_CHANGED')
 for mode,pin in [('8k',PIN8),('16k',PIN16),('8k',PIN8)]:
  out=c.command(['docker','run','--rm','--pull=never','--network','none','-e','QDC507_AUDIO_MODE='+mode,'--entrypoint','/usr/local/sbin/qdc507-mode-boot.sh',image['Id'],'--self-test'])
  need('MODE_SELECTOR_SELF_TEST_PASSED=1' in out and 'QDC507_DRIVER_SHA256='+pin in out,'CANDIDATE_MODE_SELECTOR_FAILED')
 return image['Id']

def prepare(out,tag):
 c,v=api();need(os.geteuid()==0,'ROOT_REQUIRED')
 need(out.parent==ROOT and out.name.startswith('qdc507-mode-upgrade-') and not out.exists() and not out.is_symlink(),'FRESH_UPGRADE_PLAN_REQUIRED')
 plan,live,mode,usb=baseline(c,v);image=candidate(c,tag,plan,live)
 # Preparation never needs idle or stops services: only immutable files are copied.
 os.umask(0o077);out.mkdir(mode=0o700)
 write(out/'prepare-intent.json',{'phase':'PREPARING','source_plan':str(c.PLAN),'source_id':live['Id']})
 try:
  for sub in ('profiles','dispatch','mode'):
   shutil.copytree(str(c.PLAN/sub),str(out/sub),symlinks=False)
  source_files=inventory(c.PLAN/'profiles')
  rt=out/'profiles/16k/preaudio'
  if rt.exists():shutil.rmtree(str(rt))
  shutil.copytree(str(HERE/'qdc507-preaudio-runtime'),str(rt))
  os.chown(str(out/'profiles'),0,1000);os.chmod(str(out/'profiles'),0o750)
  for p in (out/'profiles').rglob('*'):
   os.chown(str(p),0,1000);os.chmod(str(p),0o750 if p.is_dir() or p.suffix in ('.sh','.armv7') or p.name=='agi-wrapper' else 0o640)
  # Bind traversal: the candidate root itself must be accessible to asterisk.
  os.chmod(str(out),0o711)
  for sub in ('mode','dispatch'):
   os.chmod(str(out/sub),0o755)
   for p in (out/sub).iterdir():os.chmod(str(p),0o755 if p.suffix=='.sh' else 0o644)
  data=copy.deepcopy(plan)
  data['historical_snapshot_root']=plan.get('historical_snapshot_root',str(c.PLAN))
  data['upgrade_format']='qdc507-mode-upgrade-v1';data['target_image']=tag
  data['production_container_id']=live['Id'];data['production_image']=live['Image']
  data['default_mode']=mode;data['application']='NOT_INSTALLED_NOT_EXECUTED'
  data['notes']=['Upgrade existing dual-mode container; preserve current mode; initial cutover must not be replayed.']
  args=data['draft']['argv'];args=[x.replace('src='+str(c.PLAN)+'/', 'src='+str(out)+'/') if x.startswith('type=bind,') else x for x in args]
  args[args.index(c.DUAL)]=tag;data['draft']['argv']=args
  v.normalized_draft(data['draft'],tag)
  data['source_profile_hashes']={m:inventory(out/'profiles'/m) for m in ('8k','16k')}
  data['upgrade']={'source_plan':str(c.PLAN),'source_plan_sha256':sha(c.PLAN/'plan.json'),
    'source_state_sha256':sha(c.STATE),'source_state_path':str(c.STATE),'source_profiles':source_files,
    'source_fingerprint':fingerprint(live),'source_mode':mode,'usb':usb,'candidate_image_id':image,
    'backup_name':'qdc507-hardware-before-'+out.name.removeprefix('qdc507-mode-upgrade-') if hasattr(str,'removeprefix') else 'qdc507-hardware-before-'+out.name[len('qdc507-mode-upgrade-'):],
    'previous_active':read(c.ACTIVE) if c.ACTIVE.exists() else None,
    'dispatch':inventory(out/'dispatch')}
  write(out/'plan.json',data)
  write(out/'READY.json',{'plan_sha256':sha(out/'plan.json'),'phase':'CANDIDATE_ONLY'})
  print('UPGRADE_CANDIDATE_PLAN='+str(out));print('LIVE_UNCHANGED=1')
 except BaseException:
  write(out/'FAILED.json',{'phase':'PREPARE_FAILED','action':'Keep incomplete directory; inspect; use a fresh plan name. No live mutation was attempted.'})
  raise

def verify(out):
 need(out.parent==ROOT and out.name.startswith('qdc507-mode-upgrade-') and not out.is_symlink(),'UPGRADE_PLAN_PATH_UNSAFE')
 c,v=api();data=read(out/'plan.json');ready=read(out/'READY.json')
 need(ready['plan_sha256']==sha(out/'plan.json'),'CANDIDATE_PLAN_DRIFT')
 u=data['upgrade'];need(data.get('upgrade_format')=='qdc507-mode-upgrade-v1','BAD_FORMAT')
 need(str(c.PLAN)==u['source_plan'] and sha(c.PLAN/'plan.json')==u['source_plan_sha256'] and sha(c.STATE)==u['source_state_sha256'],'SOURCE_HISTORY_CHANGED')
 plan,live,mode,usb=baseline(c,v)
 need(fingerprint(live)==u['source_fingerprint'] and mode==u['source_mode'] and usb==u['usb'],'LIVE_IDENTITY_MODE_OR_USB_DRIFT')
 need(inventory(c.PLAN/'profiles')==u['source_profiles'],'SOURCE_PROFILE_DRIFT')
 for m in ('8k','16k'):need(inventory(out/'profiles'/m)==data['source_profile_hashes'][m],'CANDIDATE_PROFILE_DRIFT')
 need(inventory(out/'dispatch')==u['dispatch'],'DISPATCH_DRIFT')
 need((out/'mode/current').read_text()==mode+'\n','CANDIDATE_MODE_DRIFT')
 need(candidate(c,data['target_image'],plan,live)==u['candidate_image_id'],'CANDIDATE_IMAGE_ID_DRIFT')
 expected=copy.deepcopy(plan['draft']);expected['argv']=[x.replace('src='+str(c.PLAN)+'/', 'src='+str(out)+'/') if x.startswith('type=bind,') else x for x in expected['argv']]
 expected['argv'][expected['argv'].index(c.DUAL)]=data['target_image']
 need(v.normalized_draft(expected,data['target_image'])==v.normalized_draft(data['draft'],data['target_image']),'UPGRADE_DRAFT_DRIFT')
 print('UPGRADE_READ_ONLY_VERIFY_OK=1');return c,data

def atomic_json(p,data):
 tmp=p.with_name(p.name+'.writing');write(tmp,data);os.replace(str(tmp),str(p))
 fd=os.open(str(p.parent),os.O_RDONLY)
 try:os.fsync(fd)
 finally:os.close(fd)

def bind_candidate(c,out,data):
 c.PLAN=out;c.STATE=out/'cutover-state.json';c.DUAL=data['target_image'];c.BACKUP=data['upgrade']['backup_name']

def activate_pointer(c,out):
 atomic_json(c.ACTIVE,{'plan':str(out),'plan_sha256':sha(out/'plan.json')})

def maintenance_required(confirm):
 need(os.geteuid()==0 and confirm and sys.stdin.isatty(),'EXPLICIT_INTERACTIVE_ROOT_MAINTENANCE_REQUIRED')

def apply(out,confirm):
 maintenance_required(confirm)
 c,data=verify(out);u=data['upgrade']
 fd=c.lock_operation()
 try:
  # A staged package alone is not permission to leave the installed manager stale.
  for name in ('qdc507-mode.py','qdc507-mode-cutover-v1.py','qdc507-mode-upgrade-v1.py'):
   need(sha(ROOT/name)==sha(HERE/name),'INSTALL_REVIEWED_SOURCE_FIRST_'+name)
  need(not (out/'cutover-state.json').exists(),'UPGRADE_STATE_EXISTS_USE_ROLLBACK')
  need(c.docker(u['backup_name']) is None,'UPGRADE_BACKUP_NAME_EXISTS')
  c.idle(c.LIVE);verify(out)  # revalidate after acquiring the original shared lock
  bind_candidate(c,out,data)
  state={'format':'qdc507-dualmode-8k-cutover-v1','upgrade_format':'qdc507-mode-upgrade-v1',
         'old_id':data['production_container_id'],'backup_name':c.BACKUP,
         'dual_image':c.DUAL,'new_image_id':u['candidate_image_id'],'phase':'PREPARED'}
  c.atomic_state(state)
  try:
   c.command(['systemctl','stop',c.TIMER]);c.phase(state,'SMS_PAUSED');c.wait_sms_idle()
   current=c.idle(c.LIVE)
   need(current['Id']==state['old_id'],'LIVE_ID_CHANGED_BEFORE_STOP')
   c.command(['docker','stop','-t','12',c.LIVE]);c.phase(state,'ORIGINAL_STOPPED')
   c.command(['docker','rename',c.LIVE,c.BACKUP]);c.phase(state,'ORIGINAL_RENAMED')
   need(json.loads(c.command(['docker','image','inspect',c.DUAL]))[0]['Id']==state['new_image_id'],'TAG_DRIFT_BEFORE_CREATE')
   new_id=c.command(data['draft']['argv'],timeout=90)
   need(re.fullmatch('[0-9a-f]{64}',new_id),'NEW_CONTAINER_ID_INVALID')
   state['new_id']=new_id;c.phase(state,'NEW_CONTAINER_STARTED')
   ready(c,u['source_mode']);c.verify_containers(state)
   c.command(['systemctl','start',c.TIMER]);need(c.sms_state()[0]=='active','SMS_NOT_RESUMED')
   c.phase(state,'COMPLETE');activate_pointer(c,out)
   print('UPGRADE_COMPLETE_CURRENT_MODE='+u['source_mode'])
  except BaseException as e:
   state['failed_from_phase']=state['phase'];state['error']=type(e).__name__
   c.phase(state,'FAILED_REQUIRES_EXPLICIT_ROLLBACK')
   print('NO_AUTOMATIC_STOP_REMOVE_OR_ROLLBACK; inspect journal then explicit --rollback')
   raise
 finally:os.close(fd)

def ready(c,mode):
 import time
 end=time.monotonic()+75
 while time.monotonic()<end:
  try:
   c.idle(c.LIVE,timeout=6)
   got=c.command(['docker','exec',c.LIVE,'sha256sum','/usr/lib/asterisk/modules/chan_quectel.so'],timeout=8).split()[0]
   need(got=={'8k':PIN8,'16k':PIN16}[mode],'NEW_MODE_DRIVER_MISMATCH');return
  except Exception:time.sleep(1)
 raise RuntimeError('UPGRADE_READINESS_FAILED')

def rollback(out,confirm):
 maintenance_required(confirm);c,_=api();fd=c.lock_operation()
 try:
  data=read(out/'plan.json');need(read(out/'READY.json')['plan_sha256']==sha(out/'plan.json'),'PLAN_CHANGED')
  u=data['upgrade'];state=read(out/'cutover-state.json')
  need(state.get('upgrade_format')=='qdc507-mode-upgrade-v1' and state['old_id']==data['production_container_id'],'UPGRADE_STATE_INVALID')
  need(state['backup_name']==u['backup_name'] and state['new_image_id']==u['candidate_image_id'],'UPGRADE_STATE_IDENTITY_DRIFT')
  need(sha(Path(u['source_plan'])/'plan.json')==u['source_plan_sha256'] and sha(Path(u['source_state_path']))==u['source_state_sha256'],'OLD_HISTORY_DRIFT')
  # Do not restore old runtime under a subsequently switched mode/configuration.
  need((out/'mode/current').read_text().strip()==u['source_mode'],'ROLLBACK_REQUIRES_ORIGINAL_UPGRADE_MODE')
  oldplan=read(Path(u['source_plan'])/'plan.json')
  mounts={m['Destination']:Path(m['Source']) for m in u['source_fingerprint']['Mounts']}
  for name,p in [('quectel.conf',mounts['/etc/asterisk']/'quectel.conf'),('modules.conf',mounts['/etc/asterisk/modules.conf'])]:
   need(sha(p)==oldplan['source_profile_hashes'][u['source_mode']][name],'ROLLBACK_SHARED_CONFIG_DRIFT')
  live=c.docker(c.LIVE);old=c.docker(u['backup_name'])
  if live and live['Id']==state['old_id']:
   need(old is None,'DUPLICATE_OLD_IDENTITY')
  else:
   need(old and old['Id']==state['old_id'] and old['Image']==u['source_fingerprint']['Image'] and not old['State']['Running'],'OLD_BACKUP_IDENTITY_CHANGED')
   if live:
    need(live['Id']==state.get('new_id') and live['Image']==state['new_image_id'],'UNKNOWN_LIVE_CONTAINER_REFUSE')
    if live['State']['Running']:c.idle(c.LIVE)
  c.command(['systemctl','stop',c.TIMER]);c.wait_sms_idle()
  bind_candidate(c,out,data);c.phase(state,'ROLLBACK_PREPARED')
  if live and live['Id']!=state['old_id']:
   if live['State']['Running']:c.idle(c.LIVE);c.command(['docker','stop','-t','12',c.LIVE])
   # Preserve failed candidate container for inspection; never docker rm.
   failed=u['backup_name']+'-candidate'
   need(c.docker(failed) is None,'FAILED_CANDIDATE_NAME_EXISTS')
   c.command(['docker','rename',c.LIVE,failed])
  if not live or live['Id']!=state['old_id']:c.command(['docker','rename',u['backup_name'],c.LIVE])
  current=c.docker(c.LIVE);need(current and current['Id']==state['old_id'],'RESTORE_ID_MISMATCH')
  if not current['State']['Running']:c.command(['docker','start',c.LIVE],timeout=90)
  ready(c,u['source_mode']);c.command(['systemctl','start',c.TIMER])
  need(c.sms_state()[0]=='active','RESTORED_SMS_NOT_ACTIVE')
  if u['previous_active'] is not None:atomic_json(c.ACTIVE,u['previous_active'])
  elif c.ACTIVE.exists():
   need(read(c.ACTIVE).get('plan')==str(out),'ACTIVE_POINTER_UNKNOWN');c.ACTIVE.unlink()
  c.phase(state,'PREVIOUS_DUALMODE_RECOVERED');print('PREVIOUS_DUALMODE_RESTORED=1')
 finally:os.close(fd)

def main():
 p=argparse.ArgumentParser(description=__doc__);g=p.add_mutually_exclusive_group(required=True)
 for name in ('prepare','verify','apply','rollback'):g.add_argument('--'+name,action='store_true')
 p.add_argument('--plan-dir',type=Path,required=True);p.add_argument('--image');p.add_argument('--confirm-maintenance',action='store_true')
 a=p.parse_args()
 if a.prepare:need(a.image,'CANDIDATE_IMAGE_REQUIRED');prepare(a.plan_dir,a.image)
 elif a.verify:verify(a.plan_dir)
 elif a.apply:apply(a.plan_dir,a.confirm_maintenance)
 else:rollback(a.plan_dir,a.confirm_maintenance)
if __name__=='__main__':
 try:main()
 except Exception as e:print('UPGRADE_STOPPED='+type(e).__name__+':'+str(e)[:240]);sys.exit(2)
