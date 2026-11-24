"""Order B1 connection closure before in-process revocation notifications."""
from __future__ import annotations

import asyncio
import inspect
import logging
from collections.abc import Callable
from typing import Any


class RevocationDispatcher:
    def __init__(self) -> None:
        self._force_close: Callable[[str], Any] | None = None
        self._listeners: list[Callable[[str, float], Any]] = []

    def set_force_close_connection(self, callback: Callable[[str], Any]) -> None:
        self._force_close = callback

    def register_revocation_listener(self, callback: Callable[[str, float], Any]) -> None:
        self._listeners.append(callback)

    def transition(self, node_id: str, timestamp: float) -> None:
        """Close the B1 connection, then notify listeners once in registration order."""
        if self._force_close is None:
            self._notify(node_id, timestamp)
            return
        try:
            loop = asyncio.get_running_loop()
        except RuntimeError:
            asyncio.run(self._dispatch(node_id, timestamp))
        else:
            loop.create_task(self._dispatch(node_id, timestamp))

    async def _dispatch(self, node_id: str, timestamp: float) -> None:
        try:
            result = self._force_close(node_id) if self._force_close is not None else None
            if inspect.isawaitable(result):
                await result
        except Exception:
            logging.getLogger(__name__).exception("could not close revoked node connection")
        self._notify(node_id, timestamp)

    def _notify(self, node_id: str, timestamp: float) -> None:
        for callback in tuple(self._listeners):
            try:
                result = callback(node_id, timestamp)
                if inspect.isawaitable(result):
                    try:
                        asyncio.get_running_loop().create_task(result)
                    except RuntimeError:
                        asyncio.run(result)
            except Exception:
                logging.getLogger(__name__).exception("revocation listener failed")
