"""Global production-state safety rail for the Advisor test suite."""
from __future__ import annotations

import os
import tempfile

# Set before pytest imports test modules. Modules that honor ADVISOR_DATA_DIR
# can never resolve their data paths into the checked-out operational tree.
os.environ.setdefault("ADVISOR_DATA_DIR",
                      tempfile.mkdtemp(prefix="advisor_pytest_data_"))
