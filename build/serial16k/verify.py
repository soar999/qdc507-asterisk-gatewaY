from pathlib import Path
import hashlib,json,sys
D=Path(__file__).resolve().parent
root=Path(sys.argv[1]) if len(sys.argv)>1 else D/'src'
expected=json.loads((D/'source-manifest.json').read_text())
for rel,sha in expected.items():
 assert hashlib.sha256((root/rel).read_bytes()).hexdigest()==sha,rel
assert not (root/'src/qdc_release.h').exists()
print('SOURCE_MANIFEST_VERIFIED',len(expected))
