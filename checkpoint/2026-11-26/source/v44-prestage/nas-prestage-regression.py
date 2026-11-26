#!/usr/bin/env python3
"""Offline tests for new prestage inventory and private receipt only."""
import hashlib,importlib.util,json,os,pathlib,stat,tempfile,time,unittest
from unittest.mock import patch
P=pathlib.Path
s=importlib.util.spec_from_file_location('np',str(P(__file__).with_name('nas-prestage.py')))
n=importlib.util.module_from_spec(s);s.loader.exec_module(n)
class Test(unittest.TestCase):
    def setUp(self):
        self.tmp=tempfile.TemporaryDirectory();self.p=P(self.tmp.name)
    def tearDown(self):self.tmp.cleanup()
    def test_private_stage(self):
        d=n.save_private(self.p,{'one':b'abc','two':b'def'},{'activation_authorized':False})
        self.assertEqual(stat.S_IMODE(d.stat().st_mode),0o700)
        for f in d.iterdir():self.assertEqual(stat.S_IMODE(f.stat().st_mode),0o600)
        rec=json.loads((d/'PRESTAGE-ONLY.json').read_text())
        self.assertFalse(rec['activation_authorized'])
        self.assertEqual(rec['manifest_sha256'],n.sha((d/'manifest.json').read_bytes()))
        self.assertEqual(rec['sums_sha256'],n.sha((d/'SHA256SUMS').read_bytes()))
        for l in (d/'SHA256SUMS').read_text().splitlines():
            h,k=l.split('  ',1);self.assertEqual(h,n.sha((d/k).read_bytes()))
    def test_unique_stage(self):
        a=n.save_private(self.p,{'one':b'abc'},{});b=n.save_private(self.p,{'one':b'def'},{});self.assertNotEqual(a,b);self.assertEqual((a/'one').read_bytes(),b'abc')
    def test_no_receipt_on_invalid_name(self):
        with self.assertRaises(n.Stop):n.save_private(self.p,{'../escape':b'bad'},{})
        self.assertFalse(any(self.p.rglob('PRESTAGE-ONLY.json')))
    def test_no_receipt_on_interrupted_write(self):
        with patch.object(n.os,'fsync',side_effect=OSError('simulated power loss')):
            with self.assertRaises(OSError):n.save_private(self.p,{'one':b'a'},{})
        self.assertFalse(any(self.p.rglob('PRESTAGE-ONLY.json')))
    def test_read_regular(self):
        p=self.p/'f';p.write_bytes(b'abc');b,m=n.trusted_read(p);self.assertEqual(b,b'abc');self.assertEqual(m['sha256'],n.sha(b));self.assertEqual(m['ino'],p.stat().st_ino)
    def test_reject_symlink(self):
        p=self.p/'f';p.write_bytes(b'abc');q=self.p/'link';q.symlink_to(p)
        with self.assertRaises(OSError):n.trusted_read(q)
    def test_reject_hardlink(self):
        p=self.p/'f';p.write_bytes(b'abc');q=self.p/'link';os.link(p,q)
        with self.assertRaises(n.Stop):n.trusted_read(q)
    def test_heartbeat(self):
        p=self.p/'log';p.write_text('date SHELL_OK QDC_SHELL_PROBE_OK=1\n');self.assertIn('SHELL_OK',n.heartbeat(p))
    def test_stale_heartbeat(self):
        p=self.p/'log';p.write_text('date SHELL_OK QDC_SHELL_PROBE_OK=1\n');os.utime(p,(1,1))
        with self.assertRaises(n.Stop):n.heartbeat(p)
    def test_fail_not_healthy(self):
        p=self.p/'log';p.write_text('date SHELL_OK QDC_SHELL_PROBE_OK=1\ndate SHELL_FAIL_FIRST rc=1\n')
        with self.assertRaises(n.Stop):n.heartbeat(p)
    def test_scheduler_inventory(self):
        x=n.scheduler_contract('/path/qdc507-coldboot-startup-v221.py --boot\n/path/qdc507-patch-audit-v3.sh --deep\nqdc507-adb-watch-alert-memoryfix-v2.py.bak')
        self.assertEqual(x,{'coldboot':1,'audit':1,'watch':0,'usb':0})
    def test_idle_exact(self):
        for raw,expected in [(b'0 active channels\n0 active calls\n1 calls processed',True),(b'2 active channels\n1 active calls',False),(b'10 active channels\n10 active calls',False)]:
            with patch.object(n.prep,'ce',return_value=raw):self.assertEqual(n.idle(),expected)
    def test_loaded_contexts(self):
        with patch.object(n.prep,'ce',return_value=n.A.encode()):n.check_loaded()
        with patch.object(n.prep,'ce',return_value=n.B.encode()):
            with self.assertRaises(n.Stop):n.check_loaded()
if __name__=='__main__':unittest.main(verbosity=2)
