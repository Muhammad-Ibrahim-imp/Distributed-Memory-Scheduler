"""Plot measured DSM benchmark JSON files without synthesizing missing metrics."""

from __future__ import annotations

import json
from pathlib import Path

import matplotlib.pyplot as plt
import numpy as np


ROOT = Path(__file__).resolve().parents[1]
RESULTS = ROOT / "results"
LOCALITY = RESULTS / "distributed-benchmark-smoke.json"
ROUND_ROBIN = RESULTS / "distributed-benchmark-round-robin.json"
IMMUTABLE = RESULTS / "immutable-local-access.json"


def load(path: Path) -> list[dict]:
    return json.loads(path.read_text(encoding="utf-8"))


def application_rows(rows: list[dict], mode: str | None = None) -> dict[str, float]:
    return {
        row["workload"]: float(row.get("end_to_end_median_s", row["end_to_end_s"]))
        for row in rows
        if row.get("workload", "").startswith("image_") or row.get("workload") == "matrix_multiply"
        if mode is None or row.get("execution_mode") == mode
    }


def main() -> None:
    locality = load(LOCALITY)
    round_robin = load(ROUND_ROBIN)
    immutable = load(IMMUTABLE)
    workloads = ["image_invert", "image_grayscale", "image_blur", "matrix_multiply"]
    modes = [
        ("Native", application_rows(immutable, "native")),
        ("Local tiled", application_rows(immutable, "local_tiled")),
        ("Client DSM", application_rows(immutable, "client_compute")),
        ("Distributed, mediated", application_rows(locality, "distributed_compute")),
        ("Distributed, locality + sealed", application_rows(immutable, "distributed_compute")),
        ("Distributed, round robin", application_rows(round_robin, "distributed_compute")),
    ]
    x = np.arange(len(workloads))
    width = 0.13
    fig, ax = plt.subplots(figsize=(12, 6))
    for index, (label, values) in enumerate(modes):
        ax.bar(x + (index - (len(modes) - 1) / 2) * width,
               [values.get(workload, np.nan) for workload in workloads], width, label=label)
    ax.set_yscale("log")
    ax.set_xticks(x, ["Invert 8×11", "Grayscale 8×11", "Blur 8×11", "Matmul 7×5 @ 5×6"])
    ax.set_ylabel("Median end-to-end time (seconds, log scale)")
    ax.set_title("Small-input smoke runs; two application repetitions per mode")
    ax.legend(fontsize=8, ncols=2)
    ax.grid(axis="y", which="both", alpha=0.25)
    fig.tight_layout()
    fig.savefig(RESULTS / "distributed-execution-time.png", dpi=160)
    plt.close(fig)

    immutable_by_workload = {row["workload"]: row for row in immutable
                             if row.get("execution_mode") == "distributed_compute"}
    local_bytes = [immutable_by_workload[w].get("local_read_bytes", 0) for w in workloads]
    remote_bytes = [immutable_by_workload[w].get("remote_read_bytes", 0) for w in workloads]
    fig, ax = plt.subplots(figsize=(10, 5))
    ax.bar(x - 0.18, local_bytes, 0.36, label="Direct local RAM input bytes")
    ax.bar(x + 0.18, remote_bytes, 0.36, label="Coordinator-mediated remote input bytes")
    ax.set_xticks(x, ["Invert", "Grayscale", "Blur", "Matmul"])
    ax.set_ylabel("Input payload bytes measured by worker")
    ax.set_title("Worker input locality, immutable direct-access run")
    ax.legend()
    ax.grid(axis="y", alpha=0.25)
    fig.tight_layout()
    fig.savefig(RESULTS / "distributed-input-locality.png", dpi=160)
    plt.close(fig)


if __name__ == "__main__":
    main()
