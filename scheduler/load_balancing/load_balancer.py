# Purpose:
# Tracks scheduler-assigned placement workload.
#
# This is NOT application access frequency.
# It tracks how many placements B2 has assigned to each node.

from collections import defaultdict


class LoadBalancer:

    def __init__(self):
        self._placement_counts: dict[str, int] = (
            defaultdict(int)
        )

    def record_placement(
        self,
        node_id: str,
    ) -> None:

        self._placement_counts[node_id] += 1

    def get_placement_count(
        self,
        node_id: str,
    ) -> int:

        return self._placement_counts[node_id]

    def get_placement_frequencies(
        self,
        node_ids: list[str],
    ) -> dict[str, float]:

        total = sum(
            self._placement_counts[node_id]
            for node_id in node_ids
        )

        if total == 0:
            return {
                node_id: 0.0
                for node_id in node_ids
            }

        return {
            node_id: (
                self._placement_counts[node_id]
                / total
            )
            for node_id in node_ids
        }

    def reset(self) -> None:
        self._placement_counts.clear()
