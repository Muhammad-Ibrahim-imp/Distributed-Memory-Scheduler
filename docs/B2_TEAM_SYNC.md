# B2 Team Sync

B2 decides where a DSM object should be placed. B2 does not measure machines, speak on the network, or change trust state.

Integration path:

```text
A1 ResourceStateProvider ──┐
B1 NetworkStateProvider  ──┼── NodeStatsAggregator ── NodeStats ── DefaultScheduler ── PlacementDecision ── A2
M5 SecurityStateProvider ──┘
```

`NodeStats` is B2's merged structure. Teammates integrate through the providers below, not by constructing `NodeStats` themselves.

M5's integration/bootstrap constructs the aggregator and the scheduler. B2 does not ship a `main.py`. M5 supplies `node_ids`. The same `SecurityStateProvider` instance is passed to both `NodeStatsAggregator` and `create_scheduler`.

## Ownership

| Member | Owns |
|---|---|
| A1 | Resource measurement: free RAM, CPU utilization, normalized health. Also total RAM, used RAM, and free RAM for the scaling experiment. |
| B1 | Network measurement: latency (ms) and bandwidth (Mbps). Also request latency, network throughput, and network overhead for the scaling experiment. |
| M5 | Authentication, authorization, trust management, revocation, quarantine, and the trust state machine. Supplies `node_ids` and builds the scheduler during integration. |
| B2 | Placement, security-aware filtering, load balancing, scheduler configuration, speedup/efficiency calculation, and the eight performance graphs. |
| A2 | Global object namespace, object metadata, object lifecycle, object location directory, consistency, versioning, cache, and object migration. |

B2 does not own psutil, sockets, TCP, TLS, authentication, authorization, the trust state machine, DSM object management, protobuf, or application adapters.

## A2 — DSM abstraction

When A2 allocates a DSM object, call the object returned by `create_scheduler`:

```python
decision = scheduler.select_node_from_state(
    object_id=object_id,
    object_size=object_size,
)
```

`select_node_from_state` is on `DefaultScheduler`. It is not on the `Scheduler` ABC.

B2 returns:

```python
@dataclass
class PlacementDecision:
    object_id: str
    node_id: str
    score: float | None = None
```

Example: `PlacementDecision(object_id="obj-123", node_id="node-2", score=0.82)`.

`score` is set for the `balanced` policy. The other policies leave it as `None`.

B2 expects:

| Input | Meaning |
|---|---|
| `object_id` | Unique DSM object identifier. Empty string raises `ValueError`. |
| `object_size` | Size in bytes. Negative size raises `ValueError`. Zero is accepted. |

A2 then uses `node_id` to manage the object's location. B2 only answers: given the current node states, where should this object be placed?

### Errors A2 can see

| Call | Error | When |
|---|---|---|
| `select_node_from_state` | `RuntimeError: NodeStateProvider is not configured.` | `create_scheduler` was called without `node_state_provider`. |
| `select_node_from_state` | The provider's exception | A1, B1, or M5 raises while state is being read. B2 does not skip that node. The decision fails. |
| `select_node` / `select_node_from_state` | `ValueError: object_id cannot be empty.` | Blank `object_id`. |
| `select_node` / `select_node_from_state` | `ValueError: object_size cannot be negative.` | `object_size < 0`. |
| `select_node` | `RuntimeError: No nodes available for placement.` | The node list is empty. |
| `select_node` / `select_node_from_state` | `RuntimeError: No eligible node available for placement.` | Every node is non-TRUSTED or has `free_ram < object_size`. |

`select_node(object_id, object_size, nodes)` is the lower-level call when the caller already has a `list[NodeStats]`. A2's normal path is `select_node_from_state`.

### A2 checklist

- Call `create_scheduler(...)` and then `select_node_from_state`.
- Treat `node_id` as the placement result. Do not re-score nodes in A2.
- Handle `ValueError` for bad inputs and `RuntimeError` when no eligible node exists.
- Let provider exceptions fail the allocation attempt. B2 will not return a second-choice node after a provider error.
- Keep object lifecycle, directory updates, consistency, cache, and migration in A2.

## A1 — Memory node

Expose:

```python
class ResourceStateProvider(ABC):
    @abstractmethod
    def get_resource_state(self, node_id: str) -> ResourceStats: ...
```

```python
@dataclass
class ResourceStats:
    free_ram: int       # bytes, >= 0
    cpu_usage: float    # 0–100 %, current/recent utilization
    health: float       # 0.0–1.0
```

Example: `ResourceStats(free_ram=8_000_000_000, cpu_usage=42.5, health=0.98)`.

B2 calls this on every scheduling-state refresh through `NodeStatsAggregator`. B2 does not call psutil.

`__post_init__` raises `ValueError` if `free_ram < 0`, `cpu_usage` is outside 0–100, or `health` is outside 0.0–1.0.

A TRUSTED node is eligible when `free_ram >= object_size`. Health is a scoring input for the `balanced` policy. Health `0.0` does not by itself exclude a node.

For the scaling experiment, also record total RAM, used RAM, free RAM, and CPU utilization. There is no extra B2 interface for total/used RAM. Convert them to a 0–100 `memory_utilization` and pass that on `ScalingResult` when the experiment is assembled. B2 does not compute utilization from psutil.

### A1 checklist

- Return fresh `free_ram`, `cpu_usage`, and normalized `health` for each `node_id` M5 registered.
- Use bytes for `free_ram` and percent for `cpu_usage`.
- Raise if the node cannot be read. B2 will fail that scheduling decision.
- Keep allocation, isolation, quotas, secure deletion, and integrity checks in A1.

## B1 — Network

Expose:

```python
class NetworkStateProvider(ABC):
    @abstractmethod
    def get_network_state(self, node_id: str) -> NetworkStats: ...
```

```python
@dataclass
class NetworkStats:
    latency: float      # milliseconds, >= 0
    bandwidth: float    # Mbps, >= 0
```

Example: `NetworkStats(latency=12.4, bandwidth=940.0)`.

Values should be current or recent measurements. B2 uses them only for scoring. B2 does not implement sockets, TCP, TLS, or network measurement.

`__post_init__` raises `ValueError` if `latency` or `bandwidth` is negative.

B2 consumes these through `NodeStats.latency` (ms) and `NodeStats.bandwidth` (Mbps). Use those units in integration.

For the scaling experiment, separately provide:

| Measurement | `ScalingResult` field | Unit |
|---|---|---|
| Request latency | `network_latency` | milliseconds |
| Network throughput | `network_throughput` | Mbps |
| Network overhead | `network_overhead` | not fixed yet; optional, default `0.0` |

`network_overhead` is stored and checked to be `>= 0`. It is not one of the eight graphs. Please tell B2 the unit before the written report treats the number as a time or a byte count.

### B1 checklist

- Return latency in milliseconds and bandwidth in Mbps.
- Keep protobuf, TLS, and connection handling in B1. B2's contract is the Python dataclass.
- Raise if a node has no measurement. B2 will fail that scheduling decision.

## M5 — Security and integration

Expose:

```python
class SecurityStateProvider(ABC):
    @abstractmethod
    def get_trust_state(self, node_id: str) -> SecurityState: ...
```

```python
class SecurityState(Enum):
    TRUSTED = "trusted"
    SUSPICIOUS = "suspicious"
    QUARANTINED = "quarantined"
    REVOKED = "revoked"
```

Return the `SecurityState` enum, not the raw string `"TRUSTED"`. `NodeStats` raises `TypeError` if `security_state` is not a `SecurityState`.

`node_id` is whatever identifier this method expects. M5/integration supplies the same ids to `NodeStatsAggregator`.

Placement rule:

| State | New placement |
|---|---|
| `TRUSTED` | Eligible when `free_ram >= object_size` |
| `SUSPICIOUS` | Hard exclusion |
| `QUARANTINED` | Hard exclusion |
| `REVOKED` | Hard exclusion |

B2 only consumes the trust state. M5 owns authentication, authorization, trust management, revocation, and quarantine. B2 asks `get_trust_state` again on every decision, in addition to the read inside `NodeStatsAggregator`. Do not ask B2 to cache trust, and do not ask B2 to implement authentication.

If `get_trust_state` raises, the scheduling decision fails. B2 does not skip the node.

### How to build the scheduler

```python
from scheduler.config import load_scheduler_config
from scheduler.factory import create_scheduler
from scheduler.node_state import NodeStatsAggregator

aggregator = NodeStatsAggregator(
    node_ids,            # supplied by M5/integration
    resource_provider,   # A1
    network_provider,    # B1
    security_provider,   # M5
)

config = load_scheduler_config("configs/scheduler.json")
scheduler = create_scheduler(
    config,
    node_state_provider=aggregator,
    security_provider=security_provider,  # same instance as above
)
```

`configs/scheduler_config.yaml` is not read by this loader. Use `configs/scheduler.json`.

Policy names: `ram`, `cpu`, `latency`, `bandwidth`, `balanced`. An unknown name raises `ValueError`.

Default JSON:

```json
{
  "policy": "balanced",
  "ram_weight": 0.30,
  "cpu_weight": 0.20,
  "latency_weight": 0.15,
  "bandwidth_weight": 0.15,
  "health_weight": 0.10,
  "access_frequency_weight": 0.10
}
```

Weights must be `>= 0`, and at least one must be `> 0`.

### M5 checklist

- Pass the same security provider into the aggregator and into `create_scheduler`.
- Pass the live `node_ids` list into `NodeStatsAggregator`.
- Return `SecurityState` from `get_trust_state`.
- Keep the trust state machine in M5. B2 will not transition states.
- There is no node-address file in B2's config. Addressing stays with M5/B1 configuration.

## Shared validation

These checks run in `__post_init__` and raise immediately.

| Type | Rule | Error |
|---|---|---|
| `ResourceStats.free_ram` | `>= 0` bytes | `ValueError` |
| `ResourceStats.cpu_usage` | `0.0`–`100.0` | `ValueError` |
| `ResourceStats.health` | `0.0`–`1.0` | `ValueError` |
| `NetworkStats.latency` | `>= 0` ms | `ValueError` |
| `NetworkStats.bandwidth` | `>= 0` Mbps | `ValueError` |
| `NodeStats.node_id` | non-empty | `ValueError` |
| `NodeStats.free_ram` | `>= 0` bytes | `ValueError` |
| `NodeStats.cpu_usage` | `0.0`–`100.0` | `ValueError` |
| `NodeStats.latency` | `>= 0` ms | `ValueError` |
| `NodeStats.bandwidth` | `>= 0` Mbps | `ValueError` |
| `NodeStats.health` | `0.0`–`1.0` | `ValueError` |
| `NodeStats.access_frequency` | `0.0`–`1.0` | `ValueError` |
| `NodeStats.security_state` | instance of `SecurityState` | `TypeError` |
| `SchedulerConfig` weights | each `>= 0`, sum `> 0` | `ValueError` |

`NodeStats.access_frequency` in B2 means the scheduler's own placement frequency, not application access frequency. `LoadBalancer` computes it as this node's placement count divided by the total placements recorded for the nodes in the current decision. Callers should leave it at the default `0.0`. `DefaultScheduler` overwrites it before scoring.

## Benchmark inputs and graphs

`ScalingResult` fields and who fills them:

| Field | Who supplies it | Unit / meaning |
|---|---|---|
| `node_count` | Experiment run | Integer `> 0` |
| `execution_time` | Experiment run | Seconds, `> 0`. Used as `TN`. |
| `throughput` | Experiment run | Objects/sec, `>= 0` |
| `cpu_utilization` | A1 | `0`–`100` % |
| `memory_utilization` | A1, from total/used/free RAM | `0`–`100` % |
| `network_latency` | B1 | Milliseconds, `>= 0` |
| `network_throughput` | B1 | Mbps, `>= 0` |
| `network_overhead` | B1 | Optional float, default `0.0`, must be `>= 0`. Unit not fixed. |
| `workload_distribution` | B2 `LoadBalancer.get_placement_count` | `dict[str, int]` placement counts |
| `scheduler_overhead` | B2 | `>= 0`, default `0.0` |
| `speedup` | `calculate_scaling` | `T1 / TN`, where T1 is the row with the smallest `node_count` |
| `efficiency` | `calculate_scaling` | `speedup / node_count` |

Eight graphs from `scheduler.performance.plot_scaling`:

1. Nodes vs Execution Time
2. Nodes vs Speedup
3. Nodes vs Efficiency
4. Nodes vs Throughput (objects/sec)
5. Memory Utilization
6. CPU Utilization
7. Network Latency (ms)
8. Network Throughput (Mbps)

`workload_distribution`, `scheduler_overhead`, and `network_overhead` are stored on `ScalingResult` and are not separate graphs.

## Usage example

```python
decision = scheduler.select_node_from_state(
    object_id="obj-123",
    object_size=8_000_000,
)

# decision.node_id is the node A2 should store the object on.
```

Wiring is in the M5 section above. Policy `ram` chooses the TRUSTED node with the most free RAM that can hold `object_size`. `cpu` chooses the lowest CPU. `latency` chooses the lowest latency. `bandwidth` chooses the highest bandwidth. `balanced` chooses the highest weighted score.

## Modifications to shared contracts

These are intentional and already in the branch:

- `ScalingResult.network_overhead: float = 0.0` was added. Existing construction still works if the argument is omitted. Negative values raise `ValueError`.
- The throughput graph label says `Throughput (objects/sec)`.
- `requirements.txt` contains `matplotlib>=3.8`.

No class, method, or file from the B2 contract was renamed. `select_node_from_state` was not added to the `Scheduler` ABC.
