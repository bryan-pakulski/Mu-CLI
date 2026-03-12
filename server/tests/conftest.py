import os
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

os.environ["MUCLI_TEST_MODE"] = "true"



import pytest
from server.app.persistence.db import init_db


@pytest.fixture(autouse=True)
async def _init_db_for_tests() -> None:
    await init_db()
