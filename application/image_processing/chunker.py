"""Pure tiling math for images: no DSM, no I/O. Tiles are row-major.

Each tile has a CORE box (pixels it owns) and a PADDED box (core + halo, clamped to the
image). Padded tiles are what get stored, so neighbourhood filters work; reassemble()
crops the halo away, so tiles never overlap in the output (no seams)."""
from __future__ import annotations

from dataclasses import dataclass
from math import isqrt
from typing import Any, Sequence

import numpy as np

from application.framework.errors import ChunkingError, ReconstructionError


@dataclass(frozen=True)
class TileSpec:
    index: int
    row: int
    col: int
    y0: int; y1: int; x0: int; x1: int          # core box (end-exclusive)
    py0: int; py1: int; px0: int; px1: int      # padded box

    @property
    def core_shape(self) -> tuple[int, int]:
        return (self.y1 - self.y0, self.x1 - self.x0)

    @property
    def padded_shape(self) -> tuple[int, int]:
        return (self.py1 - self.py0, self.px1 - self.px0)

    def to_layout(self) -> dict[str, Any]:
        return {k: getattr(self, k) for k in self.__dataclass_fields__}

    @classmethod
    def from_layout(cls, d: dict[str, Any]) -> "TileSpec":
        return cls(**{k: d[k] for k in cls.__dataclass_fields__})


def channels_of(image: np.ndarray) -> int:
    if image.ndim == 2:
        return 1
    if image.ndim == 3:
        return image.shape[2]
    raise ChunkingError(f"expected 2-D or 3-D image, got {image.ndim}-D")


def choose_tile_shape(height: int, width: int, channels: int, itemsize: int, target_bytes: int,
                      tile_h: int | None = None, tile_w: int | None = None) -> tuple[int, int]:
    if height <= 0 or width <= 0:
        raise ChunkingError("image dimensions must be positive")
    if tile_h is not None or tile_w is not None:
        return (min(tile_h or height, height), min(tile_w or width, width))
    side = max(1, isqrt(max(1, target_bytes // (channels * itemsize))))
    return (min(side, height), min(side, width))


def plan_tiles(height: int, width: int, tile_h: int, tile_w: int, halo: int = 0) -> list[TileSpec]:
    if min(height, width, tile_h, tile_w) <= 0 or halo < 0:
        raise ChunkingError("dimensions and tile sizes must be > 0, halo >= 0")
    specs: list[TileSpec] = []
    idx = 0
    for r, y0 in enumerate(range(0, height, tile_h)):
        y1 = min(y0 + tile_h, height)
        for c, x0 in enumerate(range(0, width, tile_w)):
            x1 = min(x0 + tile_w, width)
            specs.append(TileSpec(idx, r, c, y0, y1, x0, x1, max(0, y0 - halo), min(height, y1 + halo),
                                  max(0, x0 - halo), min(width, x1 + halo)))
            idx += 1
    return specs


def extract_tile(image: np.ndarray, spec: TileSpec) -> np.ndarray:
    """Independent copy of the padded region (a bare slice would be a view)."""
    return image[spec.py0:spec.py1, spec.px0:spec.px1].copy()


def crop_core(tile: np.ndarray, spec: TileSpec) -> np.ndarray:
    cy, cx = spec.y0 - spec.py0, spec.x0 - spec.px0
    h, w = spec.core_shape
    return tile[cy:cy + h, cx:cx + w]


def reassemble(tiles: Sequence[np.ndarray], specs: Sequence[TileSpec],
               shape: tuple[int, ...], dtype) -> np.ndarray:
    if len(tiles) != len(specs):
        raise ReconstructionError(f"{len(tiles)} tiles for {len(specs)} specs")
    out = np.zeros(shape, dtype=dtype)
    covered = 0
    for tile, spec in zip(tiles, specs):
        if tile.shape[:2] != spec.padded_shape or tile.shape[2:] != tuple(shape[2:]):
            raise ReconstructionError(f"tile {spec.index} has shape {tile.shape}, "
                                      f"expected {spec.padded_shape}+{tuple(shape[2:])}")
        out[spec.y0:spec.y1, spec.x0:spec.x1] = crop_core(tile, spec)
        covered += spec.core_shape[0] * spec.core_shape[1]
    if covered != shape[0] * shape[1]:
        raise ReconstructionError("tiles do not cover the image exactly once")
    return out