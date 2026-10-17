"""Two-contract sizing; signal thresholds are unchanged."""
from decimal import Decimal
CONTRACTS = 2
MAX_CONTRACTS = Decimal('8')
MAX_RISK = Decimal('7.60')
DAILY_LOSS_BUDGET = Decimal('10')
MIN_CASH = Decimal('2')
PREFLIGHT_CASH = Decimal('10')
ARM_FILE = 'ARM_2CT'
