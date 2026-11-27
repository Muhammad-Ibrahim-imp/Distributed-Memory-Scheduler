import cv2
import numpy as np
import pytest

from application.image_processing.image_adapter import (
    ImageProcessingAdapter,
    apply_operation,
)
from common.types.errors import DSMTimeoutError, InvalidRequestError
from application.image_processing.compute_handlers import register_image_handlers
from compute import ComputeAPI, ComputeWorker, HandlerRegistry
from tests.mocks.mock_dsm_api import MockDSMAPI


def make_image(h, w, channels=3):
    rng = np.random.default_rng(0)
    shape = (h, w, channels) if channels else (h, w)
    return rng.integers(0, 256, shape, dtype=np.uint8)


async def test_npy_image_is_tiled_from_memmap_and_output_is_file_backed(tmp_path):
    source_path, output_path = tmp_path / "source.npy", tmp_path / "output.npy"
    source = np.lib.format.open_memmap(
        source_path, mode="w+", dtype=np.uint8, shape=(33, 35, 3)
    )
    rng = np.random.default_rng(4)
    for row in range(0, 33, 5):
        source[row:row + 5] = rng.integers(
            0, 256, (min(5, 33 - row), 35, 3), dtype=np.uint8
        )
    source.flush()
    expected = apply_operation(np.asarray(source), "blur")
    del source
    adapter = ImageProcessingAdapter(
        MockDSMAPI(), operation="blur", tile_height=16, tile_width=16, halo=2
    )

    result = await adapter.run_to_memmap(source_path, output_path)

    assert isinstance(result, np.memmap)
    assert result.shape == (33, 35, 3)
    np.testing.assert_array_equal(result, expected)
    assert isinstance(np.load(output_path, mmap_mode="r"), np.memmap)


class FailAfterWrites(MockDSMAPI):
    """Lets `ok_writes` writes through, then times out."""

    def __init__(self, ok_writes: int):
        super().__init__()
        self.left = ok_writes

    async def write(self, object_id, data):
        if self.left == 0:
            raise DSMTimeoutError("injected")
        self.left -= 1
        await super().write(object_id, data)


@pytest.mark.parametrize("operation", ["invert", "grayscale"])
@pytest.mark.parametrize("shape", [(256, 256), (300, 517), (100, 100), (1, 7), (65, 64)])
async def test_output_matches_whole_image(dsm, operation, shape):
    image = make_image(*shape)
    adapter = ImageProcessingAdapter(dsm, operation=operation, tile_height=64, tile_width=64)
    result = await adapter.run(image)
    assert result.shape == image.shape
    assert np.array_equal(result, apply_operation(image, operation))


async def test_chunk_count_non_divisible(dsm):
    adapter = ImageProcessingAdapter(dsm, tile_height=128, tile_width=128)
    workload = await adapter.prepare(make_image(300, 517))
    assert len(workload.object_ids) == 3 * 5            # ceil(300/128) * ceil(517/128)
    await adapter.cleanup(workload)


async def test_single_chunk_edge_case(dsm):
    adapter = ImageProcessingAdapter(dsm, tile_height=256, tile_width=256)
    workload = await adapter.prepare(make_image(100, 100))
    assert len(workload.object_ids) == 1
    await adapter.cleanup(workload)


async def test_blur_with_halo_has_no_seams(dsm):
    image = make_image(130, 170)
    adapter = ImageProcessingAdapter(dsm, operation="blur", tile_height=50, tile_width=50, halo=2)
    result = await adapter.run(image)
    assert np.array_equal(result, apply_operation(image, "blur"))


@pytest.mark.parametrize("operation,halo", [("invert", 0), ("blur", 2)])
async def test_compute_api_processes_tiles_and_stores_outputs(operation, halo):
    dsm = MockDSMAPI()
    registry = HandlerRegistry()
    register_image_handlers(registry)
    worker = ComputeWorker("worker-1", registry, lambda _auth, _task: dsm, max_workers=2)
    api = ComputeAPI([worker], security_context=object(), max_pending=4)
    image = make_image(71, 83)
    adapter = ImageProcessingAdapter(
        dsm, operation=operation, tile_height=24, tile_width=32, halo=halo, compute_api=api
    )

    result = await adapter.run(image)
    np.testing.assert_array_equal(result, apply_operation(image, operation))
    assert dsm.live_ids == set()
    worker.close()


def test_blur_requires_halo(dsm):
    with pytest.raises(ValueError):
        ImageProcessingAdapter(dsm, operation="blur", halo=0)


def test_unknown_operation_rejected(dsm):
    with pytest.raises(ValueError):
        ImageProcessingAdapter(dsm, operation="sepia")


async def test_2d_grayscale_input(dsm):
    image = make_image(90, 70, channels=0)
    adapter = ImageProcessingAdapter(dsm, tile_height=32, tile_width=32)
    result = await adapter.run(image)
    assert np.array_equal(result, ~image)


@pytest.mark.parametrize("bad", [np.zeros((4, 4, 3), dtype=np.float32), np.zeros((0, 4, 3), np.uint8), [[1]]])
async def test_bad_input_rejected(dsm, bad):
    adapter = ImageProcessingAdapter(dsm)
    with pytest.raises(InvalidRequestError):
        await adapter.prepare(bad)


async def test_run_frees_everything(dsm):
    adapter = ImageProcessingAdapter(dsm, tile_height=32, tile_width=32)
    await adapter.run(make_image(100, 100))
    assert dsm.live_ids == set()


async def test_prepare_failure_frees_partial_allocations():
    dsm = FailAfterWrites(ok_writes=2)
    adapter = ImageProcessingAdapter(dsm, tile_height=32, tile_width=32)
    with pytest.raises(DSMTimeoutError):
        await adapter.prepare(make_image(100, 100))
    assert dsm.live_ids == set()


async def test_execute_failure_frees_partial_outputs():
    image = make_image(100, 100)
    tiles = 4 * 4
    dsm = FailAfterWrites(ok_writes=tiles + 1)           # prepare ok, 1 output written, then fail
    adapter = ImageProcessingAdapter(dsm, tile_height=32, tile_width=32)
    workload = await adapter.prepare(image)
    with pytest.raises(DSMTimeoutError):
        await adapter.execute(workload)
    assert dsm.live_ids == set(workload.object_ids)       # only the inputs remain
    await adapter.cleanup(workload)
    assert dsm.live_ids == set()


async def test_timings_recorded(dsm):
    adapter = ImageProcessingAdapter(dsm, tile_height=32, tile_width=32)
    await adapter.run(make_image(100, 100))
    assert set(adapter.timings) == {"chunking", "processing", "aggregation"}
    assert all(v >= 0 for v in adapter.timings.values())


async def test_estimate_memory(dsm):
    image = make_image(100, 100)
    adapter = ImageProcessingAdapter(dsm, tile_height=32, tile_width=32)
    assert adapter.estimate_memory(image) == 2 * image.nbytes     # halo 0: input + output
    assert adapter.estimate_memory("some/path.png") is None


async def test_image_file_round_trip(dsm, tmp_path):
    image = make_image(80, 120)
    src, dst = tmp_path / "in.png", tmp_path / "out.png"
    cv2.imwrite(str(src), image)
    adapter = ImageProcessingAdapter(dsm, tile_height=32, tile_width=32)
    result = await adapter.run(str(src))
    adapter.save(dst, result)
    assert np.array_equal(cv2.imread(str(dst)), ~image)
