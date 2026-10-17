import copy,unittest
from zoneinfo import ZoneInfo
from study import *

class CausalTests(unittest.TestCase):
 def fixture(self,s='KXNFLGAME'):
  t=ts('2026-09-13T19:00:00Z');iso=lambda n:datetime.fromtimestamp(n,ZoneInfo('UTC')).isoformat()
  mi={'id':'espn:test','start_date':iso(t-7200),'end_date':iso(t+1000),'details':{'home_team_id':'H','away_team_id':'A','espn_team_mapping':{'1':'H','2':'A'},'season_type':'REG'}}
  ms=[{'ticker':'E-H','event_ticker':'E','custom_strike':{'football_team':'H'},'close_time':iso(t+1000),'settlement_ts':iso(t+1300),'result':'yes','rules_secondary':'In a tie both resolve 50/50.'},{'ticker':'E-A','event_ticker':'E','custom_strike':{'football_team':'A'},'close_time':iso(t+1000),'settlement_ts':iso(t+1300),'result':'no','rules_secondary':'In a tie both resolve 50/50.'}]
  def c(st,b,a):return {'end_period_ts':st,'yes_bid':{'open':str(b),'close':str(b)},'yes_ask':{'open':str(a),'close':str(a)},'volume':'10'}
  cm={'E-H':[c(t-9000,.59,.61),c(t,.90,.91),c(t+60,.90,.91)],'E-A':[c(t-9000,.39,.41),c(t,.07,.08),c(t+60,.07,.08)]}
  per=4 if s=='KXNFLGAME' else 3;clock='4:00' if s=='KXNFLGAME' else '16:00';last='0:00' if s=='KXNFLGAME' else '20:00'
  y={'plays':[{'id':'p1','sequenceNumber':'1','wallclock':iso(t-45),'homeScore':17,'awayScore':7,'period':{'number':per},'clock':{'displayValue':clock},'end':{'down':2,'team':{'id':'1'}},'start':{'team':{'id':'2'}},'text':'first'},{'id':'p2','sequenceNumber':'2','wallclock':iso(t+500),'homeScore':17,'awayScore':7,'period':{'number':per},'clock':{'displayValue':last},'text':'END'}]}
  return t,ms,cm,mi,y
 def rows(self,s='KXNFLGAME'):
  t,ms,cm,mi,y=self.fixture(s);return event_candidates(ms,cm,mi,s,y,1)[0]
 def test_historical_decimal_quote_and_tie_complement(self):
  r=self.rows()['p90/base'][0];self.assertAlmostEqual(r['entry'],.91);self.assertTrue(r['complement_allowed']);self.assertTrue(r['prefav']['f60'])
 def test_future_outcome_cannot_change_choices(self):
  t,ms,cm,mi,y=self.fixture();a=event_candidates(ms,copy.deepcopy(cm),mi,'KXNFLGAME',y,1)[0]
  for m in ms:m['result']='no' if m['result']=='yes' else 'yes'
  b=event_candidates(ms,copy.deepcopy(cm),mi,'KXNFLGAME',y,1)[0]
  project=lambda obj:{k:[(r['candidate_id'],r['entry']) for r in rr] for k,rr in obj.items()};self.assertEqual(project(a),project(b))
 def test_side_chosen_before_future_quote(self):
  t,ms,cm,mi,y=self.fixture();cm['E-H'][-1]['yes_ask']['open']='.97';r=event_candidates(ms,cm,mi,'KXNFLGAME',y,1)[0]
  self.assertFalse(r['p90/base']) # opposing NO at92c must not be selected after preferred91c fails
 def test_pregame_asof_not_future(self):
  t,ms,cm,mi,y=self.fixture();cm['E-H'][0]['end_period_ts']+=60;r=event_candidates(ms,cm,mi,'KXNFLGAME',y,1)[0]['p90/base'][0];self.assertFalse(r['prefav']['f60'])
 def test_pregame_five_minute_max_age(self):
  t,ms,cm,mi,y=self.fixture();cm['E-H'][0]['end_period_ts']-=360;r=event_candidates(ms,cm,mi,'KXNFLGAME',y,1)[0]['p90/base'][0];self.assertFalse(r['prefav']['f60'])
 def test_possession_uses_explicit_end_team(self):
  r=self.rows()['p90/base'][0];self.assertTrue(r['leader_has_possession']);self.assertTrue(accepts(r,'multi2_possession',5))
 def test_nhl_elapsed_clock_is_converted(self):
  r=self.rows('KXNHLGAME')['p90/base'][0];self.assertEqual(r['clock'],240);self.assertTrue(accepts(r,'leader',5))
 def test_source_lag_rejects_unavailable_state(self):
  self.assertFalse(self.rows().get('p90/source90'))
 def test_scalar_tie_accounting(self):
  self.assertEqual(payout({'result':'scalar','settlement_value_dollars':'.5'},'no'),.5)

if __name__=='__main__':unittest.main()
