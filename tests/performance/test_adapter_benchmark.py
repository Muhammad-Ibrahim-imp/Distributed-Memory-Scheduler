import numpy as np

from scripts.benchmark_adapters import benchmark_image, benchmark_matrix


async def test_small_benchmark_cases_report_all_adapter_phases():
    image = np.arange(16 * 16 * 3, dtype=np.uint8).reshape(16, 16, 3)
    matrices = (np.eye(8), np.eye(8))

    image_row = await benchmark_image(8, image, repeats=1, warmups=0)
    matrix_row = await benchmark_matrix(4, matrices, repeats=1, warmups=0)

    for row in (image_row, matrix_row):
        assert row["repeats"] == 1
        assert row["object_count"] > 0
        assert row["chunking_seconds"] >= 0
        assert row["processing_seconds"] >= 0
        assert row["aggregation_seconds"] >= 0
        assert row["end_to_end_seconds"] >= 0
