import numpy as np
import pytest
from application.framework.base import ALLOCATION, CHUNKING, TRANSFER
from application.framework.config import AdapterConfig, ImageConfig
from application.framework.errors import InvalidInputError
from application.framework.serialization import unpack_array
from application.image_processing.adapter import ImageProcessingAdapter
from application.image_processing.chunker import reassemble
from common.types.errors import OutOfCapacityError


def cfg(**kw):
    return AdapterConfig(image=ImageConfig(**kw))


def image(shape=(103, 97, 3)):
    return np.random.default_rng(1).integers(0, 255, shape, dtype=np.uint8)


async def read_tiles(dsm, w):
    return [unpack_array(await dsm.read(o)) for o in w.object_ids]


async def test_one_object_per_tile_and_layout_recorded(dsm):
    img = image()
    w = await ImageProcessingAdapter(dsm, cfg(tile_height=32, tile_width=32)).prepare(img)
    assert len(w.object_ids) == 16 and dsm.live_ids == set(w.object_ids)
    assert set(w.layout) == set(w.object_ids) and w.image_shape == img.shape
    first = w.layout[w.object_ids[0]]
    assert first["row"] == 0 and first["col"] == 0 and first["dtype"] == "|u1"


async def test_stored_tiles_reassemble_to_original_with_halo(dsm):
    img = image()
    w = await ImageProcessingAdapter(dsm, cfg(tile_height=40, tile_width=30, halo=2)).prepare(img)
    out = reassemble(await read_tiles(dsm, w), w.tile_specs(), w.image_shape, np.dtype(w.dtype))
    assert np.array_equal(out, img)


async def test_single_chunk_when_image_smaller_than_tile(dsm):
    w = await ImageProcessingAdapter(dsm, cfg(tile_height=256, tile_width=256)).prepare(image((20, 30)))
    assert len(w.object_ids) == 1


async def test_byte_target_used_when_tiles_not_configured(dsm):
    w = await ImageProcessingAdapter(dsm, cfg(target_chunk_bytes=3 * 50 * 50)).prepare(image((100, 100, 3)))
    assert len(w.object_ids) == 4


async def test_run_path_frees_everything_even_though_execute_is_not_built_yet(dsm):
    a = ImageProcessingAdapter(dsm)
    with pytest.raises(NotImplementedError):
        await a.run(image())
    assert dsm.live_ids == set()


async def test_failure_mid_prepare_leaves_no_orphans(dsm):
    dsm.inject("alloc", OutOfCapacityError("full"))
    with pytest.raises(OutOfCapacityError):
        await ImageProcessingAdapter(dsm, cfg(tile_height=16, tile_width=16)).prepare(image())
    assert dsm.live_ids == set()


async def test_oversized_image_rejected_before_any_dsm_call(dsm):
    with pytest.raises(InvalidInputError):
        await ImageProcessingAdapter(dsm, cfg(max_image_bytes=1000)).prepare(image())
    assert dsm.calls == []


@pytest.mark.parametrize("bad", [123, None, np.zeros((2, 2, 2, 2), np.uint8),
                                 np.zeros((0, 5), np.uint8), np.array([["a"]])])
async def test_bad_inputs_rejected(dsm, bad):
    with pytest.raises(InvalidInputError):
        await ImageProcessingAdapter(dsm).prepare(bad)


async def test_loads_png_from_disk(dsm, tmp_path):
    cv2 = pytest.importorskip("cv2")
    img = image((50, 60, 3))
    p = tmp_path / "x.png"
    cv2.imwrite(str(p), img)
    w = await ImageProcessingAdapter(dsm, cfg(tile_height=25, tile_width=30)).prepare(p)
    assert w.image_shape == (50, 60, 3) and len(w.object_ids) == 4
    assert np.array_equal(reassemble(await read_tiles(dsm, w), w.tile_specs(), w.image_shape, np.uint8), img)


async def test_missing_file_rejected(dsm, tmp_path):
    with pytest.raises(InvalidInputError):
        await ImageProcessingAdapter(dsm).prepare(tmp_path / "nope.png")


async def test_phases_timed_and_only_generic_api(dsm):
    w = await ImageProcessingAdapter(dsm, cfg(tile_height=20, tile_width=20)).prepare(image())
    for phase in (CHUNKING, ALLOCATION, TRANSFER):
        assert w.timings.get(phase) > 0
    assert {c[0] for c in dsm.calls} <= {"alloc", "write", "read", "free"}


async def test_estimate_memory(dsm):
    assert ImageProcessingAdapter(dsm).estimate_memory(image((10, 10, 3))) == 300