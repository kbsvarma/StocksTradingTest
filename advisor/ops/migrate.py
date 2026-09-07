"""Portable Advisor snapshot/restore. No services start during restore.

python -m advisor.ops.migrate export --repo /path/to/repo --bundle /private/backup.tgz
python -m advisor.ops.migrate restore --bundle /private/backup.tgz --destination /new/repo
"""
from contextlib import closing
import argparse
import hashlib
import json
import os
from pathlib import Path
import shutil
import sqlite3
import subprocess
import tarfile
import tempfile
from datetime import datetime, timezone

SECRETS = ('.advisor_research.env', '.advisor_terminal_env', '.advisor_model.env')
OMIT = {'__pycache__', '.pytest_cache', '.DS_Store', 'logs', 'reviews'}


def sha(path):
    h = hashlib.sha256()
    with path.open('rb') as source:
        for block in iter(lambda: source.read(1024*1024), b''):h.update(block)
    return h.hexdigest()


def sqlite_file(path):
    with path.open('rb') as f:return f.read(16) == b'SQLite format 3\x00'


def check_sqlite(path):
    with closing(sqlite3.connect(path.as_uri()+'?mode=ro', uri=True)) as db:
        if db.execute('PRAGMA integrity_check').fetchall() != [('ok',)]:
            raise ValueError('SQLite integrity failure: '+path.name)


def export(repo, bundle, include_secrets=False):
    repo, bundle = Path(repo).resolve(), Path(bundle).resolve()
    if bundle.exists():raise ValueError('Bundle already exists; choose a new name')
    for state in (repo/'advisor/data/intelligence/investigations').glob('*/*/status.json'):
        status=json.loads(state.read_text())
        if status.get('state') in ('queued','running'):
            age=(datetime.now(timezone.utc)-datetime.fromisoformat(status['updated_at'])).total_seconds()
            if age < 3600:raise ValueError('An investigation is active; finish it before migration')
    bundle.parent.mkdir(parents=True,exist_ok=True)
    with tempfile.TemporaryDirectory(prefix='advisor-export-',dir=bundle.parent) as tmp:
        root=Path(tmp);files={};databases=[]
        for source in sorted((repo/'advisor').rglob('*')):
            relative=source.relative_to(repo)
            if any(p in OMIT for p in relative.parts) or source.is_dir():continue
            if source.is_symlink():raise ValueError('Refusing symlink in snapshot: '+str(relative))
            if source.name=='active.json' or (source.name.endswith('.lock') and source.name not in {'requirements.lock','requirements.resolved.lock'}) or source.name.endswith(('-wal','-shm','-journal','.tmp','.pyc')):continue
            target=root/'repo'/relative;target.parent.mkdir(parents=True,exist_ok=True)
            if sqlite_file(source):
                with closing(sqlite3.connect(source.as_uri()+'?mode=ro',uri=True)) as src, closing(sqlite3.connect(target)) as dst:
                    src.backup(dst)
                    dst.execute('PRAGMA journal_mode=DELETE')
                check_sqlite(target);databases.append(str(Path('repo')/relative))
            else:shutil.copy2(source,target)
        if include_secrets:
            for name in SECRETS:
                source=Path.home()/name
                if source.exists():
                    target=root/'config'/name;target.parent.mkdir(exist_ok=True)
                    shutil.copyfile(source,target);target.chmod(0o600)
        model=Path.home()/'.local/share/advisor/ollama/model-manifest.json'
        if model.exists():shutil.copyfile(model,root/'model-manifest.json')
        try:revision=subprocess.check_output(['git','-C',str(repo),'rev-parse','HEAD'],text=True,stderr=subprocess.DEVNULL).strip()
        except subprocess.CalledProcessError:revision='unavailable'
        for path in root.rglob('*'):
            if path.is_file():files[str(path.relative_to(root))]=sha(path)
        manifest={'schema_version':1,'created_at':datetime.now(timezone.utc).isoformat(),
                  'git_revision':revision,'files':files,'sqlite_databases':databases,
                  'contains_secrets':include_secrets,
                  'snapshot_consistency':'SQLite online backups; atomic files. Stop Advisor writers for final cutover.',
                  'model_weights':'Reinstall pinned runtime and verify model digest; weights are not bundled.'}
        (root/'manifest.json').write_text(json.dumps(manifest,indent=2)+'\n')
        fd=os.open(bundle,os.O_WRONLY|os.O_CREAT|os.O_EXCL,0o600)
        with os.fdopen(fd,'wb') as output,tarfile.open(fileobj=output,mode='w:gz') as archive:
            for path in sorted(root.iterdir()):archive.add(path,arcname=path.name,recursive=True)
    checksum=bundle.with_suffix(bundle.suffix+'.sha256')
    checksum.write_text(sha(bundle)+'  '+bundle.name+'\n');checksum.chmod(0o600)
    return {'bundle':str(bundle),'files':len(files),'databases':len(databases),'sha256':sha(bundle)}


def restore(bundle, destination):
    bundle,destination=Path(bundle).resolve(),Path(destination).resolve()
    if destination.exists():raise ValueError('Destination must not exist; live data is never overwritten')
    checksum=bundle.with_suffix(bundle.suffix+'.sha256')
    if not checksum.exists() or checksum.read_text().split()[0]!=sha(bundle):raise ValueError('Bundle checksum mismatch or missing')
    destination.parent.mkdir(parents=True,exist_ok=True)
    with tempfile.TemporaryDirectory(prefix='advisor-restore-',dir=destination.parent) as tmp:
        root=Path(tmp)
        with tarfile.open(bundle,'r:gz') as archive:
            members=archive.getmembers()
            for m in members:
                p=Path(m.name)
                if p.is_absolute() or '..' in p.parts or not (m.isfile() or m.isdir()):raise ValueError('Unsafe archive entry')
            archive.extractall(root,filter='data')
        manifest=json.loads((root/'manifest.json').read_text())
        if manifest.get('schema_version')!=1:raise ValueError('Unsupported snapshot version')
        actual={str(p.relative_to(root)) for p in root.rglob('*') if p.is_file() and p!=root/'manifest.json'}
        if actual != set(manifest['files']):raise ValueError('Snapshot file inventory mismatch')
        for relative,digest in manifest['files'].items():
            if sha(root/relative)!=digest:raise ValueError('Snapshot content mismatch: '+relative)
        for relative in manifest['sqlite_databases']:check_sqlite(root/relative)
        # All validation precedes promotion. Both are on the same filesystem.
        restored=root/'repo'
        for name in ('config','manifest.json','model-manifest.json'):
            if (root/name).exists():
                private=restored/'.advisor-migration';private.mkdir(exist_ok=True);private.chmod(0o700)
                shutil.move(str(root/name),private/name)
        (restored/'advisor/logs').mkdir(exist_ok=True)
        restored.chmod(0o700);restored.rename(destination)
    return {'destination':str(destination),'verified_files':len(actual),'services_started':False}


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    sub=parser.add_subparsers(dest='command',required=True)
    exp=sub.add_parser('export');exp.add_argument('--repo',required=True);exp.add_argument('--bundle',required=True)
    exp.add_argument('--include-secrets',action='store_true',help='Private bundle includes application env files; transport over SSH only')
    res=sub.add_parser('restore');res.add_argument('--bundle',required=True);res.add_argument('--destination',required=True)
    args=parser.parse_args()
    result=export(args.repo,args.bundle,args.include_secrets) if args.command=='export' else restore(args.bundle,args.destination)
    print(json.dumps(result,indent=2))


if __name__=='__main__':main()
