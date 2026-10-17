"""Read cover-page common-share counts omitted by dimensionless SEC company facts."""
from collections import defaultdict
from decimal import Decimal,InvalidOperation


def cover_shares(soup):
    groups=defaultdict(dict);all_classes=set()
    for node in soup.find_all(lambda n:n.name and n.name.lower().endswith('nonfraction') and n.get('name','').lower()=='dei:entitycommonstocksharesoutstanding'):
        context=soup.find(id=node.get('contextref'))
        unit=soup.find(id=node.get('unitref'))
        if not context or not unit:continue
        measures=unit.find_all(lambda n:n.name and n.name.lower().endswith(':measure'))
        if len(measures)!=1 or measures[0].get_text(strip=True).lower().split(':')[-1]!='shares':continue
        if context.find(lambda n:n.name and n.name.lower().endswith(':typedmember')):continue
        instant=context.find(lambda n:n.name and n.name.lower().endswith(':instant'))
        if not instant:continue
        members=context.find_all(lambda n:n.name and n.name.lower().endswith(':explicitmember'))
        if any(not m.get('dimension','').lower().endswith('statementclassofstockaxis') for m in members):continue
        if len(members)>1:continue
        label=members[0].get_text(strip=True) if members else 'all_common_classes'
        if members:all_classes.add(label)
        try:
            value=Decimal(node.get_text(strip=True).replace(',',''))*(Decimal(10)**int(node.get('scale','0')))
        except (InvalidOperation,ValueError,OverflowError):continue
        if not value.is_finite() or value<=0 or value!=value.to_integral_value() or node.get('sign')=='-':continue
        date=instant.get_text(strip=True)
        from datetime import date as calendar_date
        try:calendar_date.fromisoformat(date)
        except ValueError:continue
        value=int(value)
        if label in groups[date] and groups[date][label]!=value:return None
        groups[date][label]=value
    if not groups:return None
    end=max(groups);counts=groups[end]
    if 'all_common_classes' in counts:
        value=counts['all_common_classes'];basis='reported aggregate common shares outstanding'
    elif len(counts)>=2 and set(counts)==all_classes:
        value=sum(counts.values());basis='sum of cover-page common share classes at one reporting date'
    else:return None
    return {'value':value,'period_end':end,'classes':counts,'basis':basis}
