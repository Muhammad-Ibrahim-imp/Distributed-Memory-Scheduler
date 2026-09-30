# Purpose:
# Defines M5 -> B2 trust-state integration.
# M5 owns the trust state machine; B2 only consumes it.

from abc import ABC, abstractmethod

from common.types.security import TrustState


class SecurityStateProvider(ABC):

    @abstractmethod
    def get_trust_state(
        self,
        node_id: str,
    ) -> TrustState:
        """Return the current trust state of a node."""
        raise NotImplementedError
