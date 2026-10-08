"""Create a private editor key and store only its SHA-256 in local .env.

Usage: .venv/bin/python scripts/create_classification_admin.py --name Juan
Restart the API after provisioning. Never place the key in frontend env variables.
"""
import argparse
import hashlib
import json
import os
from pathlib import Path
import secrets
from dotenv import dotenv_values, set_key

parser=argparse.ArgumentParser()
parser.add_argument('--name',required=True)
args=parser.parse_args()
name=args.name.strip()
if not name or len(name)>120:parser.error('Use an editor name between 1 and 120 characters.')
root=Path(__file__).resolve().parents[1]
env=root/'.env'
keys=json.loads(dotenv_values(env).get('CLASSIFICATION_ADMIN_KEYS') or '{}')
if name in keys:parser.error('This editor already exists. Rotate/remove its hash in .env explicitly before replacing it.')
private=root/'.local'
private.mkdir(mode=0o700,exist_ok=True)
filename=private/('classification-admin-'+hashlib.sha256(name.encode()).hexdigest()[:10]+'.key')
key=secrets.token_urlsafe(48)
fd=os.open(filename,os.O_WRONLY|os.O_CREAT|os.O_EXCL,0o600)
with os.fdopen(fd,'w') as f:f.write(key+'\n')
keys[name]=hashlib.sha256(key.encode()).hexdigest()
set_key(env,'CLASSIFICATION_ADMIN_KEYS',json.dumps(keys,ensure_ascii=False))
print(f'Editor: {name}. Private key saved to {filename}. Restart the API to activate.')
