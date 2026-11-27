"""Static configuration checks that do not require the Docker daemon."""

from pathlib import Path

import yaml


def test_compose_defines_three_nodes_with_unique_identities_and_credentials():
    config = yaml.safe_load(Path("docker-compose.yml").read_text(encoding="utf-8"))
    nodes = [config["services"][f"node-{index}"] for index in range(1, 4)]
    environments = [node["environment"] for node in nodes]
    coordinator = config["services"]["coordinator"]

    assert [env["NODE_ID"] for env in environments] == [
        "${NODE_1_ID:-node-1}", "${NODE_2_ID:-node-2}", "${NODE_3_ID:-node-3}"
    ]
    assert len({env["DSM_NODE_CERT_FILE"] for env in environments}) == 3
    assert len({env["DSM_NODE_KEY_FILE"] for env in environments}) == 3
    assert all("DSM_NODE_ENROLLMENT_SECRET" in env for env in environments)
    assert all(env["DSM_COMPUTE_ENABLED"] == "${DSM_COMPUTE_ENABLED:-true}" for env in environments[:2])
    assert environments[2]["DSM_COMPUTE_ENABLED"] == "${NODE_3_COMPUTE_ENABLED:-${DSM_COMPUTE_ENABLED:-true}}"
    assert all(env["NODE_COMPUTE_SLOTS"] == "${NODE_COMPUTE_SLOTS:-1}" for env in environments)
    assert all(node["cpus"] == "${NODE_CPU_LIMIT:-2.0}" for node in nodes)
    assert coordinator["environment"]["DSM_COMPUTE_SCHEDULER"] == "${DSM_COMPUTE_SCHEDULER:-locality}"
