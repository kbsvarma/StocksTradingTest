"""Validate isolated candidate and replay with outbound networking disabled."""
from pathlib import Path
import socket
import sys
import unittest
from unittest.mock import patch

P=Path(__file__).resolve().parent
sys.path.insert(0,str(P/'candidate'))
sys.path.insert(0,str(P/'tests'))
with patch.object(socket.socket,'connect',side_effect=AssertionError('Network forbidden in tests')):
    result=unittest.TextTestRunner(verbosity=2).run(unittest.defaultTestLoader.discover(str(P/'tests')))
sys.exit(0 if result.wasSuccessful() else 1)
