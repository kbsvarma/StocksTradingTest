import hashlib
import json
from pathlib import Path
import tempfile
import sqlite3
import unittest
from unittest.mock import patch
import activate
from lanes import Z2,Z3

def good_status():
    return dict(ts=101,mode='LIVE',armed=True,contracts=5,max_contracts=20,max_risk='19.00',daily_loss_budget='10',
        lanes={Z2:dict(enabled=True,contracts=5,z_min=2,execution_cap='0.939'),
               Z3:dict(enabled=True,contracts=5,z_min=3,execution_cap='0.975',signal_floor='0.93',signal_ceiling='0.98',max_contracts=20,max_risk='20',daily_loss_budget='25')})

class Activation(unittest.TestCase):
    def prepare_tree(self,root):
        stage=root/'stage';live=root/'live';stage.mkdir();live.mkdir();state=live/'state';state.mkdir()
        for n in activate.FILES:
            (stage/n).write_text('NEW '+n)
            if n!='lanes.py':(live/n).write_text('OLD '+n)
        (state/'ARM_5CT').write_text('5')
        (state/'status.json').write_text('{}')
        with sqlite3.connect(state/'orders.sqlite3') as c:
            c.execute('create table orders(ticker text, record text)');c.execute('insert into orders values (?,?)',('old','preserve'))
        return stage,live
    def test_failure_before_start_restores_source_only(self):
        with tempfile.TemporaryDirectory() as tmp:
            stage,live=self.prepare_tree(Path(tmp));calls=[]
            def run(cmd,**kw):
                calls.append(cmd)
                if '-c' in cmd:raise RuntimeError('import failure')
            with patch.object(activate,'P',stage),patch.object(activate,'LIVE',live),patch.object(activate,'verify_hashes'),patch.object(activate,'live_readiness'),patch.object(activate.subprocess,'run',side_effect=run):
                with self.assertRaisesRegex(RuntimeError,'import failure'):activate.install({}, {})
            self.assertEqual((live/'engine.py').read_text(),'OLD engine.py')
            self.assertFalse((live/'lanes.py').exists());self.assertFalse((live/'state'/activate.Z3_ARM).exists())
            self.assertEqual((live/'state/ARM_5CT').read_text(),'5')
            with sqlite3.connect(live/'state/orders.sqlite3') as c:self.assertEqual(c.execute('select record from orders').fetchone()[0],'preserve')
            self.assertEqual(sum('start' in c for c in calls),1)
    def test_uncertain_health_after_restart_never_rolls_back_new_orders(self):
        with tempfile.TemporaryDirectory() as tmp:
            stage,live=self.prepare_tree(Path(tmp));calls=[]
            def run(cmd,**kw):
                calls.append(cmd)
                if 'start' in cmd:
                    with sqlite3.connect(live/'state/orders.sqlite3') as c:c.execute('insert into orders values (?,?)',('new','pending'))
            with patch.object(activate,'P',stage),patch.object(activate,'LIVE',live),patch.object(activate,'verify_hashes'),patch.object(activate,'live_readiness'),patch.object(activate.subprocess,'run',side_effect=run),patch.object(activate.time,'sleep'):
                with self.assertRaisesRegex(RuntimeError,'no automatic rollback'):activate.install({}, {})
            self.assertEqual((live/'engine.py').read_text(),'NEW engine.py')
            self.assertEqual((live/'state'/activate.Z3_ARM).read_text().strip(),'5')
            with sqlite3.connect(live/'state/orders.sqlite3') as c:self.assertEqual(c.execute('select count(*) from orders').fetchone()[0],2)
            self.assertEqual(sum('start' in c for c in calls),1)
    def test_new_heartbeat_requires_actual_z3_enablement(self):
        self.assertTrue(activate.healthy(good_status(),100))
        for field,value in [('enabled',False),('contracts',1),('execution_cap','0.98'),('z_min',2),('daily_loss_budget','10')]:
            d=good_status();d['lanes'][Z3][field]=value
            self.assertFalse(activate.healthy(d,100))
        self.assertFalse(activate.healthy(good_status(),102))
    def test_stale_or_halted_heartbeat_is_not_success(self):
        for k in ['halted','stopped','error']:
            d=good_status();d[k]=True;self.assertFalse(activate.healthy(d,100))
    def test_changed_source_and_unapproved_paths_rejected(self):
        with tempfile.TemporaryDirectory() as tmp:
            root=Path(tmp);(root/'engine.py').write_text('one')
            hashes={'engine.py':hashlib.sha256(b'one').hexdigest()}
            activate.verify_hashes(root,hashes)
            (root/'engine.py').write_text('two')
            with self.assertRaises(RuntimeError):activate.verify_hashes(root,hashes)
            with self.assertRaises(RuntimeError):activate.verify_hashes(root,{'../bad':'anything'})
    def test_default_and_check_modes_cannot_install_or_restart(self):
        with tempfile.TemporaryDirectory() as tmp:
            root=Path(tmp)
            for n in ['original_hashes.json','staged_hashes.json']:(root/n).write_text('{}')
            with patch.object(activate,'P',root),patch.object(activate,'verify_hashes'),patch.object(activate,'live_readiness',return_value={}),patch.object(activate.subprocess,'run') as run,patch.object(activate,'install') as install:
                with patch('builtins.print'):
                    activate.main([]);activate.main(['--check'])
                install.assert_not_called()
                calls=[c.args[0] for c in run.call_args_list]
                self.assertFalse(any('stop' in c or 'start' in c or '--live' in c for c in calls))
    def test_current_source_mismatch_prevents_execute(self):
        with tempfile.TemporaryDirectory() as tmp:
            root=Path(tmp)
            for n in ['original_hashes.json','staged_hashes.json']:(root/n).write_text('{}')
            with patch.object(activate,'P',root),patch.object(activate,'verify_hashes',side_effect=[None,RuntimeError('changed')]),patch.object(activate,'install') as install:
                with self.assertRaises(RuntimeError):activate.main(['--execute'])
                install.assert_not_called()

if __name__=='__main__':unittest.main()
