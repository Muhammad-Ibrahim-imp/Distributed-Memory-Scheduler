"""Static configuration checks that do not require the Docker daemon."""

from pathlib import Path

import yaml


def test_compose_defines_three_nodes_with_unique_identities_and_credentials():
    config = yaml.safe_load(Path("docker-compose.yml").read_text(encoding="utf-8"))
    nodes = [config["services"][f"node-{index}"] for index in range(1, 4)]
    environments = [node["environment"] for node in nodes]

    assert [env["NODE_ID"] for env in environments] == ["node-1", "node-2", "node-3"]
    assert len({env["DSM_NODE_CERT_FILE"] for env in environments}) == 3
    assert len({env["DSM_NODE_KEY_FILE"] for env in environments}) == 3
    assert all("DSM_NODE_ENROLLMENT_SECRET" in env for env in environments)
