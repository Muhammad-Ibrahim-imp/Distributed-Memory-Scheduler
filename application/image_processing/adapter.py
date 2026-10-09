"""Image Processing Adapter. Week 2 = step 1: load -> chunk -> alloc/write on the DSM.
execute() / collect_result() (transform + reconstruct) land in Week 3."""
from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Any

import numpy as np

from application.framework.base import CHUNKING, BaseAdapter, PhaseTimings, TimedWorkload
from application.framework.errors import AdapterConfigError, InvalidInputError
from application.framework.serialization import pack_array
from application.image_processing.chunker import (
    TileSpec, channels_of, choose_tile_shape, extract_tile, plan_tiles,
)
from common.interfaces.application_adapter import PreparedWorkload


@dataclass
class ImageWorkload(TimedWorkload):
    """layout[object_id] = TileSpec.to_layout() + {"shape": padded tile shape, "dtype": ...}."""
    image_shape: tuple[int, ...] = ()
    dtype: str = ""

    def tile_specs(self) -> list[TileSpec]:
        return [TileSpec.from_layout(self.layout[oid]) for oid in self.object_ids]


class ImageProcessingAdapter(BaseAdapter):
    name = "image_processing"

    def _load(self, input_data: Any) -> np.ndarray:
        cfg = self.config.image
        if isinstance(input_data, np.ndarray):
            image = input_data
        elif isinstance(input_data, (str, Path)):
            try:
                import cv2
            except ImportError as exc:                       # pragma: no cover
                raise AdapterConfigError("opencv-python is required to load image files") from exc
            flag = {"color": cv2.IMREAD_COLOR, "grayscale": cv2.IMREAD_GRAYSCALE,
                    "unchanged": cv2.IMREAD_UNCHANGED}[cfg.read_mode]
            image = cv2.imread(str(input_data), flag)
            if image is None:
                raise InvalidInputError(f"cannot read image: {input_data}")
        else:
            raise InvalidInputError(f"unsupported input type {type(input_data).__name__}")
        if image.ndim not in (2, 3) or image.size == 0:
            raise InvalidInputError(f"bad image shape {image.shape}")
        if image.dtype.kind not in "uif":
            raise InvalidInputError(f"unsupported image dtype {image.dtype}")
        if image.nbytes > cfg.max_image_bytes:
            raise InvalidInputError(f"image is {image.nbytes} bytes, limit is {cfg.max_image_bytes}")
        return image

    def estimate_memory(self, input_data: Any) -> int | None:
        return int(self._load(input_data).nbytes)

    async def prepare(self, input_data: Any) -> ImageWorkload:
        cfg, timings = self.config.image, PhaseTimings()
        with timings.measure(CHUNKING):
            image = self._load(input_data)
            h, w = image.shape[:2]
            th, tw = choose_tile_shape(h, w, channels_of(image), image.dtype.itemsize,
                                       cfg.target_chunk_bytes, cfg.tile_height, cfg.tile_width)
            specs = plan_tiles(h, w, th, tw, cfg.halo)
            tiles = [extract_tile(image, s) for s in specs]
            payloads = [pack_array(t) for t in tiles]
        ids = await self._store_objects(payloads, timings)
        layout = {oid: {**s.to_layout(), "shape": tuple(t.shape), "dtype": t.dtype.str}
                  for oid, s, t in zip(ids, specs, tiles)}
        return ImageWorkload(object_ids=ids, layout=layout, timings=timings,
                             image_shape=tuple(image.shape), dtype=image.dtype.str)

    async def execute(self, workload: PreparedWorkload) -> PreparedWorkload:
        raise NotImplementedError("image transform lands in Week 3")

    async def collect_result(self, workload: PreparedWorkload) -> Any:
        raise NotImplementedError("reconstruction lands in Week 3")