import hashlib,json,tempfile,unittest,sqlite3
from pathlib import Path
from unittest.mock import patch
from contextlib import ExitStack,redirect_stdout
from io import StringIO
from types import SimpleNamespace
import activate

class Upgrade(unittest.TestCase):
    def test_only_execution_and_display_files_are_installed(self):
        self.assertNotIn('lanes.py',activate.FILES)
        self.assertNotIn('sizing.py',activate.FILES)
        self.assertTrue({'engine.py','runner.py','book_feed.py','market_data.py','observe_execution.py'}.issubset(activate.FILES))
    def test_source_guard_rejects_changed_or_unexpected_paths(self):
        with tempfile.TemporaryDirectory() as directory:
            p=Path(directory);(p/'runner.py').write_text('frozen')
            expected={'runner.py':hashlib.sha256(b'frozen').hexdigest()}
            activate.verify_hashes(p,expected)
            (p/'runner.py').write_text('changed')
            with self.assertRaises(RuntimeError):activate.verify_hashes(p,expected)
            with self.assertRaises(RuntimeError):activate.verify_hashes(p,{'../other':'x'})
    def test_health_requires_new_retry_config_and_both_lanes(self):
        root=Path(__file__).resolve().parents[1]
        d=json.loads((root/'inputs.json').read_text())['live_status']
        self.assertFalse(activate.healthy(d,0))
        d.update(execution_checks_max=24,execution_retry_deadline_seconds=25,max_ioc_submissions=3,zero_fill_retry_enabled=True,pending_reconcile_seconds=1,operations_version=1,book_feed={'mode':'BOOK_HINTS_WITH_REST_VALIDATION'})
        self.assertTrue(activate.healthy(d,0))
        d['lanes']['premium_z3']['enabled']=False
        self.assertFalse(activate.healthy(d,0))
    def test_default_command_never_calls_install(self):
        with patch.object(activate,'verify_hashes'),patch.object(activate.subprocess,'run',return_value=SimpleNamespace(stdout='',stderr='',returncode=0)),patch.object(activate,'live_readiness',return_value={}),patch.object(activate,'install') as install:
            activate.main(['--check'])
            install.assert_not_called()
    def exercise_install(self,fail=False):
        with tempfile.TemporaryDirectory() as tmp:
            root=Path(tmp);live=root/'live';stage=root/'stage';live.mkdir();stage.mkdir();(live/'state').mkdir()
            (live/'state/ARM_5CT').write_text('5')
            (live/'state/status.json').write_text(json.dumps({'lanes':{}}))
            db=live/'state/orders.sqlite3'
            with sqlite3.connect(db) as c:c.execute('create table sentinel(x)');c.execute('insert into sentinel values (42)')
            old={};new={}
            for name in activate.FILES:
                (stage/name).write_text('new '+name);new[name]=hashlib.sha256((stage/name).read_bytes()).hexdigest()
                if name in ('runner.py','engine.py'):
                    (live/name).write_text('old '+name);old[name]=hashlib.sha256((live/name).read_bytes()).hexdigest()
            def run(args,**kwargs):
                if fail and '-c' in args:raise RuntimeError('installed import failed')
            with ExitStack() as stack:
                stack.enter_context(redirect_stdout(StringIO()))
                for target,value in [('P',stage),('LIVE',live)]:stack.enter_context(patch.object(activate,target,value))
                stack.enter_context(patch.object(activate,'live_readiness',return_value={}))
                stack.enter_context(patch.object(activate,'healthy',return_value=True))
                stack.enter_context(patch.object(activate,'install_observer'))
                stack.enter_context(patch.object(activate.subprocess,'run',side_effect=run))
                stack.enter_context(patch.object(activate.time,'sleep'))
                if fail:
                    with self.assertRaisesRegex(RuntimeError,'installed import failed'):activate.install(new,old)
                else:activate.install(new,old)
            with sqlite3.connect(db) as c:self.assertEqual(c.execute('select x from sentinel').fetchone()[0],42)
            self.assertEqual((live/'state/ARM_5CT').read_text(),'5')
            if fail:
                self.assertEqual((live/'runner.py').read_text(),'old runner.py');self.assertFalse((live/'book_feed.py').exists())
            else:
                activate.verify_hashes(live,new)
                self.assertTrue((stage/'activation_receipt.json').exists())
    def test_install_preserves_ledger_and_arms(self):self.exercise_install()
    def test_prestart_failure_restores_sources_never_ledger(self):self.exercise_install(fail=True)

if __name__=='__main__':unittest.main()
