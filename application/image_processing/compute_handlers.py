"""Registered image tile handlers for compute-capable DSM workers."""

from __future__ import annotations

from typing import Any, Mapping

import numpy as np

from application.image_processing.image_adapter import OPERATIONS
from compute.worker import HandlerOutput, HandlerRegistry


def _make_handler(operation):
    def handle(inputs: tuple[bytes, ...], parameters: Mapping[str, Any]) -> HandlerOutput:
        if len(inputs) != 1:
            raise ValueError("image operation requires exactly one input tile")
        dtype = np.dtype(parameters["dtype"])
        shape = tuple(parameters["shape"])
        expected = int(np.prod(shape)) * dtype.itemsize
        if len(inputs[0]) != expected:
            raise ValueError(f"tile payload has {len(inputs[0])} bytes, expected {expected}")
        tile = np.frombuffer(inputs[0], dtype=dtype).reshape(shape)
        output = operation(tile)
        y, x = int(parameters["crop_y"]), int(parameters["crop_x"])
        h, w = int(parameters["height"]), int(parameters["width"])
        if min(y, x) < 0 or h <= 0 or w <= 0 or y + h > output.shape[0] or x + w > output.shape[1]:
            raise ValueError("invalid image tile core bounds")
        core = np.ascontiguousarray(output[y:y + h, x:x + w])
        return HandlerOutput(core.tobytes(), {"shape": list(core.shape), "dtype": core.dtype.name})

    return handle


def register_image_handlers(registry: HandlerRegistry) -> None:
    """Register the fixed, supported image operations; no client code is loaded."""
    for name, (handler, _halo) in OPERATIONS.items():
        registry.register(f"image.{name}", _make_handler(handler))
