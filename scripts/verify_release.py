"""Fail early when only part of the complete release was uploaded."""
from pathlib import Path
import hashlib,json
root=Path(__file__).resolve().parents[1]
manifest=root/'release_manifest.json'
if not manifest.exists():raise SystemExit('release_manifest.json is missing. Upload the complete release to this branch.')
obj=json.loads(manifest.read_text())
failed=[]
for name,expected in obj['sha256'].items():
    path=root/name
    if not path.is_file() or hashlib.sha256(path.read_bytes()).hexdigest()!=expected:failed.append(name)
if failed:
    raise SystemExit('Release files are missing or differ from this package. Re-upload: '+', '.join(failed))
print(f'Verified complete release: {obj["release_id"]} ({len(obj["sha256"])} source files)')
