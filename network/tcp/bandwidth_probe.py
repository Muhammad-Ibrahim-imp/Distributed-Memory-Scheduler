import asyncio
import time

from common.protocol import dsm_pb2
from network.tcp.registry import NodeRegistry

PROBE_SIZE = 256 * 1024
PROBE_INTERVAL = 30.0


class BandwidthProbe:
    def __init__(self, registry: NodeRegistry, probe_size: int = PROBE_SIZE,
                 interval: float = PROBE_INTERVAL):
        self.registry = registry
        self.probe_size = probe_size
        self.interval = interval
        self._stop = asyncio.Event()

    async def run(self) -> None:
        while not self._stop.is_set():
            for node_id in self.registry.list_ids():
                try:
                    await self._probe_one(node_id)
                except Exception:
                    pass
            try:
                await asyncio.wait_for(self._stop.wait(), timeout=self.interval)
            except asyncio.TimeoutError:
                pass

    async def _probe_one(self, node_id: str) -> None:
        entry = self.registry.get(node_id)
        if entry is None:
            return
        env = dsm_pb2.Envelope()
        env.protocol_version = 1
        env.identity = "probe"
        env.timestamp = time.time()
        env.write.request_id = f"probe-{time.time_ns()}"
        env.write.object_id = f"probe-{node_id}"
        env.write.owner_identity = "probe"
        env.write.data = b"\x00" * self.probe_size
        t0 = time.perf_counter()
        try:
            await entry.connection.call(env, timeout=5.0)
        except Exception:
            return
        elapsed = time.perf_counter() - t0
        if elapsed <= 0:
            return
        mbps = (self.probe_size * 8) / elapsed / 1e6
        self.registry.set_bandwidth(node_id, mbps)

    def stop(self) -> None:
        self._stop.set()