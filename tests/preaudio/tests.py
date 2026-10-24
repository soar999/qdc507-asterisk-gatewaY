#!/usr/bin/env python3
"""No network: fake adb executes real device.sh in private device directories."""
import hashlib, os, pathlib, select, shutil, signal, subprocess, sys, time, unittest
BASE = pathlib.Path(__file__).resolve().parent
ART = BASE / 'test-artifacts'
ART.mkdir(exist_ok=True)
ADB = BASE / 'fake-adb.py'
BRIDGE = BASE / 'fake-bridge.sh'

class RuntimeTests(unittest.TestCase):
    def setUp(self):
        self.root = ART / self._testMethodName
        if self.root.exists(): shutil.rmtree(self.root)
        self.root.mkdir()
        self.env = os.environ.copy()
        self.env.update(QDC_TEST_INTERPRETERS='1', QDC_RUNTIME_ENABLED='exclusive-short-test', QDC_ADB=str(ADB),
            QDC_SERIAL='FAKE-LOCAL-ONLY', QDC_DEVICE_SCRIPT=str(BASE/'device.sh'),
            QDC_DEVICE_ROOT=str(self.root/'device'), QDC_BIN_ROOT=str(self.root/'bin'),
            QDC_LOCAL_STATE=str(self.root/'host'), QDC_TEST_PCM_ROOT=str(self.root/'pcm'),
            QDC_TEST_ENABLE=str(self.root/'enable'))
        (self.root/'enable').write_text('0\n')
        (self.root/'bin').mkdir()
        for name in ('bridge-d4', 'bridge-d5'):
            shutil.copyfile(BRIDGE, self.root/'bin'/name)
            (self.root/'bin'/name).chmod(0o700)
        for n in ('4p','4c','5p','6c'):
            p = self.root/'pcm'/('pcm'+n)/'sub0'; p.mkdir(parents=True)
            (p/'hw_params').write_text('closed\n')
            (p/'status').write_text('state: CLOSED\n')
        self.tokens = set()

    def tearDown(self):
        for t in self.tokens:
            self.dev('cancel', t)
        # Only local test processes, including tests intentionally quarantined.
        for p in (self.root/'device').glob('tokens/*/*.pid'):
            try:
                pid, birth = p.read_text().split()
                stat = pathlib.Path('/proc/'+pid+'/stat').read_text().rsplit(') ',1)[1].split()
                if stat[19] == birth: os.kill(int(pid), signal.SIGKILL)
            except (OSError, ValueError): pass
        for p in (self.root/'host').glob('*/worker.pid'):
            try: os.kill(int(p.read_text().split()[0]), signal.SIGTERM)
            except (OSError, ValueError): pass

    def dev(self, action, token, env=None):
        self.tokens.add(token)
        return subprocess.run(['/bin/sh', str(BASE/'device.sh'), self.env['QDC_DEVICE_ROOT'],
            self.env['QDC_BIN_ROOT'], action, token], env=env or self.env,
            capture_output=True, text=True, timeout=12)

    def launch(self, t):
        self.tokens.add(t)
        return subprocess.Popen(['/bin/sh', str(BASE/'device.sh'),self.env['QDC_DEVICE_ROOT'],
            self.env['QDC_BIN_ROOT'],'launch',t],env=self.env,
            stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)

    def agi(self, action, uid='call', env=None, hup=False, reply=b'200 result=1\n'):
        t = hashlib.sha256(uid.encode()).hexdigest(); self.tokens.add(t)
        p = subprocess.Popen([sys.executable,str(BASE/'preaudio.py'),action,uid],
            env=env or self.env, stdin=subprocess.PIPE, stdout=subprocess.PIPE,
            stderr=subprocess.PIPE, bufsize=0)
        p.stdin.write(b'agi_request: preaudio.py\nagi_channel: FAKE\n\n')
        transcript=[]; start=time.monotonic(); sent=False
        while p.poll() is None:
            if hup and not sent and time.monotonic()-start>.15:
                p.send_signal(signal.SIGHUP); sent=True
            if time.monotonic()-start>25: p.kill(); self.fail('AGI unbounded')
            if select.select([p.stdout],[],[],.03)[0]:
                line=p.stdout.readline()
                if line:
                    transcript.append(line.decode().strip())
                    try: p.stdin.write(reply)
                    except BrokenPipeError: pass
        p.wait(); p.stdin.close(); p.stdout.close(); p.stderr.close()
        (self.root/(action+'-'+uid+'.transcript')).write_text('\n'.join(transcript))
        return transcript,time.monotonic()-start,t

    def clean(self,t):
        r=self.dev('cancel',t); self.assertEqual(r.returncode,0,r.stdout+r.stderr)
        self.assertFalse((self.root/'device'/'owner').exists())
        for p in (self.root/'pcm').glob('*/sub0/hw_params'):
            self.assertEqual(p.read_text().strip(),'closed')

    def test_fast_submit_and_instant_answer(self):
        lines,elapsed,t=self.agi('submit')
        self.assertIn('SET VARIABLE QDC_PA_SUBMITTED "1"',lines)
        self.assertLess(elapsed,1.8)
        self.assertFalse((self.root/'device'/'tokens'/t/'ready').exists())
        lines,waited,_=self.agi('wait')
        self.assertIn('SET VARIABLE QDC_PA_READY "1"',lines)
        self.assertGreater(waited,.1)
        self.clean(t)
        (self.root/'timing.txt').write_text(f'submit={elapsed:.3f}s ready_wait={waited:.3f}s\n')

    def test_cancel_preparation_points(self):
        for i,delay in enumerate((0,.05,.5,1.05,1.5,2.1)):
            t='phase'+str(i); p=self.launch(t); time.sleep(delay)
            self.clean(t); p.wait(timeout=6); time.sleep(.15)
            self.assertNotEqual(self.dev('query',t).returncode,0)
            self.assertNotEqual(self.dev('launch',t).returncode,0)

    def test_late_adb_launch(self):
        env=self.env.copy(); env['QDC_FAKE_LAUNCH_DELAY']='2'
        lines,_,t=self.agi('submit',env=env,hup=True)
        self.assertNotIn('SET VARIABLE QDC_PA_SUBMITTED "1"',lines)
        time.sleep(2.4)
        self.assertFalse((self.root/'device'/'tokens'/t/'accepted').exists())
        self.clean(t)

    def test_worker_exit_ready_no_periodic_adb(self):
        self.env['QDC_FAKE_ADB_LOG']=str(self.root/'adb.log')
        _,_,t=self.agi('submit')
        c=self.root/'host'/t
        deadline=time.monotonic()+6
        while not (c/'worker.done').exists() and time.monotonic()<deadline:
            time.sleep(.05)
        self.assertTrue((c/'worker.done').exists())
        self.assertEqual((c/'launch.confirmed').read_text(),t)
        before=(self.root/'adb.log').read_text()
        time.sleep(9)  # Exceeds removed lease; no host activity permitted.
        self.assertEqual((self.root/'adb.log').read_text(),before)
        self.assertNotIn(' beat ',before)
        self.assertFalse((self.root/'device'/'tokens'/t/'heartbeat').exists())
        lines,_,_=self.agi('wait')
        self.assertIn('SET VARIABLE QDC_PA_READY "1"',lines)
        calls=(self.root/'adb.log').read_text()
        self.assertEqual(calls.count(' launch '),1)  # U did not restart.
        self.clean(t)

    def test_worker_lost_during_preparation_fails_closed(self):
        _,_,t=self.agi('submit')
        c=self.root/'host'/t
        self.assertFalse((c/'launch.confirmed').exists())
        pid=int((c/'worker.pid').read_text().split()[0])
        os.kill(pid,signal.SIGKILL)
        lines,_,_=self.agi('wait')
        self.assertNotIn('SET VARIABLE QDC_PA_READY "1"',lines)
        self.clean(t)

    def test_caller_hangup_dead_agi_repeat_cancel(self):
        _,_,t=self.agi('submit')
        self.agi('wait')
        for _ in range(2):
            p=subprocess.run([sys.executable,str(BASE/'preaudio.py'),'cancel','call'],
                env=self.env,stdin=subprocess.DEVNULL,stdout=subprocess.DEVNULL,
                stderr=subprocess.PIPE,timeout=15)
            self.assertEqual(p.returncode,0,p.stderr)
            self.assertFalse((self.root/'device'/'owner').exists())
            for f in (self.root/'pcm').glob('*/sub0/hw_params'):
                self.assertEqual(f.read_text().strip(),'closed')
        self.assertTrue((self.root/'host'/t/'cancel.result').exists())

    def test_agi_result_10_rejected(self):
        lines,_,t=self.agi('submit',reply=b'200 result=10\n')
        self.assertNotIn('SET VARIABLE QDC_PA_SUBMITTED "1"',lines)
        self.assertFalse((self.root/'host'/t/'submitted').exists())
        self.assertIn('AGI rejected',(self.root/'host'/t/'agi.error').read_text())

    def test_busy_noanswer_repeat_cancel(self):
        for uid in ('BUSY','NOANSWER'):
            _,_,t=self.agi('submit',uid)
            lines,_,_=self.agi('cancel',uid)
            self.assertIn('SET VARIABLE QDC_PA_CLEAN "1"',lines)
            self.clean(t); self.clean(t)

    def test_old_stop_new_owner(self):
        p=self.launch('old'); time.sleep(.15); self.clean('old'); p.wait()
        p=self.launch('new'); time.sleep(.15)
        r=self.dev('cancel','old')
        self.assertIn('STALE_FENCED old',r.stdout)
        self.assertEqual((self.root/'device'/'owner').read_text().strip(),'new')
        self.assertEqual(self.dev('query','new').returncode,0)
        self.clean('new'); p.wait()

    def test_prepare_failure(self):
        self.env['QDC_FAKE_FAIL_D4']='1'
        lines,_,t=self.agi('submit')
        lines,_,_=self.agi('wait')
        self.assertNotIn('SET VARIABLE QDC_PA_READY "1"',lines)
        self.clean(t)

    def test_cleanup_unconfirmed_retains_lock(self):
        p=self.launch('bad'); time.sleep(.15)
        self.env['QDC_FAKE_STICKY']='1'
        # Force safety predicate failure independent of bridge cleanup.
        (self.root/'enable').write_text('1\n')
        r=self.dev('cancel','bad')
        self.assertNotEqual(r.returncode,0)
        self.assertTrue((self.root/'device'/'lock').exists())
        self.assertTrue((self.root/'device'/'owner').exists())
        self.assertNotEqual(self.dev('launch','other').returncode,0)
        p.wait(timeout=6)

    def test_agi_sighup_wait(self):
        _,_,t=self.agi('submit')
        lines,_,_=self.agi('wait',hup=True)
        self.assertNotIn('SET VARIABLE QDC_PA_READY "1"',lines)
        self.clean(t)

    def test_transport_loss_cancel_unknown(self):
        _,_,t=self.agi('submit')
        env=self.env.copy(); env['QDC_FAKE_OFFLINE']='1'
        lines,_,_=self.agi('cancel',env=env)
        self.assertIn('SET VARIABLE QDC_PA_CLEAN "0"',lines)
        self.assertIn('CLEANUP_UNCONFIRMED',(self.root/'host'/t/'fault').read_text())
        self.clean(t)

    def test_lock_timeout_tombstones_without_recovery(self):
        lock=self.root/'device'/'lock'; lock.mkdir(parents=True)
        r=self.dev('cancel','blocked')
        self.assertNotEqual(r.returncode,0)
        self.assertTrue(lock.exists())
        self.assertTrue((self.root/'device'/'tokens'/'blocked'/'cancel').exists())
        # Test-owned artificial lock only; runtime never performs this recovery.
        lock.rmdir()
        self.assertNotEqual(self.dev('launch','blocked').returncode,0)

    def clock_setup(self):
        # Explicit test-only clock; production clock remains uptime.
        clock = self.root/'clock'
        clock.write_text('1000\n')
        self.env['QDC_TEST_CLOCK'] = str(clock)
        return clock

    def wait_file(self, path):
        end = time.monotonic()+6
        while not path.exists() and time.monotonic()<end:
            time.sleep(.05)
        self.assertTrue(path.exists(), str(path))

    def test_no_call_deadline_manual_and_hangup_cancel(self):
        clock = self.clock_setup()
        for uid, via_hangup in (('manual', False), ('hangup', True)):
            clock.write_text('1000\n')
            _,_,t = self.agi('submit', uid)
            lines,_,_ = self.agi('wait', uid)
            self.assertIn('SET VARIABLE QDC_PA_READY "1"', lines)
            c = self.root/'device'/'tokens'/t
            self.wait_file(c/'ready')
            for elapsed in (151, 3600):
                # Atomic replacement avoids a partial clock read by supervisor.
                tmp = self.root/'clock.next'; tmp.write_text(str(1000+elapsed)+'\n')
                tmp.replace(clock)
                time.sleep(.7)
                self.assertEqual(self.dev('query',t).stdout.strip(), 'READY '+t)
                self.assertFalse((c/'cancel').exists())
                self.assertFalse((c/'fault').exists())
                self.assertFalse((c/'supervisor.done').exists())
            # A post-ready predicate failure must not reuse startup's deadline.
            status = self.root/'pcm'/'pcm4p'/'sub0'/'status'
            status.write_text('state: CLOSED\n'); time.sleep(.7)
            self.assertEqual(self.dev('query',t).stdout.strip(), 'ACCEPTED '+t)
            self.assertFalse((c/'cancel').exists())
            self.assertFalse((c/'fault').exists())
            status.write_text('state: RUNNING\n')
            self.assertEqual(self.dev('query',t).stdout.strip(), 'READY '+t)
            if via_hangup:
                p = subprocess.run([sys.executable,str(BASE/'preaudio.py'),'cancel',uid],
                    env=self.env,stdin=subprocess.DEVNULL,stdout=subprocess.DEVNULL,
                    stderr=subprocess.PIPE,timeout=15)
                self.assertEqual(p.returncode,0,p.stderr)
                self.assertFalse((self.root/'device'/'owner').exists())
                self.assertTrue((c/'clean').exists())
            self.clean(t)
            self.assertTrue((c/'cancel').exists())

    def test_initial_ready_timeout_preserved(self):
        clock = self.clock_setup()
        p = self.launch('notready'); p.wait(timeout=6)
        c = self.root/'device'/'tokens'/'notready'
        self.wait_file(c/'d5.pid')
        (self.root/'pcm'/'pcm4p'/'sub0'/'status').write_text('state: CLOSED\n')
        time.sleep(1.3)  # Let initial deadline start at the injected 1000.
        self.assertFalse((c/'ready').exists())
        tmp=self.root/'clock.next'; tmp.write_text('1011\n'); tmp.replace(clock)
        self.wait_file(c/'fault'); self.wait_file(c/'clean')
        self.assertEqual((c/'fault').read_text().strip(),'READY_TIMEOUT')
        self.clean('notready')

    def test_bridge_exit_after_long_call_cleans(self):
        clock = self.clock_setup()
        p=self.launch('exit'); p.wait(timeout=6)
        c=self.root/'device'/'tokens'/'exit'
        self.wait_file(c/'ready')
        tmp=self.root/'clock.next'; tmp.write_text('4600\n'); tmp.replace(clock)
        time.sleep(.7)
        self.assertEqual(self.dev('query','exit').stdout.strip(),'READY exit')
        pid=int((c/'d4.pid').read_text().split()[0])
        os.kill(pid,signal.SIGTERM)
        self.wait_file(c/'fault'); self.wait_file(c/'clean')
        self.assertEqual((c/'fault').read_text().strip(),'BRIDGE_EXIT')
        self.clean('exit')

if __name__=='__main__': unittest.main(verbosity=2)
