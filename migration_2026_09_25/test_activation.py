import importlib.util,json,tempfile,unittest,sys
from pathlib import Path
from unittest.mock import patch
from types import SimpleNamespace
spec=importlib.util.spec_from_file_location('activation',Path(__file__).with_name('activate_destination.py'));a=importlib.util.module_from_spec(spec);spec.loader.exec_module(a)
class ActivationTests(unittest.TestCase):
 def setUp(self):
  self.tmp=tempfile.TemporaryDirectory();self.h=Path(self.tmp.name);self.r=self.h/'migration_2026_09_25';self.r.mkdir()
  (self.h/'MIGRATION_HOLD').touch();(self.h/'ibc').mkdir();(self.h/'kalshi_revalidation_2026_09_12/live_v1/state').mkdir(parents=True)
  for name,obj in [('cold_restore_receipt.json',{}),('source_stopped_proof.json',{'checked_at':100,'all_stopped':True}),('migration_source_shutdown_20260925.json',{'system':{'trendbot.service':{'active':'active','enabled':'enabled'},'kalshi-dashboard.service':{'active':'active','enabled':'enabled'}},'user':{'kalshi-crypto-z2-live.service':{'active':'active','enabled':'enabled'},'advisor-model.service':{'active':'inactive','enabled':'enabled'}}})]:
   (self.r/name).write_text(json.dumps(obj))
  (self.r/'ibc-config-before-readonly.ini').write_text('ReadOnlyApi=no\n');self.calls=[]
  self.patches=[patch.object(a,'H',self.h),patch.object(a,'R',self.r),patch.object(a.os,'geteuid',return_value=0),patch.object(a.os,'chown'),patch.object(a.socket,'gethostname',return_value='ashish-home'),patch.object(a.time,'time',return_value=100),patch.object(a.time,'sleep'),patch.object(sys,'argv',['activate','--execute']),patch.object(a,'run',side_effect=lambda cmd,**kw:self.calls.append(cmd)),patch.object(a.subprocess,'run',return_value=SimpleNamespace(returncode=0)),patch.object(a,'Path',side_effect=lambda p:self.h/('etc/'+p.removeprefix('/etc/')) if str(p).startswith('/etc/') else Path(p))]
  for p in self.patches:p.start()
 def tearDown(self):
  for p in reversed(self.patches):p.stop()
  self.tmp.cleanup()
 def test_success_preserves_sizing_and_skips_inactive_model(self):
  a.main();self.assertTrue((self.r/'activation_receipt.json').exists())
  self.assertEqual((self.h/'kalshi_revalidation_2026_09_12/live_v1/state/ARM_2CT').read_text(),'2\n')
  self.assertFalse(any('advisor-model.service' in c for c in self.calls))
  self.assertTrue(any('--user' in c and 'kalshi-dashboard.service' in c for c in self.calls))
 def test_stale_source_proof_refuses_activation(self):
  (self.r/'source_stopped_proof.json').write_text(json.dumps({'checked_at':-1000,'all_stopped':True}))
  with self.assertRaises(SystemExit):a.main()
  self.assertEqual(self.calls,[]);self.assertTrue((self.h/'MIGRATION_HOLD').exists())
 def test_gateway_failure_does_not_start_trading(self):
  with patch.object(a.subprocess,'run',return_value=SimpleNamespace(returncode=1)):
   with self.assertRaises(SystemExit):a.main()
  self.assertTrue((self.h/'MIGRATION_HOLD').exists());self.assertFalse(any(c[-1]=='trendbot.service' for c in self.calls));self.assertFalse((self.h/'kalshi_revalidation_2026_09_12/live_v1/state/ARM_2CT').exists())
 def test_already_activated_refuses_repeat(self):
  (self.r/'activation_receipt.json').touch()
  with self.assertRaises(SystemExit):a.main()
  self.assertEqual(self.calls,[])
if __name__=='__main__':unittest.main()
