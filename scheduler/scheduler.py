# Purpose:
# Main B2 scheduler implementation.
#
# Responsibilities:
#   1. Obtain/accept current node state.
#   2. Refresh M5 trust state for every placement decision.
#   3. Apply scheduler-side load information.
#   4. Reject nodes without enough RAM.
#   5. Reject non-TRUSTED nodes.
#   6. Apply the configured placement policy.
#   7. Return PlacementDecision to A2.

from dataclasses import replace

from common.interfaces.node_state import NodeStateProvider
from common.interfaces.scheduler import Scheduler
from common.interfaces.security_state import SecurityStateProvider
from common.types.node import NodeStats
from common.types.placement import PlacementDecision

from scheduler.load_balancing.load_balancer import LoadBalancer
from scheduler.policies.base import PlacementPolicy
from scheduler.policies.security import SecurityPolicy


class DefaultScheduler(Scheduler):

    def __init__(
        self,
        policy: PlacementPolicy,
        node_state_provider: NodeStateProvider | None = None,
        security_provider: SecurityStateProvider | None = None,
        load_balancer: LoadBalancer | None = None,
    ):
        self.policy = policy
        self.node_state_provider = node_state_provider
        self.security_provider = security_provider
        self.load_balancer = (
            load_balancer
            or LoadBalancer()
        )

    def select_node(
        self,
        object_id: str,
        object_size: int,
        nodes: list[NodeStats],
    ) -> PlacementDecision:

        if not object_id:
            raise ValueError(
                "object_id cannot be empty."
            )

        if object_size < 0:
            raise ValueError(
                "object_size cannot be negative."
            )

        if not nodes:
            raise RuntimeError(
                "No nodes available for placement."
            )

        # Security state is refreshed for every decision.
        nodes = self._refresh_security_state(nodes)

        # Add current scheduler-side placement load.
        nodes = self._apply_scheduler_load(nodes)

        eligible_nodes = self._filter_nodes(
            object_size,
            nodes,
        )

        if not eligible_nodes:
            raise RuntimeError(
                "No eligible node available for placement."
            )

        selected_node = self.policy.select(
            eligible_nodes
        )

        score = None

        if hasattr(self.policy, "score"):
            score = self.policy.score(
                selected_node,
                eligible_nodes,
            )

        self.load_balancer.record_placement(
            selected_node.node_id
        )

        return PlacementDecision(
            object_id=object_id,
            node_id=selected_node.node_id,
            score=score,
        )

    def select_node_from_state(
        self,
        object_id: str,
        object_size: int,
    ) -> PlacementDecision:

        if self.node_state_provider is None:
            raise RuntimeError(
                "NodeStateProvider is not configured."
            )

        nodes = self.node_state_provider.get_nodes()

        return self.select_node(
            object_id=object_id,
            object_size=object_size,
            nodes=nodes,
        )

    def _refresh_security_state(
        self,
        nodes: list[NodeStats],
    ) -> list[NodeStats]:

        if self.security_provider is None:
            return nodes

        return [
            replace(
                node,
                security_state=(
                    self.security_provider
                    .get_trust_state(node.node_id)
                ),
            )
            for node in nodes
        ]

    def _apply_scheduler_load(
        self,
        nodes: list[NodeStats],
    ) -> list[NodeStats]:

        node_ids = [
            node.node_id
            for node in nodes
        ]

        frequencies = (
            self.load_balancer
            .get_placement_frequencies(node_ids)
        )

        return [
            replace(
                node,
                access_frequency=frequencies[
                    node.node_id
                ],
            )
            for node in nodes
        ]

    @staticmethod
    def _filter_nodes(
        object_size: int,
        nodes: list[NodeStats],
    ) -> list[NodeStats]:

        return [
            node
            for node in nodes
            if (
                node.free_ram >= object_size
                and SecurityPolicy.is_allowed(node)
            )
        ]
