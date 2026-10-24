#!/usr/bin/env python3
"""Local ADB build regression; no Docker daemon, ADB command or production access."""
import ast, hashlib, importlib.util, json, os, pathlib, shutil, subprocess, tempfile, unittest
R = pathlib.Path(__file__).resolve().parent
SOURCE = R.parents[1]/'build/dualmode/qdc507-dualmode-image-v2.py'
def load():
 s = importlib.util.spec_from_file_location('adbfix', str(SOURCE))
 m = importlib.util.module_from_spec(s); s.loader.exec_module(m); return m
class ADBBuildFix(unittest.TestCase):
 def test_build_run_without_adb(self):
  m = load()
  info = {'source_8k_module_sha256':'8'*64, 'source_16k_module_sha256':m.PIN16, 'source_16k_image_id':'sha256:'+'1'*64}
  recipe = m.make_dockerfile(info)
  self.assertNotIn('command -v adb', recipe)
  line = next(x[4:] for x in recipe.splitlines() if x.startswith('RUN chmod 755 /usr/local/bin/entrypoint.sh'))
  with tempfile.TemporaryDirectory() as tmp:
   root = pathlib.Path(tmp); bins = root/'bin'; bins.mkdir()
   for name in ('chmod','python3'): (bins/name).symlink_to(shutil.which(name))
   paths = ('/usr/local/bin/entrypoint.sh','/opt/qdc16k-preaudio-manual/agi-wrapper','/opt/qdc16k-preaudio-manual/prepare.py')
   for name in paths:
    p = root/name.lstrip('/'); p.parent.mkdir(parents=True, exist_ok=True); p.write_text('fixture\n'); p.chmod(0o600)
   command = line.replace('/usr/local/',str(root)+'/usr/local/').replace('/opt/',str(root)+'/opt/')
   env = dict(os.environ, PATH=str(bins))
   self.assertNotEqual(subprocess.run(['/bin/sh','-c','command -v adb'],env=env,capture_output=True).returncode,0)
   result = subprocess.run(['/bin/sh','-c',command],env=env,capture_output=True,text=True)
   self.assertEqual(result.returncode,0,result.stderr)
   for name,mode in zip(paths,(0o755,0o755,0o644)): self.assertEqual((root/name.lstrip('/')).stat().st_mode & 0o777,mode)
   (bins/'python3').unlink()
   self.assertNotEqual(subprocess.run(['/bin/sh','-c',command],env=env,capture_output=True).returncode,0)
 def test_runtime_executable_only_no_adb_execution(self):
  tree = ast.parse(SOURCE.read_text())
  facts = next(n for n in tree.body if isinstance(n,ast.FunctionDef) and n.name=='facts')
  expr = next(n for n in ast.walk(facts) if isinstance(n,ast.BinOp) and isinstance(n.left,ast.Constant) and n.left.value=='test -x /usr/local/bin/adb && sha256sum ')
  self.assertEqual(expr.right.id,'source_path')
  with tempfile.TemporaryDirectory() as tmp:
   root = pathlib.Path(tmp); adb = root/'adb'; marker = root/'must-not-execute'; slot = root/'slot'; slot.write_bytes(b'8k')
   adb.write_text('#!/bin/sh\ntouch '+str(marker)+'\nexit 99\n')
   command = expr.left.value.replace('/usr/local/bin/adb',str(adb))+str(slot)
   for mode,ok in ((0o644,False),(0o755,True)):
    adb.chmod(mode); p = subprocess.run(['/bin/sh','-c',command],capture_output=True,text=True)
    self.assertEqual(p.returncode==0,ok); self.assertFalse(marker.exists())
    if ok:self.assertEqual(p.stdout.split()[0],hashlib.sha256(b'8k').hexdigest())
   adb.unlink(); self.assertNotEqual(subprocess.run(['/bin/sh','-c',command],capture_output=True).returncode,0)
 def test_fresh_prepare_manifest_and_drift_refusal(self):
  m = load(); data = b'local synthetic 8k'; pin = hashlib.sha256(data).hexdigest()
  info = {'source_8k_module_sha256':pin,'source_16k_module_sha256':m.PIN16,'source_16k_image_id':'sha256:'+'1'*64,'live_8k_container_id':'fixture','source_8k_path':'/opt/qdc507/modes/8k/chan_quectel.so','source_8k_container_image_id':'fixture-image'}
  with tempfile.TemporaryDirectory() as tmp:
   root = pathlib.Path(tmp); old = root/'v1'; old.mkdir(); (old/'Dockerfile').write_bytes(b'old failed context\n')
   m.BUILD_DIR = root/'v2'
   # Synthetic payload: no production binaries, serial, or private configuration.
   m.PAYLOAD = root/'payload'; m.PAYLOAD.mkdir()
   for name in ('entrypoint.sh','agi-wrapper','prepare.py','device.sh','chan_quectel-16k.so'):
    (m.PAYLOAD/name).write_text('local synthetic payload\n')
   def cmd(args,**kw):
    if args[:2]==['docker','cp']: pathlib.Path(args[-1]).write_bytes(data); return ''
    self.assertEqual(args[:3],['docker','exec','fixture']); return pin+'  slot\n'
   m.cmd = cmd; m.inspect = lambda args:{'Id':'fixture','Image':'fixture-image','State':{'Running':True}}
   m.prepare(info); m.staged_verify(info)
   manifest = json.loads((m.BUILD_DIR/m.MANIFEST).read_text())
   self.assertEqual(manifest['input_files_sha256']['Dockerfile'],m.sha256(m.BUILD_DIR/'Dockerfile'))
   self.assertNotIn('command -v adb',(m.BUILD_DIR/'Dockerfile').read_text())
   with self.assertRaisesRegex(RuntimeError,'BUILD_DIRECTORY_ALREADY_EXISTS'):m.prepare(info)
   (m.BUILD_DIR/'Dockerfile').write_text(m.make_dockerfile(info)+'# drift\n')
   with self.assertRaisesRegex(RuntimeError,'BUILD_INPUT_CHANGED_Dockerfile'):m.staged_verify(info)
   self.assertEqual((old/'Dockerfile').read_bytes(),b'old failed context\n')
if __name__=='__main__': unittest.main(verbosity=2)
