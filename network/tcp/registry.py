import time
from dataclasses import dataclass

from common.protocol import dsm_pb2
from network.tcp.connection import Connection


@dataclass
class NodeEntry:
    node_id: str
    connection: Connection
    last_seen: float
    last_stats: dsm_pb2.NodeStatsReport | None = None
    rtt_ms: float = 0.0


class NodeRegistry:
    def __init__(self):
        self._nodes: dict[str, NodeEntry] = {}
        self._ever_seen: set[str] = set()
        self._listeners_down = []
        self._listeners_up = []

    def register(self, node_id: str, connection: Connection) -> None:
        was_known = node_id in self._ever_seen
        self._nodes[node_id] = NodeEntry(node_id=node_id, connection=connection, last_seen=time.time())
        if was_known:
            for cb in self._listeners_up:
                cb(node_id)
        self._ever_seen.add(node_id)

    def unregister(self, node_id: str) -> None:
        if self._nodes.pop(node_id, None) is not None:
            for cb in self._listeners_down:
                cb(node_id)

    def get(self, node_id: str) -> NodeEntry | None:
        return self._nodes.get(node_id)

    def touch(self, node_id: str, rtt_ms: float = 0.0) -> None:
        entry = self._nodes.get(node_id)
        if entry is not None:
            entry.last_seen = time.time()
            if rtt_ms:
                entry.rtt_ms = rtt_ms

    def set_stats(self, node_id: str, stats: dsm_pb2.NodeStatsReport) -> None:
        entry = self._nodes.get(node_id)
        if entry is not None:
            entry.last_stats = stats
            entry.last_seen = time.time()

    def list_ids(self) -> list[str]:
        return list(self._nodes.keys())

    def on_node_down(self, callback) -> None:
        self._listeners_down.append(callback)

    def on_node_up(self, callback) -> None:
        self._listeners_up.append(callback)


