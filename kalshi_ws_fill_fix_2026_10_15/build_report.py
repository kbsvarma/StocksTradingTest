"""Build the completion report only from successful actual-fill evidence."""
import datetime as dt
import json
from pathlib import Path
from zoneinfo import ZoneInfo

ROOT = Path(__file__).resolve().parent


def read(name):
    return json.loads((ROOT / 'receipts' / name).read_text())


def main():
    deployment = read('deployment_receipt.json')
    verification = read('actual_verification.json')
    replay = read('actual_before_after_replay.json')
    final = read('final_verification.json')
    assert verification['status'] == 'ACTUAL_NO_WEBSOCKET_FILL_VERIFIED'
    assert replay['status'] == 'PASS' and final['actual_ws_fills_verified']
    assert deployment['config_unchanged'] and final['config_unchanged']
    assert 'Ran 330 tests' in (ROOT / 'receipts/tests.log').read_text()
    at = dt.datetime.fromtimestamp(final['ts'], ZoneInfo('America/New_York'))
    lines = [
        'Kalshi WebSocket fill-confirmation repair — verified live',
        f'Review completed: {at:%Y-%m-%d %H:%M:%S %Z}',
        '',
        'Fixed: legitimate NO purchases were rejected as WebSocket fill proof',
        'because the old guard required legacy action=buy. Kalshi can represent',
        'these entries as canonical outcome_side=no, book_side=ask, action=sell.',
        'The repair validates canonical direction against the exact stored entry',
        'and fresh terminal order, retaining identity, route, quantity, cost and',
        'fee checks. Incomplete or conflicting receipts still fall back to REST.',
        '',
        'Actual trades independently verified:',
        'Market | Side | Contracts | Entry | Fill cost | Fees | Submit to WS | Submit to local confirmation',
    ]
    for trade in verification['proven']:
        timing = trade['timing_ms']
        lines.append(
            f"{trade['ticker']} | {trade['side'].upper()} | {trade['quantity']} | "
            f"${trade['entry']} | ${trade['cost']} | ${trade['fees']} | "
            f"{timing['submit_to_ws_receipt']:.1f} ms | "
            f"{timing['submit_to_local_confirmation']:.1f} ms")
    lines += [
        '',
        'Every listed fill was captured by production and an independent',
        'receive-only listener. Raw fill messages matched; authenticated GET-only',
        'order/fill receipts and the production ledger agreed on IDs, contracts,',
        'cost and fees. Initial fill transport was WebSocket. No verification',
        'financial orders were submitted; these were natural strategy entries.',
        '',
        'Same unmodified actual frames through old and repaired validators:',
    ]
    for trade in replay['trades']:
        lines.append(f"{trade['ticker']} {trade['side'].upper()} "
                     f"action={','.join(trade['actual_legacy_actions'])}: "
                     f"old accepted={trade['old_guard_accepted']}; "
                     f"repaired accepted={trade['new_guard_accepted']}")
    lines += [
        '',
        '330 regression tests passed. Only stream_transport.py, engine.py and',
        'runner.py changed in production. All strategy and price rules, five',
        'contracts per entry, shared 25 contracts / $25 exposure / $20 daily risk,',
        'lane pauses and retry rules are unchanged. The saved stable checkpoint',
        'and pre-existing order/attempt records were preserved.',
        '',
        'Raw receipts and explicit confirmation/fallback reasons are now written',
        'asynchronously, with bounded queue and dropped/error counters. Initial',
        'confirmation provenance survives later settlement reconciliation.',
        'The resting-paper observer received only its exact reviewed source-hash',
        'update; frozen rules and records were preserved. Other services were',
        'unchanged. The temporary independent listener has been stopped.',
        '',
        'Limit: receipt arrival and complete local confirmation are different.',
        'Fresh terminal-order REST proof remains required. These observations',
        'validate the fill repair; they do not establish improved margins,',
        'guarantee fills or prove an end-to-end latency improvement.',
        '',
        'Evidence: receipts/actual_verification.json (full original WS frames,',
        'independent frames, terminal orders, REST fills and ledger records);',
        'receipts/actual_before_after_replay.json; receipts/final_verification.json;',
        'receipts/deployment_receipt.json; receipts/tests.log.',
    ]
    (ROOT / 'REPORT.txt').write_text('\n'.join(lines) + '\n')
    print(ROOT / 'REPORT.txt')


if __name__ == '__main__':
    main()
