"""Five-contract sizing; signal thresholds are unchanged."""
from decimal import Decimal
CONTRACTS = 5
MAX_CONTRACTS = Decimal('25')
MAX_RISK = Decimal('25.00')
DAILY_LOSS_BUDGET = Decimal('20')
MIN_CASH = Decimal('5')
PREFLIGHT_CASH = Decimal('10')
ARM_FILE = 'ARM_5CT'
