"""Reads configs/Adapters.yaml (keys: image_processing, matrix_multiplication)."""
from __future__ import annotations

from dataclasses import dataclass, field, fields
from pathlib import Path
from typing import Any

import yaml

from application.framework.errors import AdapterConfigError


@dataclass(frozen=True)
class ImageConfig:
    tile_height: int | None = None
    tile_width: int | None = None
    halo: int = 0
    target_chunk_bytes: int = 4 * 1024 * 1024    # used only when tile_height/width are null
    max_image_bytes: int = 512 * 1024 * 1024
    read_mode: str = "color"                     # color | grayscale | unchanged

    def __post_init__(self):
        if self.read_mode not in ("color", "grayscale", "unchanged"):
            raise AdapterConfigError(f"bad read_mode {self.read_mode!r}")
        if self.halo < 0 or self.target_chunk_bytes <= 0 or self.max_image_bytes <= 0:
            raise AdapterConfigError("halo must be >= 0; byte limits must be > 0")
        for v in (self.tile_height, self.tile_width):
            if v is not None and v <= 0:
                raise AdapterConfigError("tile sizes must be > 0")


@dataclass(frozen=True)
class MatrixConfig:
    block_size: int = 512
    dtype: str = "float64"

    def __post_init__(self):
        if self.block_size <= 0:
            raise AdapterConfigError("block_size must be > 0")


@dataclass(frozen=True)
class AdapterConfig:
    max_concurrency: int = 8
    image: ImageConfig = field(default_factory=ImageConfig)
    matrix: MatrixConfig = field(default_factory=MatrixConfig)

    def __post_init__(self):
        if self.max_concurrency < 1:
            raise AdapterConfigError("max_concurrency must be >= 1")


def _build(cls, raw: dict[str, Any] | None):
    raw = raw or {}
    unknown = set(raw) - {f.name for f in fields(cls)}
    if unknown:
        raise AdapterConfigError(f"unknown keys for {cls.__name__}: {sorted(unknown)}")
    return cls(**raw)


def load_adapter_config(path: str | Path | None = None) -> AdapterConfig:
    raw: dict[str, Any] = {}
    if path is not None:
        with open(path, "r", encoding="utf-8") as fh:
            raw = yaml.safe_load(fh) or {}
    unknown = set(raw) - {"max_concurrency", "image_processing", "matrix_multiplication"}
    if unknown:
        raise AdapterConfigError(f"unknown top-level keys: {sorted(unknown)}")
    extra = {"max_concurrency": raw["max_concurrency"]} if "max_concurrency" in raw else {}
    return AdapterConfig(image=_build(ImageConfig, raw.get("image_processing")),
                         matrix=_build(MatrixConfig, raw.get("matrix_multiplication")), **extra)