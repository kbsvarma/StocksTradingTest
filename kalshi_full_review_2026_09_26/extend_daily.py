from pull_candidates import run
# Fresh broad financial/commodity ladder diagnostics, same daily baseline; no parameter tuning.
for t in ['KXINXU','KXNASDAQ100U','KXWTI','KXBRENTD','KXGOLDD','KXSILVERD','KXCOPPERD','KXNATGASD']:
 if t in ['KXINXU','KXNASDAQ100U']:
  # Hourly index strikes can be numerous. Existing complete weekly census preserved;
  # do not mislabel daily 8h strategy as an hourly test.
  continue
 run(t)
