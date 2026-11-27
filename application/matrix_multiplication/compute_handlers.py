"""Registered, bounded matrix block operations for compute workers."""

from __future__ import annotations

from typing import Any, Mapping

import numpy as np

from compute.worker import HandlerOutput, HandlerRegistry


def _array(data: bytes, shape: tuple[int, ...], dtype: np.dtype) -> np.ndarray:
    expected = int(np.prod(shape)) * dtype.itemsize
    if len(data) != expected:
        raise ValueError(f"matrix block has {len(data)} bytes, expected {expected}")
    return np.frombuffer(data, dtype=dtype).reshape(shape)


def multiply_block(inputs: tuple[bytes, ...], parameters: Mapping[str, Any]) -> HandlerOutput:
    if len(inputs) != 2:
        raise ValueError("matrix multiplication requires two input blocks")
    dtype = np.dtype(parameters["dtype"])
    left = _array(inputs[0], tuple(parameters["a_shape"]), dtype)
    right = _array(inputs[1], tuple(parameters["b_shape"]), dtype)
    if left.shape[1] != right.shape[0]:
        raise ValueError("matrix block dimensions do not align")
    result = np.ascontiguousarray(left @ right)
    return HandlerOutput(result.tobytes(), {"shape": list(result.shape), "dtype": result.dtype.name})


def add_block(inputs: tuple[bytes, ...], parameters: Mapping[str, Any]) -> HandlerOutput:
    if len(inputs) != 2:
        raise ValueError("matrix addition requires two partial blocks")
    dtype = np.dtype(parameters["dtype"])
    shape = tuple(parameters["shape"])
    left, right = (_array(data, shape, dtype) for data in inputs)
    result = np.ascontiguousarray(left + right)
    return HandlerOutput(result.tobytes(), {"shape": list(result.shape), "dtype": result.dtype.name})


def register_matrix_handlers(registry: HandlerRegistry) -> None:
    registry.register("matrix.multiply_block", multiply_block)
    registry.register("matrix.add_block", add_block)
