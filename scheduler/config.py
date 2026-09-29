# Purpose:
# Loads SchedulerConfig from a JSON configuration file.

import json
from pathlib import Path

from common.types.scheduler import SchedulerConfig


def load_scheduler_config(
    path: str | Path,
) -> SchedulerConfig:

    path = Path(path)

    with path.open(
        "r",
        encoding="utf-8",
    ) as file:
        data = json.load(file)

    return SchedulerConfig(**data)
