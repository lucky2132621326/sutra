"""
Shared test setup.

Several suites intentionally mutate campus.db (registering for events,
filling them to capacity), while others assert on exact seeded values like
"the Thursday workshop has 2 seats left". Without isolation the first kind
silently breaks the second depending on file ordering — which is exactly
what happened once the call-efficiency suite started running approvals.

Re-seeding per module keeps modules independent while costing far less than
re-seeding per test.
"""
import subprocess
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))


@pytest.fixture(scope="module", autouse=True)
def reseed_campus_db():
    """Restore campus.db to its pristine seeded state before each test module.

    The SQLAlchemy engine must release its file handle first: on Windows,
    seed.py's unlink() fails outright while any connection is open.
    """
    from apps.api.tools.db import engine

    engine.dispose()
    result = subprocess.run(
        [sys.executable, str(ROOT / "scripts" / "seed.py")],
        cwd=ROOT, capture_output=True, text=True,
    )
    if result.returncode != 0:
        pytest.fail(f"re-seed failed:\n{result.stdout}\n{result.stderr}")
    yield
    engine.dispose()
