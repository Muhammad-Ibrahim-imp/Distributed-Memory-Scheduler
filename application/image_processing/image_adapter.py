"""Image Processing Adapter.

Splits an image into tiles, stores each tile as a DSM object, runs a tile-wise
operation, and stitches the processed tiles back together. Uses ONLY the
generic DSM API (alloc/read/write/free) injected through ApplicationAdapter.

Halo: filters that read neighbouring pixels (blur) need extra pixels around
each tile. Input tiles are stored WITH the halo; after processing, the halo is
cropped off so output tiles are exactly the core regions and stitch seamlessly.

Timings (seconds) for the performance model (spec section 33) are kept in
`self.timings`: chunking, processing, aggregation. They cover host-side work
only, so transfer and allocation stay separable.
"""

from __future__ import annotations

import math
import os
from time import perf_counter
from typing import Any, Callable

import cv2
import numpy as np

from common.interfaces.application_adapter import (
    ApplicationAdapter,
    PreparedWorkload,
)
from common.interfaces.dsm_api import DSMAPI
from common.types.errors import IntegrityError, InvalidRequestError


# --- tile operations ---------------------------------------------------------

def _invert(tile: np.ndarray) -> np.ndarray:
    return ~tile


def _grayscale(tile: np.ndarray) -> np.ndarray:
    """Grayscale, but keep the original shape so tiles stay shape-compatible."""
    if tile.ndim == 2 or tile.shape[2] == 1:
        return tile.copy()
    if tile.shape[2] == 3:
        gray = cv2.cvtColor(tile, cv2.COLOR_BGR2GRAY)
        return np.repeat(gray[:, :, None], 3, axis=2)
    raise InvalidRequestError(f"grayscale needs 1 or 3 channels, got {tile.shape[2]}")


def _blur(tile: np.ndarray) -> np.ndarray:
    return cv2.GaussianBlur(tile, (5, 5), 0)


# name -> (function, minimum halo in pixels)
OPERATIONS: dict[str, tuple[Callable[[np.ndarray], np.ndarray], int]] = {
    "invert": (_invert, 0),
    "grayscale": (_grayscale, 0),
    "blur": (_blur, 2),          # 5x5 kernel reads 2 pixels each side
}


def apply_operation(image: np.ndarray, operation: str) -> np.ndarray:
    """The same operation on the whole image. Used as the test reference."""
    func, _ = OPERATIONS[operation]
    return func(np.ascontiguousarray(image))


# --- adapter -----------------------------------------------------------------

class ImageProcessingAdapter(ApplicationAdapter):
    def __init__(
        self,
        dsm_api: DSMAPI,
        operation: str = "invert",
        tile_height: int = 256,
        tile_width: int = 256,
        halo: int = 0,
    ):
        super().__init__(dsm_api)
        if operation not in OPERATIONS:
            raise ValueError(f"unknown operation {operation!r}; choose from {sorted(OPERATIONS)}")
        if tile_height <= 0 or tile_width <= 0:
            raise ValueError("tile_height and tile_width must be > 0")
        if halo < 0:
            raise ValueError("halo must be >= 0")
        min_halo = OPERATIONS[operation][1]
        if halo < min_halo:
            raise ValueError(f"operation {operation!r} needs halo >= {min_halo}, got {halo}")

        self.operation = operation
        self.tile_height = tile_height
        self.tile_width = tile_width
        self.halo = halo
        self.timings: dict[str, float] = {"chunking": 0.0, "processing": 0.0, "aggregation": 0.0}

    # -- helpers --------------------------------------------------------------

    @staticmethod
    def load(path: str | os.PathLike) -> np.ndarray:
        image = cv2.imread(os.fspath(path), cv2.IMREAD_UNCHANGED)
        if image is None:
            raise InvalidRequestError(f"cannot read image: {path}")
        return image

    @staticmethod
    def save(path: str | os.PathLike, image: np.ndarray) -> None:
        if not cv2.imwrite(os.fspath(path), image):
            raise InvalidRequestError(f"cannot write image: {path}")

    def _to_image(self, input_data: Any) -> np.ndarray:
        if isinstance(input_data, (str, os.PathLike)):
            input_data = self.load(input_data)
        if not isinstance(input_data, np.ndarray):
            raise InvalidRequestError("input must be a numpy array or an image path")
        if input_data.dtype != np.uint8:
            raise InvalidRequestError(f"image dtype must be uint8, got {input_data.dtype}")
        if input_data.ndim not in (2, 3) or input_data.size == 0:
            raise InvalidRequestError(f"bad image shape {input_data.shape}")
        return input_data

    def _grid(self, height: int, width: int) -> tuple[int, int]:
        return math.ceil(height / self.tile_height), math.ceil(width / self.tile_width)

    def _tile(self, image: np.ndarray, r: int, c: int) -> tuple[dict[str, Any], bytes]:
        h, w = image.shape[:2]
        y0, x0 = r * self.tile_height, c * self.tile_width
        y1, x1 = min(h, y0 + self.tile_height), min(w, x0 + self.tile_width)
        ey0, ey1 = max(0, y0 - self.halo), min(h, y1 + self.halo)
        ex0, ex1 = max(0, x0 - self.halo), min(w, x1 + self.halo)
        block = np.ascontiguousarray(image[ey0:ey1, ex0:ex1])
        layout = {
            "row": r, "col": c,
            "y0": y0, "y1": y1, "x0": x0, "x1": x1,   # core region in the image
            "ey0": ey0, "ex0": ex0,                    # where the stored block starts
            "shape": tuple(block.shape),
            "dtype": block.dtype.name,
            "image_shape": tuple(image.shape),
        }
        return layout, block.tobytes()

    @staticmethod
    def _decode(data: bytes, meta: dict[str, Any]) -> np.ndarray:
        dtype = np.dtype(meta["dtype"])
        shape = tuple(meta["shape"])
        expected = int(np.prod(shape)) * dtype.itemsize
        if len(data) != expected:
            raise IntegrityError(f"object has {len(data)} bytes, expected {expected}")
        return np.frombuffer(data, dtype=dtype).reshape(shape).copy()

    # -- ApplicationAdapter ---------------------------------------------------

    async def prepare(self, input_data: Any) -> PreparedWorkload:
        image = self._to_image(input_data)
        rows, cols = self._grid(*image.shape[:2])
        workload = PreparedWorkload(object_ids=[])
        chunking = 0.0
        try:
            for r in range(rows):
                for c in range(cols):
                    t0 = perf_counter()
                    layout, payload = self._tile(image, r, c)
                    chunking += perf_counter() - t0
                    oid = await self.dsm.alloc(len(payload))
                    workload.object_ids.append(oid)       # recorded BEFORE write so cleanup sees it
                    workload.layout[oid] = layout
                    await self.dsm.write(oid, payload)
        except Exception:
            await self.cleanup(workload)
            raise
        self.timings["chunking"] = chunking
        return workload

    async def execute(self, workload: PreparedWorkload) -> PreparedWorkload:
        func, _ = OPERATIONS[self.operation]
        processed = PreparedWorkload(object_ids=[])
        processing = 0.0
        try:
            for oid in workload.object_ids:
                meta = workload.layout[oid]
                tile = self._decode(await self.dsm.read(oid), meta)

                t0 = perf_counter()
                out = func(tile)
                oy, ox = meta["y0"] - meta["ey0"], meta["x0"] - meta["ex0"]
                core = np.ascontiguousarray(
                    out[oy: oy + (meta["y1"] - meta["y0"]), ox: ox + (meta["x1"] - meta["x0"])]
                )
                payload = core.tobytes()
                processing += perf_counter() - t0

                out_id = await self.dsm.alloc(len(payload))
                processed.object_ids.append(out_id)
                processed.layout[out_id] = {
                    "row": meta["row"], "col": meta["col"],
                    "y0": meta["y0"], "y1": meta["y1"], "x0": meta["x0"], "x1": meta["x1"],
                    "shape": tuple(core.shape),
                    "dtype": core.dtype.name,
                    "image_shape": meta["image_shape"],
                }
                await self.dsm.write(out_id, payload)
        except Exception:
            await self.cleanup(processed)       # inputs stay; the caller owns them
            raise
        self.timings["processing"] = processing
        return processed

    async def collect_result(self, workload: PreparedWorkload) -> np.ndarray:
        if not workload.object_ids:
            raise InvalidRequestError("empty workload")
        first = workload.layout[workload.object_ids[0]]
        image_shape = tuple(first["image_shape"])
        result = np.empty(image_shape, dtype=np.dtype(first["dtype"]))
        covered = 0
        aggregation = 0.0
        for oid in workload.object_ids:
            meta = workload.layout[oid]
            tile = self._decode(await self.dsm.read(oid), meta)
            t0 = perf_counter()
            result[meta["y0"]: meta["y1"], meta["x0"]: meta["x1"]] = tile
            aggregation += perf_counter() - t0
            covered += (meta["y1"] - meta["y0"]) * (meta["x1"] - meta["x0"])
        if covered != image_shape[0] * image_shape[1]:
            raise IntegrityError("tiles do not cover the whole image")
        self.timings["aggregation"] = aggregation
        return result

    def estimate_memory(self, input_data: Any) -> int | None:
        """Bytes in the DSM at peak: input tiles (with halo) + output tiles."""
        if not isinstance(input_data, np.ndarray) or input_data.ndim not in (2, 3):
            return None
        h, w = input_data.shape[:2]
        channels = input_data.shape[2] if input_data.ndim == 3 else 1
        itemsize = input_data.dtype.itemsize
        rows, cols = self._grid(h, w)
        total = 0
        for r in range(rows):
            for c in range(cols):
                y0, x0 = r * self.tile_height, c * self.tile_width
                y1, x1 = min(h, y0 + self.tile_height), min(w, x0 + self.tile_width)
                ext = (min(h, y1 + self.halo) - max(0, y0 - self.halo)) * \
                      (min(w, x1 + self.halo) - max(0, x0 - self.halo))
                core = (y1 - y0) * (x1 - x0)
                total += (ext + core) * channels * itemsize
        return total