"""Snapshot independently updated research files before archiving; never touches /var/backups."""
from pathlib import Path
import os, shutil, sqlite3, tarfile, tempfile, time
from datetime import datetime
H=Path.home(); source=H/'stockstest/advisor/data'; dest=H/'backups/advisor'
dest.mkdir(parents=True,exist_ok=True)
def copy_stable(src,out):
    for attempt in range(5):
        try:
            before=src.stat()
            with src.open('rb') as f: isdb=f.read(16)==b'SQLite format 3\x00'
            if isdb:
                out.unlink(missing_ok=True)
                with sqlite3.connect(src.as_uri()+'?mode=ro',uri=True) as a, sqlite3.connect(out) as b:
                    a.backup(b)
                    if b.execute('pragma integrity_check').fetchone()[0]!='ok':raise RuntimeError('SQLite integrity failure')
                return
            shutil.copyfile(src,out)
            after=src.stat()
            if (before.st_ino,before.st_size,before.st_mtime_ns)==(after.st_ino,after.st_size,after.st_mtime_ns):return
        except FileNotFoundError:
            if attempt==4:raise
        time.sleep(.2)
    raise RuntimeError('Continuously changing backup input: '+str(src.relative_to(source)))
with tempfile.TemporaryDirectory(prefix='.snapshot-',dir=dest) as temp:
    root=Path(temp)/'data';root.mkdir()
    for parent,dirs,files in os.walk(source):
        rel=Path(parent).relative_to(source)
        dirs[:]=[d for d in dirs if d!='__pycache__' and (rel/d).as_posix()!='research/panels/builds']
        target=root/rel;target.mkdir(parents=True,exist_ok=True)
        for name in files:
            src=Path(parent)/name
            if src.is_symlink():raise RuntimeError('Unexpected backup symlink: '+str(src))
            if name.endswith(('-wal','-shm')):continue
            copy_stable(src,target/name)
    stamp=datetime.now().strftime('%Y-%m-%d'); archive=dest/f'advisor_data_{stamp}.tar.gz'; temporary=Path(temp)/'archive.tar.gz'
    with tarfile.open(temporary,'w:gz') as t:t.add(root,arcname='data')
    with tarfile.open(temporary,'r:gz') as t:
        names=set(t.getnames())
        for required in ['data/decision_journal.jsonl','data/picks_ledger.jsonl']:
            if required not in names:raise RuntimeError('Required record missing: '+required)
        for member in t:
            if member.isfile():
                f=t.extractfile(member)
                while f.read(1024*1024):pass
    temporary.chmod(0o600);os.replace(temporary,archive)
archives=sorted(dest.glob('advisor_data_*.tar.gz'),key=lambda p:p.stat().st_mtime,reverse=True)
for old in archives[14:]:
    if not old.name.endswith('-01.tar.gz'):old.unlink()
print(datetime.now().isoformat(), 'verified backup',archive.name,'bytes',archive.stat().st_size)
