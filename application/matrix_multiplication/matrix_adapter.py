"""Matrix Multiplication Adapter (blocked matmul, spec section 8).

A (m x k) and B (k x n) are cut into block_size x block_size blocks. Every block
is a DSM object. For each output block:

    C[i][j] = sum over l of  A[i][l] @ B[l][j]

which for a 2x2 grid is exactly C11 = A11B11 + A12B21 and so on. Edge blocks are
smaller when a dimension is not divisible by block_size.

Timings (seconds) are kept in `self.timings`: chunking, processing, aggregation.
"""

from __future__ import annotations

import math
from time import perf_counter
from typing import Any

import numpy as np

from common.interfaces.application_adapter import (
    ApplicationAdapter,
    PreparedWorkload,
)
from common.interfaces.dsm_api import DSMAPI
from common.types.errors import IntegrityError, InvalidRequestError


class MatrixMultiplicationAdapter(ApplicationAdapter):
    def __init__(self, dsm_api: DSMAPI, block_size: int = 512):
        super().__init__(dsm_api)
        if not isinstance(block_size, int) or block_size <= 0:
            raise ValueError("block_size must be a positive int")
        self.block_size = block_size
        self.timings: dict[str, float] = {"chunking": 0.0, "processing": 0.0, "aggregation": 0.0}

    # -- helpers --------------------------------------------------------------

    @staticmethod
    def _validate(input_data: Any) -> tuple[np.ndarray, np.ndarray]:
        try:
            a, b = input_data
        except (TypeError, ValueError):
            raise InvalidRequestError("input must be a pair (A, B)") from None
        a, b = np.asarray(a), np.asarray(b)
        if a.ndim != 2 or b.ndim != 2 or 0 in a.shape or 0 in b.shape:
            raise InvalidRequestError(f"A and B must be non-empty 2-D, got {a.shape} and {b.shape}")
        if a.shape[1] != b.shape[0]:
            raise InvalidRequestError(f"shape mismatch: {a.shape} @ {b.shape}")
        dtype = np.result_type(a.dtype, b.dtype)
        if not np.issubdtype(dtype, np.floating):
            dtype = np.dtype(np.float64)
        return a.astype(dtype, copy=False), b.astype(dtype, copy=False)

    @staticmethod
    def _decode(data: bytes, meta: dict[str, Any]) -> np.ndarray:
        dtype = np.dtype(meta["dtype"])
        shape = tuple(meta["shape"])
        expected = int(np.prod(shape)) * dtype.itemsize
        if len(data) != expected:
            raise IntegrityError(f"object has {len(data)} bytes, expected {expected}")
        return np.frombuffer(data, dtype=dtype).reshape(shape).copy()

    async def _store_blocks(
        self, matrix: np.ndarray, name: str, workload: PreparedWorkload
    ) -> float:
        bs = self.block_size
        rows, cols = matrix.shape
        chunking = 0.0
        for i in range(math.ceil(rows / bs)):
            for j in range(math.ceil(cols / bs)):
                t0 = perf_counter()
                r0, c0 = i * bs, j * bs
                block = np.ascontiguousarray(matrix[r0: r0 + bs, c0: c0 + bs])
                payload = block.tobytes()
                chunking += perf_counter() - t0

                oid = await self.dsm.alloc(len(payload))
                workload.object_ids.append(oid)
                workload.layout[oid] = {
                    "matrix": name, "row": i, "col": j, "r0": r0, "c0": c0,
                    "shape": tuple(block.shape), "dtype": block.dtype.name,
                    "matrix_shape": (rows, cols),
                }
                await self.dsm.write(oid, payload)
        return chunking

    @staticmethod
    def _index(workload: PreparedWorkload, name: str) -> dict[tuple[int, int], str]:
        return {
            (m["row"], m["col"]): oid
            for oid, m in workload.layout.items()
            if m["matrix"] == name
        }

    # -- ApplicationAdapter ---------------------------------------------------

    async def prepare(self, input_data: Any) -> PreparedWorkload:
        a, b = self._validate(input_data)
        workload = PreparedWorkload(object_ids=[])
        try:
            chunking = await self._store_blocks(a, "A", workload)
            chunking += await self._store_blocks(b, "B", workload)
        except Exception:
            await self.cleanup(workload)
            raise
        self.timings["chunking"] = chunking
        return workload

    async def execute(self, workload: PreparedWorkload) -> PreparedWorkload:
        a_ids = self._index(workload, "A")
        b_ids = self._index(workload, "B")
        if not a_ids or not b_ids:
            raise InvalidRequestError("workload has no A or B blocks")
        a_rows = max(i for i, _ in a_ids) + 1
        inner = max(l for _, l in a_ids) + 1
        b_cols = max(j for _, j in b_ids) + 1
        if max(l for l, _ in b_ids) + 1 != inner:
            raise InvalidRequestError("A and B block grids do not line up")
        m, _ = workload.layout[a_ids[(0, 0)]]["matrix_shape"]
        _, n = workload.layout[b_ids[(0, 0)]]["matrix_shape"]

        processed = PreparedWorkload(object_ids=[])
        processing = 0.0
        try:
            for i in range(a_rows):
                # Read each A block of this block-row once; reuse it for every j.
                a_blocks = [
                    self._decode(await self.dsm.read(a_ids[(i, l)]), workload.layout[a_ids[(i, l)]])
                    for l in range(inner)
                ]
                r0 = workload.layout[a_ids[(i, 0)]]["r0"]
                for j in range(b_cols):
                    acc = None
                    for l in range(inner):
                        b_blk = self._decode(
                            await self.dsm.read(b_ids[(l, j)]), workload.layout[b_ids[(l, j)]]
                        )
                        t0 = perf_counter()
                        prod = a_blocks[l] @ b_blk
                        acc = prod if acc is None else acc + prod
                        processing += perf_counter() - t0

                    payload = np.ascontiguousarray(acc).tobytes()
                    out_id = await self.dsm.alloc(len(payload))
                    processed.object_ids.append(out_id)
                    processed.layout[out_id] = {
                        "matrix": "C", "row": i, "col": j,
                        "r0": r0, "c0": workload.layout[b_ids[(0, j)]]["c0"],
                        "shape": tuple(acc.shape), "dtype": acc.dtype.name,
                        "matrix_shape": (m, n),
                    }
                    await self.dsm.write(out_id, payload)
        except Exception:
            await self.cleanup(processed)
            raise
        self.timings["processing"] = processing
        return processed

    async def collect_result(self, workload: PreparedWorkload) -> np.ndarray:
        if not workload.object_ids:
            raise InvalidRequestError("empty workload")
        first = workload.layout[workload.object_ids[0]]
        m, n = first["matrix_shape"]
        result = np.zeros((m, n), dtype=np.dtype(first["dtype"]))
        covered = 0
        aggregation = 0.0
        for oid in workload.object_ids:
            meta = workload.layout[oid]
            block = self._decode(await self.dsm.read(oid), meta)
            t0 = perf_counter()
            h, w = block.shape
            result[meta["r0"]: meta["r0"] + h, meta["c0"]: meta["c0"] + w] = block
            aggregation += perf_counter() - t0
            covered += h * w
        if covered != m * n:
            raise IntegrityError("blocks do not cover the whole result matrix")
        self.timings["aggregation"] = aggregation
        return result

    def estimate_memory(self, input_data: Any) -> int | None:
        """Bytes in the DSM at peak: A + B + C."""
        try:
            a, b = self._validate(input_data)
        except InvalidRequestError:
            return None
        m, k = a.shape
        n = b.shape[1]
        return (m * k + k * n + m * n) * a.dtype.itemsize