# Purpose:
# Enforces B2's security-aware placement rule.
# Only TRUSTED nodes may receive new placements.

from common.types.node import NodeStats
from common.types.security import TrustState


class SecurityPolicy:

    @staticmethod
    def is_allowed(
        node: NodeStats,
    ) -> bool:

        return node.security_state is TrustState.TRUSTED
