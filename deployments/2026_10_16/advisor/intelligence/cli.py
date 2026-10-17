"""Operator access to the decision store. No execution or notification commands.

Local CLI identities use the actual operating-system account. Role policy is
provisioned by operators, not accepted as a command-line role override.
"""
import argparse
import getpass
import json
from pathlib import Path
from advisor.intelligence.access import Principal
from advisor.intelligence.adapters import read_json, snapshot
from advisor.intelligence.store import CallStore


def main():
    p=argparse.ArgumentParser(description=__doc__)
    p.add_argument('--data-dir',type=Path,required=True)
    sub=p.add_subparsers(dest='command',required=True)
    sub.add_parser('snapshot');sub.add_parser('export')
    review=sub.add_parser('review')
    review.add_argument('--call-id',required=True);review.add_argument('--revision',type=int,required=True)
    review.add_argument('--verdict',choices=['approve','reject'],required=True);review.add_argument('--reason',required=True)
    backup=sub.add_parser('backup');backup.add_argument('--output',type=Path,required=True)
    a=p.parse_args()
    username=getpass.getuser()
    policy=read_json(a.data_dir/'access_policy.json')
    member=(policy.get('local_accounts') or {}).get(username) or {'tenant':'model','role':'viewer'}
    principal=Principal(username,member['tenant'],member['role'])
    if a.command=='snapshot': result=snapshot(a.data_dir,principal=principal)
    else:
        with CallStore(a.data_dir/'intelligence'/'calls.sqlite',principal,readonly=a.command=='export') as store:
            if a.command=='export':result=store.export()
            elif a.command=='review':result=store.review(a.call_id,expected_revision=a.revision,verdict=a.verdict,reason=a.reason)
            else:result={'backup':str(store.backup(a.output))}
    print(json.dumps(result,indent=2))


if __name__=='__main__':main()
