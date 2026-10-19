"""Loads configs/Adapters.yaml so block and tile sizes are never hardcoded."""

from __future__ import annotations

from pathlib import Path

import yaml

DEFAULT_PATH = Path("configs/Adapters.yaml")


def load_adapter_config(path: str | Path = DEFAULT_PATH) -> dict:
    with Path(path).open("r", encoding="utf-8") as fh:
        return yaml.safe_load(fh) or {}