import sys
from pathlib import Path
import streamlit as st
sys.path.insert(0,str(Path(__file__).resolve().parent))
import dashboard_panel
st.set_page_config(page_title='Kalshi Desk preview',layout='wide')
dashboard_panel.render(st)
