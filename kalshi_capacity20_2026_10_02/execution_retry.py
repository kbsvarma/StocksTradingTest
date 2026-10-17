"""Only retry pre-submission checks on a still-valid frozen minute signal."""
def retry_allowed(original,reason,checks,age,has_order):
    if has_order or reason=='Book weakened below floor' or not 0<=age<=25 or checks>=8:
        return False
    return original in ('decision_candle_unavailable','no_decision_quote',
                        'book_outside_limit','insufficient_depth') or reason in (
                        'Order reconciliation pending','Insufficient depth inside cap')
