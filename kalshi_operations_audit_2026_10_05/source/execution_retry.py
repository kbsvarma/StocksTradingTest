"""Bound book checks and confirmed-zero IOC retries on one frozen signal."""
from decimal import Decimal

MAX_PRE_SUBMISSION_CHECKS=24
RETRY_DEADLINE_SECONDS=25
PRIMARY_HEAD_START_CHECKS=8
MAX_IOC_SUBMISSIONS=3
MIN_IOC_RETRY_INTERVAL_SECONDS=1
ZERO_FILL_RETRY='Zero fill confirmed; retry pending'

def retry_candidate(record,decision_ts,now,lane=None):
    """Local eligibility only. The engine must reconfirm with the exchange."""
    if not record or record.get('ioc_retry_version')!=1:return False
    try:
        proof=record.get('zero_fill_confirmation') or {}
        return (record.get('status')=='unfilled' and not record.get('retry_forbidden') and
                record.get('attempt_number') in (1,2) and
                record['attempt_number']<MAX_IOC_SUBMISSIONS and
                Decimal(record.get('filled','-1'))==0 and Decimal(record.get('ever_filled','-1'))==0 and
                proof.get('order_id')==record.get('order_id') and bool(record.get('order_id')) and
                proof.get('status')=='canceled' and proof.get('fill_count')=='0' and
                proof.get('remaining_count')=='0' and proof.get('fill_rows')==0 and
                record['signal']['decision_ts']==decision_ts and
                0<=now-decision_ts<=RETRY_DEADLINE_SECONDS and
                (lane is None or record.get('lane','cushion_z2')==lane))
    except (KeyError,TypeError,ValueError,ArithmeticError):return False

def retry_allowed(original,reason,checks,age,has_order,zero_fill_retry=False,awaiting_terminal=False):
    # awaiting_terminal permits a scheduler check, never a submission; Engine blocks it.
    if (has_order and not (zero_fill_retry or awaiting_terminal)) or reason=='Book weakened below floor' or not 0<=age<=RETRY_DEADLINE_SECONDS or checks>=MAX_PRE_SUBMISSION_CHECKS:
        return False
    return original in ('decision_candle_unavailable','no_decision_quote',
                        'book_outside_limit','insufficient_depth') or reason in (
                        'Order reconciliation pending','Insufficient depth inside cap',
                        ZERO_FILL_RETRY,'Zero-fill retry cooldown')
