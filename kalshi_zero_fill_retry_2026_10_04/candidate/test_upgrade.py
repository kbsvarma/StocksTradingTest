import hashlib,json,tempfile,unittest
from pathlib import Path
from unittest.mock import patch
import activate

class Upgrade(unittest.TestCase):
    def test_only_execution_and_display_files_are_installed(self):
        self.assertEqual(set(activate.FILES),{'engine.py','runner.py','execution_retry.py','dashboard_panel.py'})
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
        d.update(execution_checks_max=24,execution_retry_deadline_seconds=25,max_ioc_submissions=3,zero_fill_retry_enabled=True,pending_reconcile_seconds=1)
        self.assertTrue(activate.healthy(d,0))
        d['lanes']['premium_z3']['enabled']=False
        self.assertFalse(activate.healthy(d,0))
    def test_default_command_never_calls_install(self):
        with patch.object(activate,'verify_hashes'),patch.object(activate.subprocess,'run'),patch.object(activate,'live_readiness',return_value={}),patch.object(activate,'install') as install:
            activate.main(['--check'])
            install.assert_not_called()

if __name__=='__main__':unittest.main()
