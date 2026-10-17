import copy,unittest
from late import *

class CausalityTests(unittest.TestCase):
 def fixture(self):
  start=ts('2026-09-01T18:00:00Z');t=start+7200;iso=lambda n:datetime.fromtimestamp(n,ZoneInfo('UTC')).isoformat()
  mile={'start_date':iso(start),'end_date':iso(t+1000),'details':{'home_team_id':'H','away_team_id':'A'}}
  markets=[{'ticker':'GAME-H','event_ticker':'GAME','custom_strike':{'baseball_team':'H'},'close_time':iso(t+1200),'settlement_ts':iso(t+1500),'result':'yes'},{'ticker':'GAME-A','event_ticker':'GAME','custom_strike':{'baseball_team':'A'},'close_time':iso(t+1200),'settlement_ts':iso(t+1500),'result':'no'}]
  def c(st,b,a,ob=None,oa=None):return {'end_period_ts':st,'yes_bid':{'close_dollars':str(b),'open_dollars':str(b if ob is None else ob)},'yes_ask':{'close_dollars':str(a),'open_dollars':str(a if oa is None else oa)},'volume_fp':'5'}
  cm={'GAME-H':[c(start-1800,.64,.66),c(t,.89,.90),c(t+60,.89,.90)],'GAME-A':[c(start-1800,.34,.36),c(t,.08,.09),c(t+60,.08,.09)]}
  pbp={'pbp':{'periods':[{'period_number':9,'half':'bottom','home_score':2,'away_score':1,'events':[{'wall_clock':t+500,'sequence':2,'home_points':2,'away_points':1,'half':'bottom','outs':3}]},{'period_number':8,'half':'bottom','home_score':2,'away_score':1,'events':[{'wall_clock':t-60,'sequence':1,'home_points':2,'away_points':1,'half':'bottom','outs':1}]}]}}
  return t,mile,markets,cm,pbp
 def test_future_result_does_not_select_bet(self):
  t,mi,ms,cm,pb=self.fixture();a=event_rows(ms,cm,mi,'KXMLBGAME',pb,None,.5)[0];flipped=copy.deepcopy(ms)
  for m in flipped:m['result']='no' if m['result']=='yes' else 'yes'
  b=event_rows(flipped,cm,mi,'KXMLBGAME',pb,None,.5)[0]
  project=lambda xs:{k:[(r['ticker'],r['side'],r['t'],r['entry']) for r in rr] for k,rr in xs.items()}
  self.assertEqual(project(a),project(b));self.assertTrue(a)
 def test_choice_fixed_before_execution(self):
  t,mi,ms,cm,pb=self.fixture();cm['GAME-H'][-1]['yes_ask']['open_dollars']='.95';rows=event_rows(ms,cm,mi,'KXMLBGAME',pb,None,.5)[0]
  self.assertFalse(any(r['t']==t for r in rows['original_favorite/very_late/p90/base']))
 def test_period_final_score_not_used(self):
  t,mi,ms,cm,pb=self.fixture();a=event_rows(ms,cm,mi,'KXMLBGAME',pb,None,.5)[0]
  for p in pb['pbp']['periods']:p['home_score']=999;p['away_score']=0
  mi['details']['home_last_games']=[{'winner':False,'points':0}]
  b=event_rows(ms,cm,mi,'KXMLBGAME',pb,None,.5)[0];self.assertEqual(a,b)
 def test_future_play_does_not_change_previous_state(self):
  t,mi,ms,cm,pb=self.fixture();a=parse_states(pb,mi,'KXMLBGAME')[0];pb['pbp']['periods']=pb['pbp']['periods'][1:];b=parse_states(pb,mi,'KXMLBGAME')[0]
  self.assertEqual([r for r in a if r['wall']<=t],b)
 def test_feed_delay_and_terminal_gate(self):
  t,mi,ms,cm,pb=self.fixture();st,_,_=parse_states(pb,mi,'KXMLBGAME');stamps=[r['wall'] for r in st]
  self.assertIsNone(at(st,stamps,t-40,30));self.assertEqual(at(st,stamps,t,30)['home'],2)
  self.assertIsNone(eligible(st[-1],'KXMLBGAME','very_late','leader',t+540,30,'H','A',None))
 def test_fee_change_boundary(self):
  change=ts('2026-08-07T04:59:45.131Z')
  self.assertEqual(fee_multiplier_at('KXMLBGAME',change-1,.5),1)
  self.assertEqual(fee_multiplier_at('KXMLBGAME',change,.5),.5)

if __name__=='__main__':unittest.main()
