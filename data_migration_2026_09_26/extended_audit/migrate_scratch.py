from pathlib import Path
import subprocess,json,tarfile,io,hashlib,shlex
P=Path(__file__).parent
rows=[r for r in json.loads((P/'candidate_comparison.json').read_text())['candidates'] if r['path'].startswith('/tmp/') and not r['new_server_matches']]
assert len(rows)==49
old=['ssh','-o','BatchMode=yes','varmak@192.168.3.36']
raw=subprocess.check_output(old+['tar -C / --null -T - -cf -'],input=b''.join(r['path'].lstrip('/').encode()+b'\0' for r in rows))
with tarfile.open(fileobj=io.BytesIO(raw),mode='r:') as t:
 for r in rows:
  member=t.getmember(r['path'].lstrip('/'));body=t.extractfile(member).read()
  assert len(body)==r['bytes'] and hashlib.sha256(body).hexdigest()==r['sha256'],r['path']
key=str(Path.home()/'.ssh/id_ed25519_migration_192_168_3_136')
new=['ssh','-i',key,'-o','IdentitiesOnly=yes','varma@192.168.3.136']
base='/home/varma/old_server_archive_2026_09_26/additional_projects/research_scratch'
code=f'''from pathlib import Path
import sys,hashlib,tarfile,io,json
B=Path({base!r});B.mkdir(parents=True,mode=0o700,exist_ok=True);B.chmod(0o700)
raw=sys.stdin.buffer.read();expected={rows!r}
with tarfile.open(fileobj=io.BytesIO(raw),mode='r:') as t:
 for r in expected:
  name=r['path'].lstrip('/');m=t.getmember(name);assert m.isfile()
  p=B/name;assert '..' not in p.relative_to(B).parts
  body=t.extractfile(m).read();assert hashlib.sha256(body).hexdigest()==r['sha256']
  p.parent.mkdir(parents=True,exist_ok=True)
  if p.exists():assert p.read_bytes()==body
  else:
   with p.open('xb') as f:f.write(body)
  p.chmod(m.mode & 0o777)
  assert hashlib.sha256(p.read_bytes()).hexdigest()==r['sha256']
(B/'manifest.json').write_text(json.dumps(expected,indent=2))
print(json.dumps({{'files':len(expected),'bytes':sum(r['bytes'] for r in expected),'checksum_errors':0,'destination':str(B)}}))
'''
result=json.loads(subprocess.check_output(new+['python3 -c '+shlex.quote(code)],input=raw))
(P/'scratch_migration_receipt.json').write_text(json.dumps(result,indent=2));print(json.dumps(result))
