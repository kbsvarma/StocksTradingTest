"""Render host-specific user services; activation is an explicit cutover step."""
import argparse
import ipaddress
from pathlib import Path
import re
import shutil
import subprocess
from advisor.ops.migrate import SECRETS


def render(repo, bind, port):
    repo=Path(repo).resolve()
    if not re.fullmatch(r'/[A-Za-z0-9_./-]+',str(repo)):raise ValueError('Use a repository path without spaces or shell metacharacters')
    ipaddress.ip_address(bind)
    if not 1024<=port<=65535:raise ValueError('Invalid unprivileged terminal port')
    units={}
    for source in (repo/'advisor/ops/systemd').iterdir():
        if source.suffix not in ('.service','.timer'):continue
        text=source.read_text().replace('%h/stockstest',str(repo)).replace('192.168.3.36',bind)
        text=text.replace('--server.port 8505',f'--server.port {port}')
        units[source.name]=text
    return units


def main():
    p=argparse.ArgumentParser(description=__doc__)
    p.add_argument('--repo',required=True,type=Path);p.add_argument('--bind',default='127.0.0.1');p.add_argument('--port',type=int,default=8505)
    p.add_argument('--render-only',type=Path,help='Write units to a review directory without changing services')
    p.add_argument('--activate',action='store_true',help='Start terminal only; source-host cutover must be complete')
    p.add_argument('--replace-services',action='store_true')
    args=p.parse_args();repo=args.repo.resolve();units=render(repo,args.bind,args.port)
    private=repo/'.advisor-migration/config'
    secrets=list(private.glob('*')) if not args.render_only else []
    for source in secrets:
        if source.name not in SECRETS or not source.is_file():raise ValueError('Unexpected application configuration file')
        destination=Path.home()/source.name
        if destination.exists() and destination.read_bytes()!=source.read_bytes():raise ValueError('Existing application configuration differs: '+source.name)
    if args.activate and not (repo/'.venv/bin/python').exists():raise ValueError('Create the runtime virtual environment first')
    target=args.render_only or Path.home()/'.config/systemd/user'
    target.mkdir(parents=True,exist_ok=True)
    for name,text in units.items():
        path=target/name
        if path.exists() and path.read_text()!=text and not args.replace_services:raise ValueError('Existing service differs: '+name)
    for name,text in units.items():(target/name).write_text(text)
    if args.render_only:
        print(f'Rendered {len(units)} units for review; no services changed');return
    for source in secrets:
        destination=Path.home()/source.name
        if not destination.exists():shutil.copyfile(source,destination)
        destination.chmod(0o600)
    for name in ('data','logs'):(repo/'advisor'/name).mkdir(exist_ok=True)
    subprocess.run(['systemctl','--user','daemon-reload'],check=True)
    if args.activate:
        subprocess.run(['systemctl','--user','enable','--now','advisor-terminal.service'],check=True)
    print('Services installed. Scheduled writers are not enabled automatically; cut over one host at a time.')


if __name__=='__main__':main()
