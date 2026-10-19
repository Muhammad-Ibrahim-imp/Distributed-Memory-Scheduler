"""Adapter <-> DSM API contract (Principles 1, 2 and 8).

1. Adapters touch the DSM only through alloc/read/write/free, and only with ids
   alloc() returned (StrictDSM refuses anything else).
2. Adapter source never imports DSM / scheduler / node / network / security code.
3. DSM core source never contains application-specific logic.
"""

import ast
import re
from pathlib import Path

import numpy as np
import pytest

from application.framework.config import load_adapter_config
from application.image_processing.image_adapter import ImageProcessingAdapter
from application.matrix_multiplication.matrix_adapter import MatrixMultiplicationAdapter
from tests.mocks.mock_dsm_api import MockDSMAPI

ROOT = Path(__file__).resolve().parents[2]
GENERIC_API = {"alloc", "read", "write", "free"}


class StrictDSM:
    """Exposes ONLY the four generic calls and rejects ids it never issued."""

    def __init__(self):
        self._inner = MockDSMAPI()
        self._issued: set[str] = set()

    @property
    def calls(self):
        return self._inner.calls

    async def alloc(self, size):
        oid = await self._inner.alloc(size)
        self._issued.add(oid)
        return oid

    async def read(self, object_id):
        assert object_id in self._issued, "read of an id alloc() never returned"
        return await self._inner.read(object_id)

    async def write(self, object_id, data):
        assert object_id in self._issued, "write to an id alloc() never returned"
        assert isinstance(data, (bytes, bytearray))
        return await self._inner.write(object_id, data)

    async def free(self, object_id):
        assert object_id in self._issued, "free of an id alloc() never returned"
        return await self._inner.free(object_id)


async def _run_both(dsm):
    rng = np.random.default_rng(0)
    image = rng.integers(0, 256, (70, 90, 3), dtype=np.uint8)
    await ImageProcessingAdapter(dsm, tile_height=32, tile_width=32).run(image)
    a, b = rng.standard_normal((20, 30)), rng.standard_normal((30, 10))
    await MatrixMultiplicationAdapter(dsm, block_size=8).run((a, b))


async def test_adapters_use_only_generic_api():
    dsm = StrictDSM()
    await _run_both(dsm)
    used = {op for op, *_ in dsm.calls}
    assert used <= GENERIC_API
    assert used == GENERIC_API                    # and each one really is used


async def test_adapters_leave_nothing_allocated():
    dsm = StrictDSM()
    await _run_both(dsm)
    assert dsm._inner.live_ids == set()


def _imports(path: Path) -> set[str]:
    roots = set()
    for node in ast.walk(ast.parse(path.read_text(encoding="utf-8"))):
        if isinstance(node, ast.Import):
            roots |= {a.name.split(".")[0] for a in node.names}
        elif isinstance(node, ast.ImportFrom) and node.module and node.level == 0:
            roots.add(node.module.split(".")[0])
    return roots


def test_adapters_do_not_import_dsm_internals():
    forbidden = {"dsm", "scheduler", "memory_node", "network", "security"}
    files = list((ROOT / "application").rglob("*.py"))
    assert files, "applications/ not found; check the folder name (applications, plural)"
    for path in files:
        bad = _imports(path) & forbidden
        assert not bad, f"{path.relative_to(ROOT)} imports DSM internals: {sorted(bad)}"


APP_LOGIC = re.compile(
    r"application\s*==|applications\.(image|matrix)|image_processing|matrix_multiplication"
)


@pytest.mark.parametrize("core", ["dsm", "scheduler", "memory_node", "network"])
def test_dsm_core_has_no_application_logic(core):
    folder = ROOT / core
    if not folder.exists():
        pytest.skip(f"{core}/ not present")
    for path in folder.rglob("*.py"):
        hit = APP_LOGIC.search(path.read_text(encoding="utf-8"))
        assert not hit, f"{path.relative_to(ROOT)} contains application-specific code: {hit.group(0)}"


def test_adapters_build_from_config():
    cfg = load_adapter_config(ROOT / "configs" / "Adapters.yaml")
    assert cfg.image.tile_height is not None and cfg.image.tile_width is not None
    dsm = MockDSMAPI()
    image = ImageProcessingAdapter(
        dsm,
        tile_height=cfg.image.tile_height,
        tile_width=cfg.image.tile_width,
        halo=cfg.image.halo,
    )
    matrix = MatrixMultiplicationAdapter(dsm, block_size=cfg.matrix.block_size)
    assert image.tile_height == cfg.image.tile_height
    assert image.tile_width == cfg.image.tile_width
    assert image.halo == cfg.image.halo
    assert matrix.block_size == cfg.matrix.block_size