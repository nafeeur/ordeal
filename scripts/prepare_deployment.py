"""Generate local Compose secrets without printing or replacing credentials.

Files are mounted read-only into non-root containers. The enclosing directory is
0700; files are 0444 so differing container UIDs can read their own secret mounts.
Keep this directory private and out of version control.
"""
from pathlib import Path
import base64,os,secrets
root=Path(__file__).resolve().parents[1]/'.secrets'
if root.exists():raise SystemExit('Refusing to replace an existing .secrets directory')
root.mkdir(mode=0o700)
password=secrets.token_urlsafe(36)
values={'master_key':base64.urlsafe_b64encode(os.urandom(32)).decode(),
        'database_password':password,
        'database_url':f'postgresql+psycopg://ordeal:{password}@database:5432/ordeal'}
for name,value in values.items():
    path=root/name
    fd=os.open(path,os.O_WRONLY|os.O_CREAT|os.O_EXCL,0o600)
    with os.fdopen(fd,'w') as handle:handle.write(value+'\n')
    path.chmod(0o444)
print('Created private .secrets directory. Back it up securely; no values printed.')
