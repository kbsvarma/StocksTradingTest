"""Isolated UI preview. Reads production records, never writes them."""
import sys
from pathlib import Path
import streamlit as st

P=Path(__file__).resolve().parent
LIVE=P.parent.parent/'kalshi_revalidation_2026_09_12/live_v1'
sys.path.insert(0,str(LIVE))
sys.path.insert(0,str(P))
st.set_page_config(page_title='Kalshi Desk · UI preview',layout='wide')
import dashboard_panel
dashboard_panel.render(st)
