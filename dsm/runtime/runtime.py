"""Identity-bound runtime implementing the frozen public DSM API."""

from typing import Callable, Protocol
from uuid import uuid4

from common.interfaces.NodeClient import NodeClient
from common.interfaces.dsm_api import DSMAPI
from common.types.context import RequestContext
from dsm.directory.directory import Directory
from dsm.object_manager.dsm_alloc import (
    AccessChecker,
    ObjectManager,
    ObjectManagerConfig,
    Scheduler,
)


class RevocationRegistrar(Protocol):
    """Security dependency exposing the callback registration contract."""

    def register_revocation_listener(
        self,
        callback: Callable[[str, float], list[str]],
    ) -> None:
        """Register a callback invoked with a revoked node id and timestamp."""


class RuntimeSecurity(AccessChecker, RevocationRegistrar, Protocol):
    """Security dependencies needed by the runtime."""


class Runtime:
    """Construct the Object Manager and bind identities to DSM handles."""

    def __init__(
        self,
        directory: Directory,
        node_client: NodeClient,
        scheduler: Scheduler,
        security: RuntimeSecurity,
        config: ObjectManagerConfig = ObjectManagerConfig(),
        node_timeout: float = 5.0,
    ) -> None:
        """Initialize runtime dependencies and subscribe to node revocations."""
        self.object_manager = ObjectManager(
            directory=directory,
            node_client=node_client,
            scheduler=scheduler,
            security=security,
            config=config,
            node_timeout=node_timeout,
        )
        self.security = security
        register_listener = getattr(security, "register_revocation_listener", None)
        if not callable(register_listener):
            raise TypeError(
                "security must provide register_revocation_listener(callback)"
            )
        register_listener(self.object_manager.handle_node_revoked)

    def bind(self, identity: str) -> "DSMHandle":
        """Return a client handle that cannot override its bound identity."""
        return DSMHandle(self.object_manager, identity)


class DSMHandle(DSMAPI):
    """Frozen DSMAPI implementation bound to a single client identity."""

    def __init__(self, object_manager: ObjectManager, identity: str) -> None:
        """Create a handle for one authenticated identity."""
        self._object_manager = object_manager
        self._identity = identity

    def _context(self) -> RequestContext:
        """Create a fresh request context while preserving the bound identity."""
        return RequestContext(
            identity=self._identity,
            request_id=str(uuid4()),
        )

    async def alloc(self, size: int) -> str:
        """Allocate an object for this handle's identity."""
        return await self._object_manager.alloc(
            self._context(),
            self._identity,
            size,
        )

    async def read(self, object_id: str) -> bytes:
        """Read an object as this handle's identity."""
        return await self._object_manager.read(
            self._context(),
            self._identity,
            object_id,
        )

    async def write(self, object_id: str, data: bytes) -> None:
        """Replace an object's bytes without exposing the internal version."""
        await self._object_manager.write(
            self._context(),
            self._identity,
            object_id,
            data,
        )

    async def free(self, object_id: str) -> None:
        """Free an object as this handle's identity."""
        await self._object_manager.free(
            self._context(),
            self._identity,
            object_id,
        )


__all__ = ["DSMAPI", "DSMHandle", "Runtime"]
