"""Global production-state safety rail for the Advisor test suite."""
from __future__ import annotations

import os
import tempfile

# Set before pytest imports test modules. Modules that honor ADVISOR_DATA_DIR
# can never resolve their data paths into the checked-out operational tree.
os.environ.setdefault("ADVISOR_DATA_DIR",
                      tempfile.mkdtemp(prefix="advisor_pytest_data_"))

import pytest

@pytest.fixture(autouse=True)
def isolate_model_credentials(monkeypatch,tmp_path):
    """Automated tests must never inherit a real service credential."""
    monkeypatch.setenv('ADVISOR_RESEARCH_ENV',str(tmp_path/'no-model-credentials'))
    for key in ('OPENAI_API_KEY','GEMINI_API_KEY','ADVISOR_RESEARCH_PROVIDER','ADVISOR_RESEARCH_MODEL','ADVISOR_RESEARCH_REASONING'):
        monkeypatch.delenv(key,raising=False)
