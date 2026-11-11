"""Run repeatable adapter phase benchmarks against the in-memory DSM fake."""

from __future__ import annotations

import argparse
import asyncio
import csv
from pathlib import Path
from statistics import median
from time import perf_counter

import numpy as np

from application.image_processing.image_adapter import (
    ImageProcessingAdapter,
    apply_operation,
)
from application.matrix_multiplication.matrix_adapter import MatrixMultiplicationAdapter
from tests.mocks.mock_dsm_api import MockDSMAPI


async def benchmark_image(size: int, image: np.ndarray, repeats: int, warmups: int) -> dict:
    expected = apply_operation(image, "invert")
    samples: list[dict[str, float]] = []
    object_counts: list[int] = []
    for iteration in range(warmups + repeats):
        dsm = MockDSMAPI()
        adapter = ImageProcessingAdapter(dsm, operation="invert", tile_height=size, tile_width=size)
        started = perf_counter()
        result = await adapter.run(image)
        elapsed = perf_counter() - started
        np.testing.assert_array_equal(result, expected)
        if dsm.live_ids:
            raise AssertionError("image adapter leaked DSM objects")
        if iteration >= warmups:
            samples.append({**adapter.timings, "end_to_end": elapsed})
            object_counts.append(sum(call[0] == "alloc" for call in dsm.calls))
    return _summarize("image_invert", size, image.shape, samples, object_counts)


async def benchmark_matrix(size: int, matrices: tuple[np.ndarray, np.ndarray], repeats: int,
                           warmups: int) -> dict:
    a, b = matrices
    expected = a @ b
    samples: list[dict[str, float]] = []
    object_counts: list[int] = []
    for iteration in range(warmups + repeats):
        dsm = MockDSMAPI()
        adapter = MatrixMultiplicationAdapter(dsm, block_size=size)
        started = perf_counter()
        result = await adapter.run(matrices)
        elapsed = perf_counter() - started
        np.testing.assert_allclose(result, expected, rtol=1e-10, atol=1e-10)
        if dsm.live_ids:
            raise AssertionError("matrix adapter leaked DSM objects")
        if iteration >= warmups:
            samples.append({**adapter.timings, "end_to_end": elapsed})
            object_counts.append(sum(call[0] == "alloc" for call in dsm.calls))
    return _summarize("matrix_multiply", size, f"{a.shape} @ {b.shape}", samples, object_counts)


def _summarize(application: str, chunk_size: int, shape: object,
               samples: list[dict[str, float]], object_counts: list[int]) -> dict:
    return {
        "application": application,
        "chunk_size": chunk_size,
        "input_shape": shape,
        "repeats": len(samples),
        "object_count": round(median(object_counts)),
        "chunking_seconds": median(sample["chunking"] for sample in samples),
        "processing_seconds": median(sample["processing"] for sample in samples),
        "aggregation_seconds": median(sample["aggregation"] for sample in samples),
        "end_to_end_seconds": median(sample["end_to_end"] for sample in samples),
    }


async def run(args: argparse.Namespace) -> list[dict]:
    rng = np.random.default_rng(args.seed)
    image = rng.integers(0, 256, (args.image_size, args.image_size, 3), dtype=np.uint8)
    matrix_a = rng.standard_normal((args.matrix_size, args.matrix_size))
    matrix_b = rng.standard_normal((args.matrix_size, args.matrix_size))
    rows = []
    for size in args.sizes:
        rows.append(await benchmark_image(size, image, args.repeats, args.warmups))
        rows.append(await benchmark_matrix(size, (matrix_a, matrix_b), args.repeats, args.warmups))
    return rows


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--sizes", type=int, nargs="+", default=[64, 128, 256])
    parser.add_argument("--repeats", type=int, default=5)
    parser.add_argument("--warmups", type=int, default=1)
    parser.add_argument("--image-size", type=int, default=1024)
    parser.add_argument("--matrix-size", type=int, default=256)
    parser.add_argument("--seed", type=int, default=2026)
    parser.add_argument("--output", type=Path, default=Path("benchmarks/week4_adapter_benchmarks.csv"))
    args = parser.parse_args()
    if min(args.sizes) <= 0 or args.repeats <= 0 or args.warmups < 0:
        parser.error("sizes and repeats must be positive; warmups cannot be negative")
    if args.image_size <= 0 or args.matrix_size <= 0:
        parser.error("input dimensions must be positive")

    rows = asyncio.run(run(args))
    args.output.parent.mkdir(parents=True, exist_ok=True)
    with args.output.open("w", newline="", encoding="utf-8") as stream:
        writer = csv.DictWriter(stream, fieldnames=rows[0].keys())
        writer.writeheader()
        writer.writerows(rows)
    print(f"Wrote {len(rows)} median benchmark rows to {args.output}")


if __name__ == "__main__":
    main()
