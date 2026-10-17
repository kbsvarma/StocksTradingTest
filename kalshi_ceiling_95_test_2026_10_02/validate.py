"""Each variant is tested in an isolated process; outbound sockets blocked."""
from pathlib import Path
import argparse
import socket
import sys
import unittest
from unittest.mock import patch

P=Path(__file__).resolve().parent
parser=argparse.ArgumentParser()
parser.add_argument('variant',choices=('reference','ceiling935','signal95','ceiling95'))
args=parser.parse_args()
sys.path.insert(0,str(P/args.variant))
sys.path.insert(0,str(P/'tests'))
with patch.object(socket.socket,'connect',side_effect=AssertionError('Network forbidden in tests')):
    result=unittest.TextTestRunner(verbosity=2).run(unittest.defaultTestLoader.discover(str(P/'tests')))
sys.exit(0 if result.wasSuccessful() else 1)
