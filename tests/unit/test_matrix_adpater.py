import numpy as np
import pytest

from application.matrix_multiplication.matrix_adapter import MatrixMultiplicationAdapter
from common.types.errors import DSMTimeoutError, InvalidRequestError
from application.matrix_multiplication.compute_handlers import register_matrix_handlers
from compute import ComputeAPI, ComputeWorker, HandlerRegistry, TaskStatus
from tests.mocks.mock_dsm_api import MockDSMAPI


@pytest.mark.asyncio
async def test_memmap_matrix_inputs_and_file_backed_output(tmp_path):
    adapter = MatrixMultiplicationAdapter(MockDSMAPI(), block_size=4)
    a_path, b_path, c_path = (tmp_path / "a.npy", tmp_path / "b.npy", tmp_path / "c.npy")
    a_map = adapter.generate_memmap_matrix(a_path, (9, 7), seed=11, rows_per_chunk=3)
    b_map = adapter.generate_memmap_matrix(b_path, (7, 11), seed=12, rows_per_chunk=2)
    expected = np.asarray(a_map) @ np.asarray(b_map)
    del a_map, b_map

    result = await adapter.run_memmaps(a_path, b_path, c_path)

    assert isinstance(result, np.memmap)
    assert result.shape == (9, 11)
    np.testing.assert_allclose(result, expected, rtol=1e-5, atol=1e-5)
    assert np.load(c_path, mmap_mode="r").shape == (9, 11)


def rand(m, n, dtype=np.float64, seed=0):
    return np.random.default_rng(seed).standard_normal((m, n)).astype(dtype)


class FailAfterWrites(MockDSMAPI):
    def __init__(self, ok_writes: int):
        super().__init__()
        self.left = ok_writes

    async def write(self, object_id, data):
        if self.left == 0:
            raise DSMTimeoutError("injected")
        self.left -= 1
        await super().write(object_id, data)


class ReadTrackingDSM(MockDSMAPI):
    def __init__(self):
        super().__init__()
        self.reads = []

    async def read(self, object_id):
        self.reads.append(object_id)
        return await super().read(object_id)


@pytest.mark.parametrize(
    "m,k,n,bs",
    [
        (64, 64, 64, 16),        # divisible
        (100, 70, 130, 32),      # nothing divisible
        (5, 5, 5, 512),          # one block
        (1, 9, 1, 4),            # row vector @ column vector
        (33, 17, 41, 8),
    ],
)
async def test_matches_numpy(dsm, m, k, n, bs):
    a, b = rand(m, k, seed=1), rand(k, n, seed=2)
    result = await MatrixMultiplicationAdapter(dsm, block_size=bs).run((a, b))
    assert result.shape == (m, n)
    np.testing.assert_allclose(result, a @ b, rtol=1e-10, atol=1e-10)


async def test_2x2_block_formula(dsm):
    """Spec section 8: C11 = A11B11 + A12B21."""
    a, b = rand(4, 4, seed=3), rand(4, 4, seed=4)
    c = await MatrixMultiplicationAdapter(dsm, block_size=2).run((a, b))
    expected_c11 = a[:2, :2] @ b[:2, :2] + a[:2, 2:] @ b[2:, :2]
    np.testing.assert_allclose(c[:2, :2], expected_c11, rtol=1e-12)


async def test_execute_reads_matrix_blocks_as_pairs():
    dsm = ReadTrackingDSM()
    a, b = rand(8, 16, seed=7), rand(16, 8, seed=8)
    adapter = MatrixMultiplicationAdapter(dsm, block_size=8)
    workload = await adapter.prepare((a, b))
    a_ids = adapter._index(workload, "A")
    b_ids = adapter._index(workload, "B")
    dsm.reads.clear()

    await adapter.execute(workload)

    assert dsm.reads[:4] == [a_ids[(0, 0)], b_ids[(0, 0)], a_ids[(0, 1)], b_ids[(1, 0)]]


async def test_compute_api_multiplies_and_reduces_blocks_on_worker():
    dsm = MockDSMAPI()
    registry = HandlerRegistry()
    register_matrix_handlers(registry)
    worker = ComputeWorker("worker-1", registry, lambda _auth, _task: dsm, max_workers=2)
    api = ComputeAPI([worker], security_context=object(), max_pending=3)
    a, b = rand(13, 17, seed=31), rand(17, 11, seed=32)

    result = await MatrixMultiplicationAdapter(dsm, block_size=5, compute_api=api).run((a, b))

    np.testing.assert_allclose(result, a @ b, rtol=1e-10, atol=1e-10)
    assert api._results and all(r.node_id == "worker-1" and r.status is TaskStatus.COMPLETED
                                for r in api._results.values())
    assert dsm.live_ids == set()
    worker.close()


async def test_identity(dsm):
    a = rand(37, 37, seed=5)
    result = await MatrixMultiplicationAdapter(dsm, block_size=10).run((a, np.eye(37)))
    np.testing.assert_allclose(result, a, atol=1e-12)


async def test_object_count(dsm):
    adapter = MatrixMultiplicationAdapter(dsm, block_size=32)
    workload = await adapter.prepare((rand(100, 70), rand(70, 130)))
    # A: ceil(100/32) * ceil(70/32) = 4*3 ; B: 3 * ceil(130/32)=5  -> 12 + 15
    assert len(workload.object_ids) == 27
    await adapter.cleanup(workload)


async def test_float32_preserved(dsm):
    a, b = rand(20, 20, np.float32), rand(20, 20, np.float32, seed=9)
    result = await MatrixMultiplicationAdapter(dsm, block_size=8).run((a, b))
    assert result.dtype == np.float32
    np.testing.assert_allclose(result, a @ b, rtol=1e-4, atol=1e-4)


async def test_integers_promoted_to_float64(dsm):
    a = np.arange(12).reshape(3, 4)
    b = np.arange(8).reshape(4, 2)
    result = await MatrixMultiplicationAdapter(dsm, block_size=2).run((a, b))
    assert result.dtype == np.float64
    np.testing.assert_allclose(result, a @ b)


@pytest.mark.parametrize(
    "bad",
    [
        (np.zeros((3, 4)), np.zeros((5, 2))),   # inner mismatch
        (np.zeros(4), np.zeros((4, 2))),        # not 2-D
        (np.zeros((0, 4)), np.zeros((4, 2))),   # empty
        "not a pair",
    ],
)
async def test_bad_input_rejected(dsm, bad):
    with pytest.raises(InvalidRequestError):
        await MatrixMultiplicationAdapter(dsm, block_size=4).prepare(bad)


def test_bad_block_size(dsm):
    with pytest.raises(ValueError):
        MatrixMultiplicationAdapter(dsm, block_size=0)


async def test_run_frees_everything(dsm):
    await MatrixMultiplicationAdapter(dsm, block_size=16).run((rand(40, 40), rand(40, 40)))
    assert dsm.live_ids == set()


async def test_prepare_failure_frees_partial_allocations():
    dsm = FailAfterWrites(ok_writes=3)
    with pytest.raises(DSMTimeoutError):
        await MatrixMultiplicationAdapter(dsm, block_size=8).prepare((rand(40, 40), rand(40, 40)))
    assert dsm.live_ids == set()


async def test_execute_failure_frees_partial_outputs():
    a, b = rand(16, 16), rand(16, 16)
    inputs = 4 + 4                                        # 2x2 blocks each at block_size 8
    dsm = FailAfterWrites(ok_writes=inputs + 1)
    adapter = MatrixMultiplicationAdapter(dsm, block_size=8)
    workload = await adapter.prepare((a, b))
    with pytest.raises(DSMTimeoutError):
        await adapter.execute(workload)
    assert dsm.live_ids == set(workload.object_ids)
    await adapter.cleanup(workload)
    assert dsm.live_ids == set()


async def test_timings_and_estimate(dsm):
    a, b = rand(30, 20), rand(20, 10)
    adapter = MatrixMultiplicationAdapter(dsm, block_size=8)
    await adapter.run((a, b))
    assert set(adapter.timings) == {"chunking", "processing", "aggregation"}
    assert adapter.estimate_memory((a, b)) == (30 * 20 + 20 * 10 + 30 * 10) * 8
    assert adapter.estimate_memory("junk") is None
