"""Plot correctness-checked file-backed matrix benchmark results."""

from __future__ import annotations

import json
from pathlib import Path
import statistics

import matplotlib.pyplot as plt


ROOT = Path(__file__).resolve().parents[1]
RESULTS = ROOT / "results"


def main() -> None:
    rows = json.loads((RESULTS / "matrix-256-results.json").read_text(encoding="utf-8"))
    samples = [row for row in rows if "repetition" in row and row["numerically_correct"]]
    dimensions = sorted({row["dimension"] for row in samples})
    modes = ("native", "local_tiled", "distributed_compute")
    fig, ax = plt.subplots(figsize=(8, 5))
    for mode in modes:
        values = []
        for dimension in dimensions:
            group = [row["end_to_end_s"] for row in samples
                     if row["mode"] == mode and row["dimension"] == dimension]
            values.append(statistics.median(group) if group else float("nan"))
        ax.plot(dimensions, values, marker="o", label=mode.replace("_", " "))
    ax.set_xscale("log", base=2)
    ax.set_yscale("log")
    ax.set_xlabel("Square matrix dimension (float32)")
    ax.set_ylabel("End-to-end median (seconds; two repetitions)")
    ax.set_title("File-backed matrix run — 3 resource processes, 128 tiles, 1 BLAS thread")
    ax.grid(True, which="both", alpha=0.25)
    ax.legend()
    fig.tight_layout()
    fig.savefig(RESULTS / "matrix-dimension-execution-time.png", dpi=160)
    plt.close(fig)


if __name__ == "__main__":
    main()
