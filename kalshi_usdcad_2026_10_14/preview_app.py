
import json,sys
from pathlib import Path
import streamlit as st
P=Path(__file__).resolve().parent
sys.path.insert(0,str(P/'candidate'))
import dashboard_panel as panel
panel.STATUS=P/'preview/status.json'
panel.read_ledger=lambda:json.loads((P/'preview/ledger.json').read_text())
panel.read_attempts=lambda:[]
panel.get_quotes=lambda:json.loads((P/'preview/quotes.json').read_text())
panel.render(st)
