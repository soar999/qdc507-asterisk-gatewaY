"""Offline release transaction model. No production entry point or command runner.
Only regular files inside an explicit temporary fixture root may be edited.
Real installer must provide a verified maintenance barrier covering V43 as well
as V44, a sealed manifest, and independent runtime verification.
"""
import hashlib, json, os, pathlib, stat, tempfile
P=pathlib.Path
class Stop(RuntimeError): pass
def need(ok,msg):
    if not ok: raise Stop(msg)
def sha(b): return hashlib.sha256(b).hexdigest()
def syncdir(p):
    fd=os.open(str(p),os.O_RDONLY|os.O_DIRECTORY);os.fsync(fd);os.close(fd)
def snapshot(p):
    fd=os.open(str(p),os.O_RDONLY|os.O_NOFOLLOW)
    try:
        s=os.fstat(fd);need(stat.S_ISREG(s.st_mode) and s.st_nlink==1,'UNSAFE_FILE')
        with os.fdopen(os.dup(fd),'rb') as f:b=f.read()
        return b,(s.st_dev,s.st_ino,s.st_uid,s.st_gid,stat.S_IMODE(s.st_mode))
    finally:os.close(fd)
class Transaction:
    def __init__(self,root,changes,*,barrier,verify,activate):
        self.root=P(root).resolve();need(self.root.name.startswith('qdc-r5-fixture-') and self.root.parent==P(tempfile.gettempdir()).resolve(),'OFFLINE_FIXTURE_ONLY')
        self.changes=changes;self.barrier=barrier;self.verify=verify;self.activate=activate;self.j=self.root/'journal';self.j.mkdir(exist_ok=True)
        need(not self.j.is_symlink(),'JOURNAL_SYMLINK')
        names=[x[0] for x in changes];need(len(set(names))==len(names) and all(P(n).name==n and n not in ('.','..','journal') for n in names),'UNSAFE_CHANGE_NAMES')
    def record(self,phase,entries):
        p=self.j/'state.json';tmp=self.j/'state.tmp'
        with tmp.open('x') as f:json.dump({'phase':phase,'entries':entries},f);f.flush();os.fsync(f.fileno())
        os.replace(tmp,p);syncdir(self.j)
    def load(self):return json.loads((self.j/'state.json').read_text())
    def write(self,n,expected,content,ident):
        p=self.root/n;fd=os.open(str(p),os.O_RDWR|os.O_NOFOLLOW)
        try:
            s=os.fstat(fd);current=(s.st_dev,s.st_ino,s.st_uid,s.st_gid,stat.S_IMODE(s.st_mode));need(list(current)==list(ident) and s.st_nlink==1,'IDENTITY_DRIFT_'+n)
            with os.fdopen(os.dup(fd),'r+b') as f:
                need(sha(f.read())==expected,'CONTENT_DRIFT_'+n);need(self.barrier(),'BARRIER_LOST')
                f.seek(0);f.write(content);f.truncate();f.flush();os.fsync(f.fileno())
            need(snapshot(p)==(content,current),'READBACK_FAILED_'+n)
        finally:os.close(fd)
    def prepare(self):
        need(not (self.j/'state.json').exists(),'EXISTING_TRANSACTION');need(self.barrier(),'BARRIER_REQUIRED')
        e=[]
        # Full preflight before writing backups or target files.
        for n,h,new in self.changes:
            old,ident=snapshot(self.root/n);need(sha(old)==h,'BASELINE_DRIFT_'+n)
            e.append({'name':n,'old':h,'new':sha(new),'identity':ident})
        for (n,h,new),x in zip(self.changes,e):
            old,ident=snapshot(self.root/n);need(sha(old)==h and ident==x['identity'],'SOURCE_CHANGED_'+n)
            for suffix,b in [('old',old),('new',new)]:
                with (self.j/(n+'.'+suffix)).open('xb') as f:f.write(b);f.flush();os.fsync(f.fileno())
        syncdir(self.j);self.record('PREPARED',e)
    def apply(self):
        j=self.load();need(j['phase']=='PREPARED','NOT_PREPARED');need(self.barrier(),'BARRIER_REQUIRED');e=j['entries']
        for x in e:
            n=x['name'];b,i=snapshot(self.root/n);need(sha(b)==x['old'] and list(i)==x['identity'],'PREFLIGHT_DRIFT_'+n)
            need(sha((self.j/(n+'.new')).read_bytes())==x['new'],'STAGED_BYTES_DRIFT')
        self.record('APPLYING',e)
        # caller orders hooks before routes; last verification precedes activation.
        for x in e:self.write(x['name'],x['old'],(self.j/(x['name']+'.new')).read_bytes(),x['identity'])
        need(self.verify('new'),'NEW_RUNTIME_UNVERIFIED');need(self.barrier(),'BARRIER_LOST');self.record('VERIFIED',e)
        need(self.activate('new'),'ACTIVATION_UNCONFIRMED');self.record('APPLIED',e)
    def rollback(self):
        j=self.load();need(j['phase'] in ('PREPARED','APPLYING','VERIFIED','APPLIED','ROLLING_BACK'),'INVALID_ROLLBACK_PHASE');need(self.barrier(),'BARRIER_REQUIRED');e=j['entries']
        # Refuse unknown partially written data rather than guess; preserve backups.
        for x in e:
            n=x['name'];b,i=snapshot(self.root/n);need(sha(b) in (x['old'],x['new']) and list(i)==x['identity'],'UNKNOWN_LIVE_BYTES_'+n)
            need(sha((self.j/(n+'.old')).read_bytes())==x['old'],'BACKUP_DRIFT_'+n)
        self.record('ROLLING_BACK',e)
        for x in reversed(e):
            n=x['name'];b,i=snapshot(self.root/n)
            if sha(b)==x['new']:self.write(n,x['new'],(self.j/(n+'.old')).read_bytes(),x['identity'])
        need(self.verify('old'),'OLD_RUNTIME_UNVERIFIED');need(self.barrier(),'BARRIER_LOST');self.record('ROLLED_BACK_VERIFIED',e)
        need(self.activate('old'),'ROLLBACK_ACTIVATION_UNCONFIRMED');self.record('ROLLED_BACK',e)
