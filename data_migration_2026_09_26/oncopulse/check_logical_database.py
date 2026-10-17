import subprocess,shlex,json
from pathlib import Path
code='''import sqlite3,hashlib,json,contextlib
with contextlib.closing(sqlite3.connect(PATH+'?mode=ro',uri=True)) as c:
 c.execute('BEGIN')
 h=hashlib.sha256()
 for line in c.iterdump():h.update((line+'\\n').encode())
 print(json.dumps({'logical_sha256':h.hexdigest(),'integrity':c.execute('PRAGMA integrity_check').fetchone()[0]}))
'''
old=['ssh','-o','BatchMode=yes','varmak@192.168.3.36']
new=['ssh','-i',str(Path.home()/'.ssh/id_ed25519_migration_192_168_3_136'),'-o','IdentitiesOnly=yes','varma@192.168.3.136']
path='/home/varma/old_server_archive_2026_09_26/additional_projects/oncopulse/full_export/extracted/oncopulse_export/files/var/lib/oncopulse/oncopulse.db'
results=[]
for command,name in [(old,'/var/lib/oncopulse/oncopulse.db'),(new,path)]:
 text='PATH='+repr('file:'+name)+'\n'+code
 results.append(json.loads(subprocess.check_output(command+['python3 -c '+shlex.quote(text)],text=True)))
assert results[0]==results[1],results
report={'source':results[0],'destination':results[1],'match':True}
Path(__file__).with_name('logical_database_verification.json').write_text(json.dumps(report,indent=2));print(json.dumps(report))
