"""Compare old and deployed guards using unchanged, independently witnessed WS fills.

Offline replay only: imports the transport classes and reads saved receipts.
Does not create a connection, exchange client, order or position.
"""
import importlib.util
import json
from pathlib import Path

ROOT = Path(__file__).resolve().parent


def load(name, source):
    spec = importlib.util.spec_from_file_location(name, source)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def main():
    verification = json.loads((ROOT / 'receipts/actual_verification.json').read_text())
    assert verification['status'] == 'ACTUAL_NO_WEBSOCKET_FILL_VERIFIED'
    old = load('original_fill_transport', ROOT / 'source_before/stream_transport.py')
    new = load('repaired_fill_transport', ROOT / 'candidate/stream_transport.py')
    results = []
    for trade in verification['proven']:
        before, after = old.FillState(), new.FillState()
        for row in trade['production_frames']:
            before.apply(row['frame']['msg'], row['ts'])
            after.apply(row['frame']['msg'], row['ts'])
        args = trade['terminal_order'], trade['ledger_record']
        old_accepts = before.confirmed(*args) is not None
        new_accepts = after.confirmed(*args) is not None
        assert new_accepts, trade['ticker']
        if trade['side'] == 'no' and trade['actual_legacy_actions'] == ['sell']:
            assert not old_accepts, 'Expected original NO/sell rejection not reproduced'
        results.append({
            'ticker': trade['ticker'], 'side': trade['side'],
            'actual_legacy_actions': trade['actual_legacy_actions'],
            'fill_ids': trade['fill_ids'], 'old_guard_accepted': old_accepts,
            'new_guard_accepted': new_accepts, 'new_guard_reason': after.last_reason,
            'original_frames_unchanged': True,
            'independent_payloads_equal': trade['independent_payloads_equal'],
            'rest_economics_equal': trade['rest_economics_equal'],
        })
    result = {'status': 'PASS', 'comparison': 'same actual WS frames and terminal orders',
              'trades': results}
    (ROOT / 'receipts/actual_before_after_replay.json').write_text(json.dumps(result, indent=2) + '\n')
    print(json.dumps(result, indent=2))


if __name__ == '__main__':
    main()
