"""Reproduce slow GET and CAD retry behavior offline, with no credentials/network."""
import json,sys
from pathlib import Path
from unittest.mock import patch
P=Path(__file__).resolve().parent
sys.path.insert(0,str(P.parent/'kalshi_usdcad_2026_10_14/candidate'))
import kalshi_client as kc
from execution_retry import retry_candidate,fresh_retry_candidate,RETRY_DEADLINE_SECONDS
elapsed=[0.0];calls=[]
client=kc.KalshiClientV2(creds=None,dry_run=True,timeout=5)
def request(method,url,**kw):
    assert method=='GET';calls.append(dict(method=method,timeout=kw['timeout']))
    elapsed[0]+=kw['timeout'];raise kc.requests.exceptions.Timeout('simulated timeout')
def sleep(seconds):elapsed[0]+=seconds
with patch.object(client.session,'request',side_effect=request),patch.object(kc.time,'sleep',side_effect=sleep):
    try:client._request('GET','/portfolio/positions',signed=False)
    except kc.KalshiAPIError:pass
client.session.close()
assert elapsed[0]==30.5 and len(calls)==4
r=dict(ticker='KXUSDCAD15M-TEST',asset='USDCAD',status='unfilled',requested_count='5',filled='0',ever_filled='0',lane='cushion_z2',ioc_retry_version=1,attempt_number=1,order_id='test-order',signal=dict(decision_ts=1020,close_ts=1620),zero_fill_confirmation=dict(order_id='test-order',status='canceled',fill_count='0',remaining_count='0',fill_rows=0))
assert retry_candidate(r,1020,1024,'cushion_z2')
assert not fresh_retry_candidate(r,1080,1083,'cushion_z2')
w=dict(r,asset='WTI',ticker='KXWTI15M-TEST')
assert fresh_retry_candidate(w,1080,1083,'cushion_z2')
out=dict(network='all requests mocked; no credentials loaded',get_timeout_case=dict(attempts=len(calls),simulated_seconds=elapsed[0],signal_retry_deadline=RETRY_DEADLINE_SECONDS),cad_zero_fill=dict(same_minute_retry=True,later_minute_retry=False,wti_control_later_retry=True))
(P/'offline_failure_results.json').write_text(json.dumps(out,indent=2));print(json.dumps(out,indent=2))
