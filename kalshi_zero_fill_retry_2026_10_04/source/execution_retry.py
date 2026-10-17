"""Only retry pre-submission checks on a still-valid frozen minute signal."""
MAX_PRE_SUBMISSION_CHECKS=24
RETRY_DEADLINE_SECONDS=25
PRIMARY_HEAD_START_CHECKS=8

def retry_allowed(original,reason,checks,age,has_order):
    if has_order or reason=='Book weakened below floor' or not 0<=age<=RETRY_DEADLINE_SECONDS or checks>=MAX_PRE_SUBMISSION_CHECKS:
        return False
    return original in ('decision_candle_unavailable','no_decision_quote',
                        'book_outside_limit','insufficient_depth') or reason in (
                        'Order reconciliation pending','Insufficient depth inside cap')
