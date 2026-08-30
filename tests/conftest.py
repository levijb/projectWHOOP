from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import pytest

FIXTURE_DIRECTORY = Path(__file__).parent / "fixtures"


@pytest.fixture
def fixture_records() -> dict[str, list[dict[str, Any]]]:
    return {
        name: json.loads((FIXTURE_DIRECTORY / f"{name}.json").read_text(encoding="utf-8"))
        for name in ("cycles", "recovery", "sleep", "workouts")
    }
