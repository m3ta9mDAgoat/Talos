"""Create per-install credentials once. Never print secret values in server logs."""
from __future__ import annotations
import json,os,secrets
from pathlib import Path
root=Path(__file__).resolve().parents[1]
path=root/'.local_auth.json'
if path.exists():
    record=json.loads(path.read_text(encoding='utf-8'))
    if len(record.get('admin',''))<24 or len(record.get('worker',''))<24:
        raise SystemExit('Existing credential file is invalid; correct it privately. Never silently replace credentials.')
    print('Existing private local credentials found; not rotated.')
else:
    payload={'admin':secrets.token_urlsafe(40),'worker':secrets.token_urlsafe(40)}
    with path.open('x',encoding='utf-8') as f:json.dump(payload,f)
    try:os.chmod(path,0o600)
    except OSError:pass
    print('Created private local credentials in .local_auth.json (GITIGNORED).')
print('Open Research Lab and paste the "admin" value from .local_auth.json. Never share it here or commit it.')
