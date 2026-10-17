import copy
from types import SimpleNamespace
import hashlib
import json
from pathlib import Path
import sqlite3
import tempfile
import unittest
from unittest.mock import patch
import activate
import runner
from lanes import ALL_ASSETS,NEW_ASSETS,NONCRYPTO_ARM,WTI_ARM,Z2,Z3,lane_for,route_for

class Release(unittest.TestCase):
    def healthy(self):
        assets={}
        for a in ALL_ASSETS:
            rules={}
            for l in (Z2,Z3):
                r=lane_for(dict(asset=a,lane=l))
                rules[l]=dict(z_base_at_5m=r.z,z_slope=r.z_slope,min_minutes=r.min_minutes,max_minutes=r.max_minutes,signal_floor=str(r.floor),signal_ceiling=str(r.ceiling),execution_cap=str(r.cap))
            assets[a]=dict(enabled=True,contracts=5,exchange_index=route_for(dict(asset=a)),rules=rules)
        return dict(ts=101,mode='LIVE',armed=True,multiasset_version=2,contracts=5,max_contracts=20,max_risk='19.00',daily_loss_budget='15',
                    execution_checks_max=24,execution_retry_deadline_seconds=25,max_ioc_submissions=3,zero_fill_retry_enabled=True,pending_reconcile_seconds=1,
                    assets=assets,lanes={Z3:dict(enabled=True,max_contracts=20,max_risk='20',daily_loss_budget='25')})
    def test_heartbeat_must_confirm_every_profile_and_limit(self):
        d=self.healthy();self.assertTrue(activate.healthy(d,100))
        for asset in ALL_ASSETS:
            wrong=copy.deepcopy(d);wrong['assets'][asset]['enabled']=False
            self.assertFalse(activate.healthy(wrong,100))
            wrong=copy.deepcopy(d);wrong['assets'][asset]['rules'][Z2]['z_base_at_5m']+=1
            self.assertFalse(activate.healthy(wrong,100))
        for k,v in [('max_risk','20'),('daily_loss_budget','10'),('stopped',True),('ts',99)]:
            wrong=dict(d,**{k:v});self.assertFalse(activate.healthy(wrong,100))
    def test_changed_or_unsafe_source_cannot_install(self):
        with tempfile.TemporaryDirectory() as tmp:
            root=Path(tmp);(root/'engine.py').write_text('x');wanted=hashlib.sha256(b'x').hexdigest()
            activate.verify_hashes(root,{'engine.py':wanted})
            for name,digest in [('engine.py','bad'),('../engine.py',wanted)]:
                with self.assertRaises(RuntimeError):activate.verify_hashes(root,{name:digest})
    def test_default_mode_never_installs(self):
        with tempfile.TemporaryDirectory() as tmp:
            root=Path(tmp);staged={n:'hash' for n in activate.FILES};old=dict(staged)
            (root/'original_hashes.json').write_text(json.dumps(old));(root/'staged_hashes.json').write_text(json.dumps(staged))
            with patch.object(activate,'P',root),patch.object(activate,'verify_hashes'),patch.object(activate,'verify_bundle'),patch.object(activate.subprocess,'run',return_value=SimpleNamespace(returncode=0)),patch.object(activate,'source_readiness',return_value={}),patch.object(activate,'live_readiness',return_value={}),patch.object(activate,'install') as install:
                activate.main([]);install.assert_not_called()
    def test_failed_install_restores_source_and_arm_not_ledger(self):
        with tempfile.TemporaryDirectory() as tmp:
            p=Path(tmp);live=p/'live';stage=p/'stage';live.mkdir();stage.mkdir();state=live/'state';state.mkdir()
            original={};staged={}
            for n in activate.FILES:
                (stage/n).write_text('new '+n);staged[n]=hashlib.sha256((stage/n).read_bytes()).hexdigest()
                if True:
                    (live/n).write_text('old '+n);original[n]=hashlib.sha256((live/n).read_bytes()).hexdigest()
            with sqlite3.connect(state/'orders.sqlite3') as db:db.execute('create table sentinel(value text)');db.execute("insert into sentinel values ('keep')")
            (state/'HALT').write_text('operator state');(state/'ARM_5CT').write_text('5')
            (state/WTI_ARM).write_text('old arm')
            def fail_import(args,**kw):
                if '-c' in args:raise RuntimeError('injected import failure')
            with patch.object(activate,'LIVE',live),patch.object(activate,'P',stage),patch.object(activate,'live_readiness'),patch.object(activate.subprocess,'run',side_effect=fail_import):
                with self.assertRaisesRegex(RuntimeError,'injected'):activate.install(staged,original)
            activate.verify_hashes(live,original);self.assertTrue((live/'pyth_data.py').exists())
            self.assertEqual((state/WTI_ARM).read_text(),'old arm');self.assertEqual((state/'HALT').read_text(),'operator state')
            with sqlite3.connect(state/'orders.sqlite3') as db:self.assertEqual(db.execute('select value from sentinel').fetchone()[0],'keep')
    def test_fx_display_failure_does_not_poison_other_cash(self):
        from test_multiasset import RoutingExchange
        class Engine:pass
        e=Engine();e.exchange=RoutingExchange();e.last_account={'balances':{'2':'50','0':'20'}}
        def fail(**kw):raise TimeoutError('FX down')
        e.exchange.balance=fail
        with patch.object(runner,'asset_permission',return_value=''):runner.refresh_fx_cash(e)
        self.assertEqual(e.last_account['balances'],{'2':'50'});self.assertIn('FX down',e.last_account['fx_cash_error'])

if __name__=='__main__':unittest.main()
