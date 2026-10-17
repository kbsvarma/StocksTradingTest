"""Read-only crypto dashboard. Legacy display archived September 12, 2026."""
import importlib.util
import sys
from pathlib import Path
import streamlit as st

st.set_page_config(page_title='Kalshi · Crypto 15m', page_icon='₿', layout='wide')
PANEL=Path('/home/varma/kalshi_revalidation_2026_09_12/live_v1/dashboard_panel.py')

@st.fragment(run_every='10s')
def dashboard():
    # Streamlit starts in the legacy directory; spec_from_file_location does
    # not make the panel's sibling modules importable. Also covers fragment reruns.
    panel_directory=str(PANEL.parent)
    if panel_directory not in sys.path:sys.path.insert(0,panel_directory)
    spec=importlib.util.spec_from_file_location('crypto_dashboard_panel',PANEL)
    module=importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    module.render(st)

dashboard()
