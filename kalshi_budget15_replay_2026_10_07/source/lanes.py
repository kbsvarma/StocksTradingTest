"""Fixed lane definitions. Only the explicit user-run activation arms z3."""
from dataclasses import dataclass
from decimal import Decimal as D

Z2 = 'cushion_z2'
Z3 = 'premium_z3'

@dataclass(frozen=True)
class Lane:
    name: str
    contracts: int
    floor: D
    ceiling: D
    z: float
    cap: D
    reserve: D

LANES = {
    Z2: Lane(Z2, 5, D('.85'), D('.95'), 2, D('.939'), D('.95')),
    Z3: Lane(Z3, 5, D('.93'), D('.98'), 3, D('.975'), D('1')),
}
Z3_ARM = 'ARM_Z3_5CT'
Z3_HALT = 'HALT_Z3'
Z3_MAX_CONTRACTS = D('20')
Z3_MAX_RISK = D('20')
Z3_DAILY_BUDGET = D('25')

def lane_for(record):
    # Historical records predate lane tags and belong to the existing z2 bot.
    name = record.get('lane', Z2)
    if name not in LANES:
        raise ValueError('Unknown strategy lane: ' + str(name))
    return LANES[name]

def z3_permission(root):
    if (root / Z3_HALT).exists():
        return 'Strong cushion manually halted'
    try:
        if (root / Z3_ARM).read_text().strip() == '5':
            return ''
    except OSError:
        pass
    return 'Strong cushion not activated'
