"""USER-RUN ONLY: allocate up to USD 20 existing Predictions cash to shard 2."""
import argparse
from decimal import Decimal
import fcntl
import json
import os
from pathlib import Path
import time
from kalshi_client import Credentials, KalshiClientV2

ROOT=Path(__file__).resolve().parent

def main():
    p=argparse.ArgumentParser()
    p.add_argument('--execute',action='store_true',help='Transfer existing Predictions funds; armed bot may subsequently trade')
    p.add_argument('--target',type=Decimal,default=Decimal('5'),help='Target crypto balance in USD, maximum 20')
    args=p.parse_args()
    if not args.target.is_finite() or not 0<args.target<=20 or args.target*10000!=(args.target*10000).to_integral_value():
        raise SystemExit('Target must be positive, at most USD 20, and have at most four decimals.')
    state=ROOT/'state';state.mkdir(exist_ok=True)
    lock=(state/'fund_crypto.lock').open('a')
    fcntl.flock(lock,fcntl.LOCK_EX|fcntl.LOCK_NB)
    client=KalshiClientV2(Credentials.load(),dry_run=not args.execute,timeout=10)
    def balance(index):
        row=client._request('GET','/portfolio/balance',params={'exchange_index':index,'subaccount':0})
        return Decimal(row['balance_dollars'])
    target=args.target;available=balance(2);source=balance(0)
    print(f'Existing Predictions balance on default engine: USD {source:.4f}')
    print(f'Crypto engine balance: USD {available:.4f}')
    if available>=target:
        print(f'Crypto already has at least USD {target}. No transfer needed.');return
    amount=target-available
    if source<amount:raise SystemExit('Insufficient existing default-engine funds; no transfer made.')
    intent=state/'fund_crypto_once.json'
    if intent.exists():raise SystemExit('A prior transfer attempt is recorded. Inspect it before any retry; no transfer made.')
    payload={'source':'event_contract','destination':'event_contract','amount':int(amount*10000),
             'source_exchange_shard':0,'destination_exchange_shard':2,
             'source_subaccount':0,'destination_subaccount':0}
    print(f'Plan: move USD {amount:.4f} within your existing Predictions account (default → crypto).')
    if not args.execute:
        print('READ-ONLY CHECK PASSED. No transfer made.');return
    record={'created_ts':time.time(),'status':'intent','request':payload}
    with intent.open('x') as f:
        json.dump(record,f,indent=2);f.flush();os.fsync(f.fileno())
    try:
        response=client._request('POST','/portfolio/intra_exchange_instance_transfer',body=payload)
    except Exception as e:
        print('Transfer outcome needs reconciliation. Do not rerun or delete the intent file.')
        raise SystemExit(str(e))
    record.update(status='accepted',response=response)
    with intent.open('w') as f:
        json.dump(record,f,indent=2);f.flush();os.fsync(f.fileno())
    print('Transfer accepted:',response.get('transfer_id','ID not returned'))
    for _ in range(12):
        available=balance(2)
        if available>=target:
            print(f'Confirmed crypto balance USD {available:.4f}. The armed bot refreshes funding every 10 seconds.');return
        time.sleep(2)
    print('Transfer still processing. Do not resubmit; check the dashboard or transfer record.')

if __name__=='__main__':main()
