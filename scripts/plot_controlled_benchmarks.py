"""Plot a same-cluster coordinator-mediated versus direct-read comparison."""

from __future__ import annotations

import json
from pathlib import Path

import matplotlib.pyplot as plt
import numpy as np


ROOT = Path(__file__).resolve().parents[1]
RESULTS = ROOT / "results"


def load_rows(name: str) -> dict[str, dict]:
    rows = json.loads((RESULTS / name).read_text(encoding="utf-8"))
    return {
        row["workload"]: row for row in rows
        if row.get("execution_mode") == "distributed_compute"
        and row.get("workload", "").startswith("image_") or
        row.get("execution_mode") == "distributed_compute"
        and row.get("workload") == "matrix_multiply"
    }


def main() -> None:
    mediated = load_rows("controlled-mediated-3nodes.json")
    direct = load_rows("controlled-direct-3nodes.json")
    workloads = ["image_invert", "image_grayscale", "image_blur", "matrix_multiply"]
    x = np.arange(len(workloads))
    width = 0.36

    fig, ax = plt.subplots(figsize=(10, 5))
    for offset, label, data, color in (
        (-width / 2, "Coordinator-mediated", mediated, "#c43c39"),
        (width / 2, "Direct local RAM", direct, "#2878b5"),
    ):
        medians = [data[name]["end_to_end_median_s"] for name in workloads]
        lows = [medians[i] - data[name]["end_to_end_min_s"]
                for i, name in enumerate(workloads)]
        highs = [data[name]["end_to_end_max_s"] - medians[i]
                 for i, name in enumerate(workloads)]
        ax.bar(x + offset, medians, width, label=label, color=color,
               yerr=np.array([lows, highs]), capsize=3)
    ax.set_xticks(x, ["Invert", "Grayscale", "Blur", "Matmul"])
    ax.set_ylabel("End-to-end median (seconds; range shows 2 repetitions)")
    ax.set_title("Same three-node cluster, fixed input and scheduling configuration")
    ax.legend()
    ax.grid(axis="y", alpha=0.25)
    fig.tight_layout()
    fig.savefig(RESULTS / "controlled-access-time.png", dpi=160)
    plt.close(fig)

    direct_local = [direct[name].get("local_read_bytes", 0) for name in workloads]
    direct_remote = [direct[name].get("remote_read_bytes", 0) for name in workloads]
    mediated_remote = [mediated[name].get("remote_read_bytes", 0) for name in workloads]
    fig, ax = plt.subplots(figsize=(10, 5))
    ax.bar(x - width / 2, mediated_remote, width,
           label="Mediated input payload via coordinator", color="#c43c39")
    ax.bar(x + width / 2, direct_local, width,
           label="Direct local RAM payload", color="#2878b5")
    ax.bar(x + width / 2, direct_remote, width, bottom=direct_local,
           label="Remote payload retained in direct mode", color="#f0a33a")
    ax.set_xticks(x, ["Invert", "Grayscale", "Blur", "Matmul"])
    ax.set_ylabel("Input payload bytes measured at worker")
    ax.set_title("Same input and task graph; only local-read path changed")
    ax.legend()
    ax.grid(axis="y", alpha=0.25)
    fig.tight_layout()
    fig.savefig(RESULTS / "controlled-access-bytes.png", dpi=160)
    plt.close(fig)


if __name__ == "__main__":
    main()
