"""Plot measured native and local tiled benchmark medians."""
import json
from pathlib import Path

import matplotlib.pyplot as plt

root = Path(__file__).resolve().parents[1]
series = {}
for name, path in (("Native NumPy", root / "results/final-native.json"),
                   ("Local tiled", root / "results/final-local.json")):
    rows = json.loads(path.read_text(encoding="utf-8"))
    series[name] = ({row["dimension"]: row["median_s"] for row in rows},
                    {row["dimension"]: row["variability_s"] for row in rows})
fig, ax = plt.subplots(figsize=(7, 4.5))
for name, (medians, ranges) in series.items():
    xs = sorted(medians)
    ax.errorbar(xs, [medians[x] for x in xs], yerr=[ranges[x] / 2 for x in xs],
                marker="o", capsize=4, label=name)
ax.set(xlabel="Matrix dimension (N × N)", ylabel="End-to-end seconds",
       title="Measured laptop matrix multiplication (3 repetitions)")
ax.set_xticks(sorted({x for medians, _ in series.values() for x in medians}))
ax.grid(True, alpha=.3)
ax.legend()
fig.tight_layout()
out = root / "results/final-matrix-comparison.png"
fig.savefig(out, dpi=160)
print(out)
