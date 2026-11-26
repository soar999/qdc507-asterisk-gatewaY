#!/usr/bin/env python3
"""Offline candidate generation only; never edits a running dialplan.
Requires both exact predeployment config files. Output is a new local directory.
"""
import hashlib,pathlib,re,argparse
P=pathlib.Path
OLD='1cf546b4ba413f34c1fd1cb4f48a326cdd1b15d9e5860990fbc3997ff10fd53a'
A='/opt/qdc16k-preaudio-manual/agi-wrapper-inbound-v43'
B='/opt/qdc16k-preaudio-manual/agi-wrapper-v44'
CONTEXTS={'internal-100','incoming-mobile','qdc16k-inbound-v42-wait','qdc16k-inbound-v42-cancel','qdc16k-outbound-v43-wait'}
def need(ok,label):
    if not ok:raise RuntimeError(label)
def stage(text, expected=OLD):
    need(hashlib.sha256(text.encode()).hexdigest()==expected,'DIALPLAN_BASELINE_CHANGED')
    current=None; seen=set();out=[];count=0
    for line in text.splitlines(True):
        m=re.match(r'^\s*\[([^]]+)\]',line)
        if m:current=m.group(1)
        if A in line:
            need(current in CONTEXTS and 'AGI('+A+',' in line,'UNEXPECTED_WRAPPER_REFERENCE')
            seen.add(current);count+=line.count(A);line=line.replace(A,B)
        out.append(line)
    need(seen==CONTEXTS,'MISSING_EXPECTED_CONTEXT:'+repr(CONTEXTS-seen))
    result=''.join(out)
    need(A not in result and result.replace(B,A)==text,'NON_WRAPPER_CHANGE')
    need('JITTERBUFFER(fixed)=80' in result,'JB80_MISSING')
    need('QDC507_V43_16K_OUTBOUND' in result,'16K_ROUTE_MISSING')
    return result,count

def main():
    p=argparse.ArgumentParser();p.add_argument('v0',type=P);p.add_argument('v0_edit',type=P);p.add_argument('output',type=P);a=p.parse_args()
    # Bytes decoded without newline normalization; refuse stale production snapshots.
    a0=a.v0.read_bytes();a1=a.v0_edit.read_bytes();need(a0==a1,'DIALPLAN_PAIR_DRIFT')
    new,n=stage(a0.decode('utf-8'));a.output.mkdir(exist_ok=False)
    for name in ('extensions-v0.candidate.conf','extensions-v0-edit.candidate.conf'):
        with (a.output/name).open('xb') as f:f.write(new.encode())
    print('WRAPPER_REFERENCES_CHANGED='+str(n));print('PRODUCTION_UNCHANGED=1')
if __name__=='__main__':main()
