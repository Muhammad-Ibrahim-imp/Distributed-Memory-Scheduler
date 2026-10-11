import pytest
from application.framework.config import AdapterConfig, load_adapter_config
from application.framework.errors import AdapterConfigError


def test_loads_repo_config():
    c = load_adapter_config("configs/Adapters.yaml")
    assert (c.image.tile_height, c.image.tile_width, c.image.halo) == (256, 256, 0)
    assert c.matrix.block_size == 512 and c.max_concurrency == 8


@pytest.mark.parametrize("bad", ["image_processing:\n  halo: -1\n", "image_processing:\n  nope: 1\n",
                                 "bogus: 1\n", "max_concurrency: 0\n"])
def test_bad_config_rejected(tmp_path, bad):
    p = tmp_path / "a.yaml"; p.write_text(bad)
    with pytest.raises(AdapterConfigError):
        load_adapter_config(p)