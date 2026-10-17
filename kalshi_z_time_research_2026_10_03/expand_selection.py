"""Second-pass family coverage, selected solely from the first 30 days.

The first twelve diversity leaders were concentrated in lower-price profiles.
Evaluate every eligible asset/profile/time/slope-family winner, without ranking
or selecting on later results. Explicit post-plan coverage extension.
"""
from study import *
original=json.loads((P/'shortlist_frozen.json').read_text())
chosen={c['id']:c for c in original['candidates']};seen=set()
for c in json.loads((P/'ranking_selection_only.json').read_text()):
    if not c['eligible']:continue
    timefamily='late' if c['hi']<=2 else 'early' if c['lo']>=11 else 'middle' if c['lo']>=3 and c['hi']<=10 else 'mixed'
    key=(c['assets'],c['profile'],c['family'],timefamily)
    if key in seen:continue
    seen.add(key)
    if c['id'] not in chosen:chosen[c['id']]=dict(c,selection_reason='second-pass winner in additional development/validation asset/price/time/slope family')
out=dict(original,created_utc=dt.datetime.now(dt.timezone.utc).isoformat(),candidates=list(chosen.values()),later_results_not_evaluated=False,
    extension='After initial shortlist results, cover all eligible development/validation families; no later metrics are read by this selector. Retrospective exploratory extension, not a new holdout.',
    selector_sha256=digest(P/'expand_selection.py'),initial_shortlist_sha256=digest(P/'shortlist_frozen.json'))
save('expanded_shortlist_frozen.json',out)
print('families',len(seen),'total candidates',len(chosen))
