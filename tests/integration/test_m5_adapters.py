import numpy as np
import pytest

from application.image_processing.image_adapter import ImageProcessingAdapter, apply_operation
from application.matrix_multiplication.matrix_adapter import MatrixMultiplicationAdapter


pytestmark = pytest.mark.integration


async def test_image_adapter_runs_through_runtime(integration_components):
    runtime, node, security = integration_components
    client = runtime.bind("image-integration")
    image = np.random.default_rng(10).integers(0, 256, (70, 90, 3), dtype=np.uint8)

    result = await ImageProcessingAdapter(
        client, operation="invert", tile_height=32, tile_width=32
    ).run(image)

    np.testing.assert_array_equal(result, apply_operation(image, "invert"))
    assert node.data == {}
    assert security.access_checks


async def test_matrix_adapter_runs_through_runtime(integration_components):
    runtime, node, security = integration_components
    client = runtime.bind("matrix-integration")
    rng = np.random.default_rng(11)
    a = rng.standard_normal((33, 17))
    b = rng.standard_normal((17, 41))

    result = await MatrixMultiplicationAdapter(client, block_size=8).run((a, b))

    np.testing.assert_allclose(result, a @ b, rtol=1e-10, atol=1e-10)
    assert node.data == {}
    assert security.access_checks
