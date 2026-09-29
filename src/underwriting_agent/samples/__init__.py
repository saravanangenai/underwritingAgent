"""Synthetic sample submissions used by the API docs and the tests."""

import json
from pathlib import Path
from typing import Any

SAMPLE_NAMES = ("clean", "high_risk", "decline", "incomplete", "acord_text")


def load_sample(name: str) -> dict[str, Any]:
    return json.loads((Path(__file__).parent / f"{name}.json").read_text())
