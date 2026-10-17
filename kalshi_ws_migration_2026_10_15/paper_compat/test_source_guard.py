import hashlib,json,tempfile,unittest
from pathlib import Path
from unittest.mock import patch
import observer
class SourceGuard(unittest.TestCase):
 def setUp(self):
    self.tmp=tempfile.TemporaryDirectory();self.root=Path(self.tmp.name);self.live=self.root/'live';self.live.mkdir()
    self.o=observer.Observer.__new__(observer.Observer);self.o.root=self.root
    self.old={}
    for name in ('engine.py','runner.py','market_data.py','lanes.py','sizing.py'):
        p=self.live/name;p.write_text('old '+name);self.old[name]=hashlib.sha256(p.read_bytes()).hexdigest()
    self.o.cfg={'live_source_hashes':self.old};self.mock=patch.object(observer,'LIVE',self.live);self.mock.start()
 def tearDown(self):self.mock.stop();self.tmp.cleanup()
 def update(self):
    new={}
    for name in ('engine.py','runner.py','market_data.py','stream_transport.py'):
        p=self.live/name;p.write_text('new '+name);new[name]=hashlib.sha256(p.read_bytes()).hexdigest()
    self.manifest=dict(before=self.old,updates=new)
    self.write()
 def write(self):(self.root/'reviewed_live_source_hashes.json').write_text(json.dumps(self.manifest))
 def test_original_still_accepted(self):self.assertEqual(self.o.source_guard(),'')
 def test_exact_reviewed_transport_accepted(self):self.update();self.assertEqual(self.o.source_guard(),'')
 def test_unreviewed_source_drift_blocks(self):
    (self.live/'runner.py').write_text('drift');self.assertEqual(self.o.source_guard(),'production_source_changed')
 def test_price_strategy_drift_cannot_be_reviewed(self):
    self.update();(self.live/'lanes.py').write_text('changed');self.manifest['updates']['lanes.py']=hashlib.sha256(b'changed').hexdigest();self.write()
    self.assertEqual(self.o.source_guard(),'invalid_reviewed_source_manifest')
 def test_missing_new_transport_blocks(self):
    self.update();(self.live/'stream_transport.py').unlink();self.assertEqual(self.o.source_guard(),'production_source_changed')
 def test_partial_revision_blocks(self):
    self.update();(self.live/'engine.py').write_text('old engine.py');self.assertEqual(self.o.source_guard(),'production_source_changed')
 def test_forged_old_revision_blocks(self):
    self.update();self.manifest['before']={};self.write();self.assertEqual(self.o.source_guard(),'invalid_reviewed_source_manifest')
 def test_malformed_manifest_blocks(self):
    self.update();(self.root/'reviewed_live_source_hashes.json').write_text('{');self.assertEqual(self.o.source_guard(),'invalid_reviewed_source_manifest')
