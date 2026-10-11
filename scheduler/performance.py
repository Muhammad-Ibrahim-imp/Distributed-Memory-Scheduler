# Purpose:
# Generates the performance-scaling graphs required by the project.
#
# Required graphs:
#   1. Nodes vs Execution Time
#   2. Nodes vs Speedup
#   3. Nodes vs Efficiency
#   4. Nodes vs Throughput
#   5. Memory Utilization
#   6. CPU Utilization
#   7. Network Latency
#   8. Network Throughput
#
# This module visualizes measurements.
# It does not measure CPU, memory, or network itself.

from pathlib import Path

import matplotlib.pyplot as plt

from scheduler.benchmark import ScalingResult


def _prepare(
    results: list[ScalingResult],
):
    if not results:
        raise ValueError(
            "At least one benchmark result is required."
        )

    results = sorted(
        results,
        key=lambda result: result.node_count,
    )

    nodes = [
        result.node_count
        for result in results
    ]

    return results, nodes


def _save_line_plot(
    x,
    y,
    xlabel: str,
    ylabel: str,
    title: str,
    output_path: Path,
) -> None:

    plt.figure()
    plt.plot(x, y, marker="o")
    plt.xlabel(xlabel)
    plt.ylabel(ylabel)
    plt.title(title)
    plt.grid(True)
    plt.tight_layout()
    plt.savefig(output_path)
    plt.close()


def plot_execution_time(
    results: list[ScalingResult],
    output_dir: str | Path = "results",
) -> Path:

    results, nodes = _prepare(results)

    output_dir = Path(output_dir)
    output_dir.mkdir(
        parents=True,
        exist_ok=True,
    )

    path = output_dir / "nodes_vs_execution_time.png"

    _save_line_plot(
        nodes,
        [result.execution_time for result in results],
        "Number of Nodes",
        "Execution Time (s)",
        "Nodes vs Execution Time",
        path,
    )

    return path


def plot_speedup(
    results: list[ScalingResult],
    output_dir: str | Path = "results",
) -> Path:

    results, nodes = _prepare(results)

    output_dir = Path(output_dir)
    output_dir.mkdir(
        parents=True,
        exist_ok=True,
    )

    path = output_dir / "nodes_vs_speedup.png"

    _save_line_plot(
        nodes,
        [result.speedup for result in results],
        "Number of Nodes",
        "Speedup",
        "Nodes vs Speedup",
        path,
    )

    return path


def plot_efficiency(
    results: list[ScalingResult],
    output_dir: str | Path = "results",
) -> Path:

    results, nodes = _prepare(results)

    output_dir = Path(output_dir)
    output_dir.mkdir(
        parents=True,
        exist_ok=True,
    )

    path = output_dir / "nodes_vs_efficiency.png"

    _save_line_plot(
        nodes,
        [result.efficiency for result in results],
        "Number of Nodes",
        "Efficiency",
        "Nodes vs Efficiency",
        path,
    )

    return path


def plot_throughput(
    results: list[ScalingResult],
    output_dir: str | Path = "results",
) -> Path:

    results, nodes = _prepare(results)

    output_dir = Path(output_dir)
    output_dir.mkdir(
        parents=True,
        exist_ok=True,
    )

    path = output_dir / "nodes_vs_throughput.png"

    _save_line_plot(
        nodes,
        [result.throughput for result in results],
        "Number of Nodes",
        "Throughput (objects/sec)",
        "Nodes vs Throughput",
        path,
    )

    return path


def plot_memory_utilization(
    results: list[ScalingResult],
    output_dir: str | Path = "results",
) -> Path:

    results, nodes = _prepare(results)

    output_dir = Path(output_dir)
    output_dir.mkdir(
        parents=True,
        exist_ok=True,
    )

    path = output_dir / "memory_utilization.png"

    _save_line_plot(
        nodes,
        [
            result.memory_utilization
            for result in results
        ],
        "Number of Nodes",
        "Memory Utilization (%)",
        "Memory Utilization",
        path,
    )

    return path


def plot_cpu_utilization(
    results: list[ScalingResult],
    output_dir: str | Path = "results",
) -> Path:

    results, nodes = _prepare(results)

    output_dir = Path(output_dir)
    output_dir.mkdir(
        parents=True,
        exist_ok=True,
    )

    path = output_dir / "cpu_utilization.png"

    _save_line_plot(
        nodes,
        [
            result.cpu_utilization
            for result in results
        ],
        "Number of Nodes",
        "CPU Utilization (%)",
        "CPU Utilization",
        path,
    )

    return path


def plot_network_latency(
    results: list[ScalingResult],
    output_dir: str | Path = "results",
) -> Path:

    results, nodes = _prepare(results)

    output_dir = Path(output_dir)
    output_dir.mkdir(
        parents=True,
        exist_ok=True,
    )

    path = output_dir / "network_latency.png"

    _save_line_plot(
        nodes,
        [
            result.network_latency
            for result in results
        ],
        "Number of Nodes",
        "Network Latency (ms)",
        "Network Latency",
        path,
    )

    return path


def plot_network_throughput(
    results: list[ScalingResult],
    output_dir: str | Path = "results",
) -> Path:

    results, nodes = _prepare(results)

    output_dir = Path(output_dir)
    output_dir.mkdir(
        parents=True,
        exist_ok=True,
    )

    path = output_dir / "network_throughput.png"

    _save_line_plot(
        nodes,
        [
            result.network_throughput
            for result in results
        ],
        "Number of Nodes",
        "Network Throughput (Mbps)",
        "Network Throughput",
        path,
    )

    return path


def plot_scaling(
    results: list[ScalingResult],
    output_dir: str | Path = "results",
) -> list[Path]:

    output_dir = Path(output_dir)

    return [
        plot_execution_time(results, output_dir),
        plot_speedup(results, output_dir),
        plot_efficiency(results, output_dir),
        plot_throughput(results, output_dir),
        plot_memory_utilization(results, output_dir),
        plot_cpu_utilization(results, output_dir),
        plot_network_latency(results, output_dir),
        plot_network_throughput(results, output_dir),
    ]
