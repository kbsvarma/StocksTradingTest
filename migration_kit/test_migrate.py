import json,sqlite3,tempfile,unittest,subprocess
from pathlib import Path
from unittest.mock import patch
from migrate import copy_one,sha,stage,snapshot,verify
class MigrationTests(unittest.TestCase):
 def test_sqlite_backup_captures_committed_wal(self):
  with tempfile.TemporaryDirectory() as d:
   h=Path(d);src=h/'active.sqlite';db=sqlite3.connect(src);db.execute('PRAGMA journal_mode=WAL');db.execute('CREATE TABLE t(v)');db.execute('INSERT INTO t VALUES (17)');db.commit();dst=h/'snapshot.sqlite';method=copy_one(src,dst,h,h,'copy');self.assertEqual(method,'sqlite_backup');clone=sqlite3.connect(dst);self.assertEqual(clone.execute('SELECT v FROM t').fetchone()[0],17);clone.close();db.close()
 def test_cold_refuses_active_source(self):
  with tempfile.TemporaryDirectory() as d:
   with patch('migrate.inventory',return_value={'units':[{'active':'active','enabled':'enabled'}]}):
    with self.assertRaisesRegex(ValueError,'cold snapshot'):snapshot(Path(d),Path(d)/'bundle','cold')
 def test_relocation_keeps_frozen_release_and_lockfile(self):
  with tempfile.TemporaryDirectory() as d:
   b=Path(d)/'bundle';repo=b/'home/trendbot/trend_bot';repo.mkdir(parents=True);(repo/'requirements.lock').write_text('example==1\n');subprocess.run(['git','init','-q',str(repo)],check=True);subprocess.run(['git','-C',str(repo),'add','.'],check=True);subprocess.run(['git','-C',str(repo),'-c','user.name=test','-c','user.email=test@example.invalid','commit','-qm','fixture'],check=True)
   frozen=b/'home/trendbot/shadow_capture';frozen.mkdir();(frozen/'collector.py').write_text('ROOT="/home/olduser"\n');(frozen/'release.json').write_text(json.dumps({'collector.py':sha(frozen/'collector.py')}));units=b/'units/system';units.mkdir(parents=True);(units/'trendbot.service').write_text('User=olduser\nWorkingDirectory=/home/olduser/trendbot/trend_bot\n');(b/'units/user').mkdir();(b/'runtime.lock').write_text('example==1\n');files=[{'path':str(f.relative_to(b)),'method':'copy','sha256':sha(f)} for f in b.rglob('*') if f.is_file()];m={'mode':'warm','activation_eligible':False,'source_home':'/home/olduser','source_user':'olduser','source_host':'oldbox','python':'Python 3.12.3','architecture':'x86_64','files':files};(b/'manifest.json').write_text(json.dumps(m));(b/'manifest.sha256').write_text(sha(b/'manifest.json'));out=Path(d)/'stage';stage(b,out,'/home/newuser','newuser');self.assertIn('User=newuser',(out/'units/system/trendbot.service').read_text());self.assertEqual(sha(out/'home/trendbot/shadow_capture/collector.py'),sha(frozen/'collector.py'));self.assertTrue((out/'home/trendbot/trend_bot/requirements.lock').exists());(b/'runtime.lock').write_text('tampered')
   with self.assertRaisesRegex(ValueError,'checksum mismatch'):verify(b)
if __name__=='__main__':unittest.main()
