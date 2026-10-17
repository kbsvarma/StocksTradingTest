"""Fixed lane definitions. Only the explicit user-run activation arms z3."""
from dataclasses import dataclass, replace
from decimal import Decimal as D
import json
import math

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
    min_minutes: float = 3
    max_minutes: float = 10
    z_slope: float = 0

    def threshold(self, minutes):
        if not math.isfinite(float(minutes)) or minutes <= 0:
            raise ValueError('Invalid minutes remaining')
        return max(.5, self.z+self.z_slope*math.log2(minutes/5))

LANES = {
    Z2: Lane(Z2, 5, D('.85'), D('.95'), 2, D('.939'), D('.95')),
    Z3: Lane(Z3, 5, D('.93'), D('.98'), 3, D('.975'), D('1')),
}
Z3_ARM = 'ARM_Z3_5CT'
Z3_HALT = 'HALT_Z3'
Z3_MAX_CONTRACTS = D('25')
Z3_MAX_RISK = D('25')
Z3_DAILY_BUDGET = D('25')
CRYPTO_ASSETS = ('BTC','ETH','SOL','XRP')
BASE_NONCRYPTO_ASSETS = ('COPPER','NATGAS','EURUSD')
NEW_ASSETS = BASE_NONCRYPTO_ASSETS + ('WTI',)
ALL_ASSETS = CRYPTO_ASSETS + NEW_ASSETS
NONCRYPTO_ARM = 'ARM_NONCRYPTO_5CT'
NONCRYPTO_VERSION = 1
WTI_ARM = 'ARM_WTI_5CT'
MULTIASSET_VERSION = 2

def record_asset(record):
    explicit=record.get('asset')
    ticker=record.get('ticker','')
    inferred=next((a for a in ALL_ASSETS if ticker.startswith('KX'+a+'15M-')),None)
    if explicit and inferred and explicit != inferred:
        raise ValueError('Ticker/asset mismatch')
    return explicit or inferred

def route_for(record):
    asset=record_asset(record)
    if asset not in ALL_ASSETS:raise ValueError('Unsupported market asset')
    return 0 if asset=='EURUSD' else 2

def asset_permission(root,asset):
    if asset not in NEW_ASSETS:return ''
    if (root/('HALT_'+asset)).exists():return asset+' manually halted'
    if asset=='WTI':
        try:
            if (root/WTI_ARM).read_text().strip()=='5':return ''
        except OSError:pass
        return 'WTI not activated'
    try:
        arm=json.loads((root/NONCRYPTO_ARM).read_text())
        if arm != dict(version=NONCRYPTO_VERSION,contracts=5,assets=list(BASE_NONCRYPTO_ASSETS)):
            return 'Non-crypto activation invalid'
    except (OSError,ValueError):return 'Non-crypto markets not activated'
    return ''

def lane_for(record):
    # Historical records predate lane tags and belong to the existing z2 bot.
    name = record.get('lane', Z2)
    if name not in LANES:
        raise ValueError('Unknown strategy lane: ' + str(name))
    rule=LANES[name]
    asset=record_asset(record)
    if asset=='COPPER':
        return replace(rule,z=1.5 if name==Z2 else 2.5,
                       floor=D('.80') if name==Z2 else D('.93'),min_minutes=1)
    if asset=='EURUSD':
        return replace(rule,z=1.75 if name==Z2 else 2.75,z_slope=.5,min_minutes=1)
    if asset=='WTI':
        return replace(rule,z=1.5 if name==Z2 else 2.5,
                       floor=D('.85') if name==Z2 else D('.95'))
    return rule

def z3_permission(root):
    if (root / Z3_HALT).exists():
        return 'Strong cushion manually halted'
    try:
        if (root / Z3_ARM).read_text().strip() == '5':
            return ''
    except OSError:
        pass
    return 'Strong cushion not activated'
