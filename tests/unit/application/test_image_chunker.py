import numpy as np
import pytest
from application.framework.errors import ChunkingError, ReconstructionError
from application.image_processing.chunker import (
    choose_tile_shape, extract_tile, plan_tiles, reassemble,
)

SHAPES = [(100, 100, 3), (103, 97, 3), (7, 5), (1, 1, 3), (64, 1000), (1000, 64, 1)]


@pytest.mark.parametrize("shape", SHAPES)
@pytest.mark.parametrize("tile", [(10, 10), (33, 17), (1, 1), (500, 500)])
@pytest.mark.parametrize("halo", [0, 1, 3])
def test_core_boxes_partition_image_exactly_once(shape, tile, halo):
    specs = plan_tiles(shape[0], shape[1], *tile, halo)
    cover = np.zeros(shape[:2], int)
    for s in specs:
        cover[s.y0:s.y1, s.x0:s.x1] += 1
        assert 0 <= s.py0 <= s.y0 < s.y1 <= s.py1 <= shape[0]
        assert 0 <= s.px0 <= s.x0 < s.x1 <= s.px1 <= shape[1]
    assert (cover == 1).all()


@pytest.mark.parametrize("shape", SHAPES)
@pytest.mark.parametrize("halo", [0, 2])
def test_roundtrip_is_lossless(shape, halo):
    rng = np.random.default_rng(0)
    img = rng.integers(0, 255, shape, dtype=np.uint8)
    specs = plan_tiles(shape[0], shape[1], 16, 24, halo)
    out = reassemble([extract_tile(img, s) for s in specs], specs, img.shape, img.dtype)
    assert np.array_equal(out, img)


def test_halo_adds_border_but_clamps_at_edges():
    specs = plan_tiles(30, 30, 10, 10, halo=2)
    corner, centre = specs[0], specs[4]
    assert corner.padded_shape == (12, 12)      # only inner sides get halo
    assert centre.padded_shape == (14, 14)


def test_single_chunk_when_image_smaller_than_tile():
    h, w = choose_tile_shape(20, 30, 3, 1, 4 * 1024 * 1024)
    assert (h, w) == (20, 30) and len(plan_tiles(20, 30, h, w)) == 1


def test_tile_shape_from_byte_target_and_overrides():
    h, w = choose_tile_shape(5000, 5000, 3, 1, 3 * 100 * 100)
    assert (h, w) == (100, 100)
    assert choose_tile_shape(50, 80, 3, 1, 1000, tile_h=10) == (10, 80)


def test_extract_tile_is_a_copy():
    img = np.zeros((10, 10), np.uint8)
    t = extract_tile(img, plan_tiles(10, 10, 5, 5)[0])
    t[:] = 9
    assert img.max() == 0


def test_invalid_args_and_bad_tiles():
    with pytest.raises(ChunkingError):
        plan_tiles(0, 5, 1, 1)
    with pytest.raises(ChunkingError):
        plan_tiles(5, 5, 1, 1, halo=-1)
    specs = plan_tiles(10, 10, 5, 5)
    img = np.zeros((10, 10), np.uint8)
    tiles = [extract_tile(img, s) for s in specs]
    with pytest.raises(ReconstructionError):
        reassemble(tiles[:-1], specs, img.shape, img.dtype)
    tiles[0] = tiles[0][:-1]
    with pytest.raises(ReconstructionError):
        reassemble(tiles, specs, img.shape, img.dtype)