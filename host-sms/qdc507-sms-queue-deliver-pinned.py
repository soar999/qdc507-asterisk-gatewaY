#!/usr/bin/env python3
import json, subprocess, time, shutil
from pathlib import Path
BASE=Path('/volume2/docker/asterisk/qdc507-current/v2/sms-queue')
PENDING=BASE/'pending'; DELIVERED=BASE/'delivered'; WORK=BASE/'work'
for d in (PENDING,DELIVERED,WORK): d.mkdir(parents=True,exist_ok=True)
r=subprocess.run(['/usr/local/bin/docker','exec','qdc507-hardware','asterisk','-rx','database show registrar/contact'],capture_output=True,text=True)
contacts=[]; direct=[]
for line in r.stdout.splitlines():
    if not line.startswith('/registrar/contact/100;@') or ': ' not in line: continue
    try: obj=json.loads(line.split(': ',1)[1])
    except Exception: continue
    if int(obj.get('expiration_time','0')) <= int(time.time()): continue
    uri=obj.get('uri','')
    if not uri.startswith('sip:'): continue
    contacts.append(uri)
    if 'SIPIS' not in obj.get('user_agent','').upper(): direct.append(uri)
if not contacts: raise SystemExit(0)
uri=(direct or contacts)[-1]
for bodyfile in sorted(PENDING.glob('*.body')):
    qid=bodyfile.stem; parts={x:PENDING/f'{qid}.{x}' for x in ('body','gw','src','conv')}
    if not all(x.exists() for x in parts.values()): continue
    for key,path in parts.items(): shutil.copy2(path,WORK/key)
    (WORK/'uri').write_text(uri)
    cmd=['/usr/local/bin/docker','exec','qdc507-hardware','asterisk','-rx','channel originate Local/s@sms-deliver-queued application Wait 1']
    out=subprocess.run(cmd,capture_output=True,text=True)
    if out.returncode==0:
        dest=DELIVERED/qid; dest.mkdir(exist_ok=True)
        for key,path in parts.items(): shutil.move(str(path),str(dest/key))
        (dest/'uri').write_text(uri); (dest/'delivered_at').write_text(time.strftime('%F %T'))
