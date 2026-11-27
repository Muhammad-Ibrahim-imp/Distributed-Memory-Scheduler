"""Matrix Multiplication Adapter (blocked matmul, spec section 8).

A (m x k) and B (k x n) are cut into block_size x block_size blocks. Every block
is a DSM object. For each output block:

    C[i][j] = sum over l of  A[i][l] @ B[l][j]

which for a 2x2 grid is exactly C11 = A11B11 + A12B21 and so on. Edge blocks are
smaller when a dimension is not divisible by block_size.

Timings (seconds) are kept in `self.timings`: chunking, processing, aggregation.
"""

from __future__ import annotations

import asyncio
import math
import os
from time import perf_counter
from typing import Any
from uuid import uuid4

import numpy as np

from common.interfaces.application_adapter import (
    ApplicationAdapter,
    PreparedWorkload,
)
from common.interfaces.dsm_api import DSMAPI
from common.types.errors import IntegrityError, InvalidRequestError
from compute import ComputeAPIClient, ComputeTask, TaskStatus


class MatrixMultiplicationAdapter(ApplicationAdapter):
    def __init__(self, dsm_api: DSMAPI, block_size: int = 512, compute_api: ComputeAPIClient | None = None,
                 io_concurrency: int = 1):
        super().__init__(dsm_api)
        if not isinstance(block_size, int) or block_size <= 0:
            raise ValueError("block_size must be a positive int")
        self.block_size = block_size
        self.compute_api = compute_api
        if not isinstance(io_concurrency, int) or io_concurrency <= 0:
            raise ValueError("io_concurrency must be a positive int")
        self.io_concurrency = io_concurrency
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
        self, matrix: np.ndarray, name: str, workload: PreparedWorkload,
        dtype: np.dtype | None = None,
    ) -> float:
        bs = self.block_size
        rows, cols = matrix.shape
        chunking = 0.0
        tiles = [(i, j) for i in range(math.ceil(rows / bs)) for j in range(math.ceil(cols / bs))]
        # Allocate serially to preserve placement order, then overlap a bounded
        # window of writes and seals.
        for start in range(0, len(tiles), self.io_concurrency):
            batch = []
            for i, j in tiles[start:start + self.io_concurrency]:
                t0 = perf_counter()
                r0, c0 = i * bs, j * bs
                block = np.ascontiguousarray(matrix[r0:r0 + bs, c0:c0 + bs], dtype=dtype)
                payload = block.tobytes()
                chunking += perf_counter() - t0
                oid = await self.dsm.alloc(len(payload))
                meta = {"matrix": name, "row": i, "col": j, "r0": r0, "c0": c0,
                        "shape": tuple(block.shape), "dtype": block.dtype.name,
                        "matrix_shape": (rows, cols)}
                workload.object_ids.append(oid)
                workload.layout[oid] = meta
                batch.append((oid, payload))
            async def write_tile(oid, payload):
                await self.dsm.write(oid, payload)
                if self.compute_api is not None:
                    await self.dsm.seal(oid)
            await asyncio.gather(*(write_tile(oid, payload) for oid, payload in batch))
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

    async def prepare_from_memmaps(
        self, a_path: str | os.PathLike, b_path: str | os.PathLike
    ) -> PreparedWorkload:
        """Upload bounded tiles from .npy matrices without loading either matrix whole."""
        try:
            a = np.load(a_path, mmap_mode="r", allow_pickle=False)
            b = np.load(b_path, mmap_mode="r", allow_pickle=False)
        except (OSError, ValueError) as exc:
            raise InvalidRequestError(f"cannot open matrix memmap input: {exc}") from exc
        if (a.ndim != 2 or b.ndim != 2 or 0 in a.shape or 0 in b.shape
                or a.shape[1] != b.shape[0]
                or not np.issubdtype(a.dtype, np.number)
                or not np.issubdtype(b.dtype, np.number)):
            raise InvalidRequestError(f"invalid memmap matrix shapes/dtypes: {a.shape}/{a.dtype}, {b.shape}/{b.dtype}")
        dtype = np.result_type(a.dtype, b.dtype)
        if not np.issubdtype(dtype, np.floating):
            dtype = np.dtype(np.float64)
        workload = PreparedWorkload(object_ids=[])
        started = perf_counter()
        try:
            await self._store_blocks(a, "A", workload, dtype)
            await self._store_blocks(b, "B", workload, dtype)
        except Exception:
            await self.cleanup(workload)
            raise
        finally:
            del a, b
        self.timings["chunking"] = perf_counter() - started
        return workload

    async def execute(self, workload: PreparedWorkload) -> PreparedWorkload:
        if self.compute_api is not None:
            return await self._execute_with_compute(workload)
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
                r0 = workload.layout[a_ids[(i, 0)]]["r0"]
                for j in range(b_cols):
                    acc = None
                    for l in range(inner):
                        # Keep only one pair of input blocks and the output
                        # accumulator live, independent of the inner dimension.
                        a_blk = self._decode(
                            await self.dsm.read(a_ids[(i, l)]), workload.layout[a_ids[(i, l)]]
                        )
                        b_blk = self._decode(
                            await self.dsm.read(b_ids[(l, j)]), workload.layout[b_ids[(l, j)]]
                        )
                        t0 = perf_counter()
                        prod = a_blk @ b_blk
                        acc = prod if acc is None else acc + prod
                        processing += perf_counter() - t0
                        del a_blk, b_blk, prod

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

    async def _execute_with_compute(self, workload: PreparedWorkload) -> PreparedWorkload:
        a_ids, b_ids = self._index(workload, "A"), self._index(workload, "B")
        if not a_ids or not b_ids:
            raise InvalidRequestError("workload has no A or B blocks")
        a_rows, inner = max(i for i, _ in a_ids) + 1, max(l for _, l in a_ids) + 1
        b_cols = max(j for _, j in b_ids) + 1
        if max(l for l, _ in b_ids) + 1 != inner:
            raise InvalidRequestError("A and B block grids do not line up")

        processed = PreparedWorkload(object_ids=[])
        temporary_ids: set[str] = set()
        started = perf_counter()
        try:
            # Each multiply reads only one A/B pair; pairwise reductions keep
            # the live working set bounded as the inner dimension grows.
            multiply_tasks = []
            task_tile: dict[str, tuple[int, int]] = {}
            for i in range(a_rows):
                for j in range(b_cols):
                    for l in range(inner):
                        aid, bid = a_ids[(i, l)], b_ids[(l, j)]
                        am, bm = workload.layout[aid], workload.layout[bid]
                        task_id = str(uuid4())
                        task_tile[task_id] = (i, j)
                        multiply_tasks.append(ComputeTask(
                            task_id, f"matrix-{task_id}", "matrix.multiply_block", (aid, bid),
                            {"a_shape": list(am["shape"]), "b_shape": list(bm["shape"]),
                             "dtype": am["dtype"]},
                            estimated_memory_bytes=3 * (
                                int(np.prod(am["shape"])) + int(np.prod(bm["shape"]))
                                + am["shape"][0] * bm["shape"][1]
                            ) * np.dtype(am["dtype"]).itemsize,
                        ))
            await self.compute_api.submit_all(multiply_tasks)
            results = await self._wait_tasks(multiply_tasks)
            partials: dict[tuple[int, int], list[str]] = {(i, j): [] for i in range(a_rows) for j in range(b_cols)}
            tile_shapes = {}
            temporary_ids.update(oid for result in results for oid in result.output_object_ids)
            for result in results:
                if result.status is not TaskStatus.COMPLETED or len(result.output_object_ids) != 1:
                    raise RuntimeError(f"matrix multiply task {result.task_id} failed: {result.error or result.status}")
                oid = result.output_object_ids[0]
                tile = task_tile[result.task_id]
                partials[tile].append(oid)
                tile_shapes[tile] = tuple(result.output_metadata["shape"])

            while any(len(ids) > 1 for ids in partials.values()):
                additions = []
                destinations = {}
                for tile, ids in partials.items():
                    next_level = []
                    for pair_index in range(0, len(ids) - 1, 2):
                        left, right = ids[pair_index:pair_index + 2]
                        shape = tile_shapes[tile]
                        dtype = workload.layout[a_ids[(tile[0], 0)]]["dtype"]
                        task_id = str(uuid4())
                        additions.append(ComputeTask(
                            task_id, f"matrix-{task_id}", "matrix.add_block", (left, right),
                            {"shape": list(shape), "dtype": dtype},
                            estimated_memory_bytes=3 * int(np.prod(shape)) * np.dtype(dtype).itemsize,
                        ))
                        destinations[task_id] = tile
                        next_level.append(task_id)
                    if len(ids) % 2:
                        next_level.append(ids[-1])
                    partials[tile] = next_level
                await self.compute_api.submit_all(additions)
                sums = await self._wait_tasks(additions)
                temporary_ids.update(oid for result in sums for oid in result.output_object_ids)
                for result in sums:
                    if result.status is not TaskStatus.COMPLETED or len(result.output_object_ids) != 1:
                        raise RuntimeError(f"matrix reduction task {result.task_id} failed: {result.error or result.status}")
                    oid = result.output_object_ids[0]
                    tile = destinations[result.task_id]
                    partials[tile][partials[tile].index(result.task_id)] = oid

            for (i, j), ids in partials.items():
                oid = ids[0]
                processed.object_ids.append(oid)
                temporary_ids.discard(oid)
                meta = workload.layout[a_ids[(i, 0)]]
                bm = workload.layout[b_ids[(0, j)]]
                m, n = meta["matrix_shape"][0], bm["matrix_shape"][1]
                processed.layout[oid] = {
                    "matrix": "C", "row": i, "col": j,
                    "r0": meta["r0"], "c0": bm["c0"],
                    "shape": tile_shapes[(i, j)],
                    "dtype": meta["dtype"], "matrix_shape": (m, n),
                }
        except Exception:
            await self.cleanup(processed)
            raise
        finally:
            for oid in temporary_ids:
                try:
                    await self.dsm.free(oid)
                except Exception:
                    pass
        self.timings["processing"] = perf_counter() - started
        return processed

    async def _wait_tasks(self, tasks):
        wait_all = getattr(self.compute_api, "wait_all", None)
        if callable(wait_all):
            return await wait_all(tuple(task.task_id for task in tasks))
        return await asyncio.gather(*(self.compute_api.wait(task.task_id) for task in tasks))

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

    async def collect_result_to_memmap(
        self, workload: PreparedWorkload, output_path: str | os.PathLike
    ) -> np.memmap:
        """Write result tiles incrementally to a .npy memmap instead of an in-RAM C matrix."""
        if not workload.object_ids:
            raise InvalidRequestError("empty workload")
        first = workload.layout[workload.object_ids[0]]
        m, n = first["matrix_shape"]
        result = np.lib.format.open_memmap(
            output_path, mode="w+", dtype=np.dtype(first["dtype"]), shape=(m, n)
        )
        covered = 0
        aggregation = 0.0
        try:
            for oid in workload.object_ids:
                meta = workload.layout[oid]
                block = self._decode(await self.dsm.read(oid), meta)
                t0 = perf_counter()
                h, w = block.shape
                result[meta["r0"]:meta["r0"] + h, meta["c0"]:meta["c0"] + w] = block
                aggregation += perf_counter() - t0
                covered += h * w
            if covered != m * n:
                raise IntegrityError("blocks do not cover the whole result matrix")
            result.flush()
        except Exception:
            result.flush()
            del result
            try:
                os.unlink(output_path)
            except OSError:
                pass
            raise
        self.timings["aggregation"] = aggregation
        return result

    async def run_memmaps(
        self, a_path: str | os.PathLike, b_path: str | os.PathLike,
        output_path: str | os.PathLike,
    ) -> np.memmap:
        """Multiply .npy memmap inputs and stream the reconstructed result to disk."""
        inputs = await self.prepare_from_memmaps(a_path, b_path)
        outputs = None
        try:
            outputs = await self.execute(inputs)
            return await self.collect_result_to_memmap(outputs, output_path)
        finally:
            await self.cleanup(inputs, outputs)

    @staticmethod
    def generate_memmap_matrix(
        path: str | os.PathLike, shape: tuple[int, int], seed: int,
        dtype: np.dtype | str = np.float32, rows_per_chunk: int = 128,
    ) -> np.memmap:
        """Generate deterministic normal values using a bounded row buffer."""
        if (len(shape) != 2 or min(shape) <= 0 or rows_per_chunk <= 0
                or not np.issubdtype(np.dtype(dtype), np.floating)):
            raise ValueError("shape, floating dtype, and rows_per_chunk must be valid")
        matrix = np.lib.format.open_memmap(
            path, mode="w+", dtype=np.dtype(dtype), shape=shape
        )
        rng = np.random.default_rng(seed)
        for row in range(0, shape[0], rows_per_chunk):
            end = min(shape[0], row + rows_per_chunk)
            matrix[row:end] = rng.standard_normal((end - row, shape[1]), dtype=matrix.dtype)
        matrix.flush()
        return matrix

    def estimate_memory(self, input_data: Any) -> int | None:
        """Bytes in the DSM at peak: A + B + C."""
        try:
            a, b = self._validate(input_data)
        except InvalidRequestError:
            return None
        m, k = a.shape
        n = b.shape[1]
        return (m * k + k * n + m * n) * a.dtype.itemsize
