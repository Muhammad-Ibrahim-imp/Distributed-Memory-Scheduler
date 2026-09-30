# B2 Implementation Report

## Summary

B2's scheduler is implemented on branch `B2/Scheduler`: placement policies, security filtering, load tracking, node-state aggregation, scaling math, and the eight performance graphs. Shared contracts live under `common/`. The scheduler does not measure resources, open sockets, or own trust transitions.

Approved differences from the original spec:

- `ScalingResult.network_overhead` is an optional float, default `0.0`. A negative value raises `ValueError`, same rule as `scheduler_overhead`.
- The throughput graph y-axis is `Throughput (objects/sec)`.
- `requirements.txt` requires `matplotlib>=3.8`.
- Empty `common/__init__.py`, `common/interfaces/__init__.py`, and `scheduler/__init__.py` were added so `common` and `scheduler` import as packages.

`configs/scheduler_config.yaml` was left as it was. B2 reads `configs/scheduler.json` only. No `main.py`, protobuf, psutil, NumPy, or asyncio code was added. The empty untracked `tests/unit/test_scheduler.py` was deleted and not replaced, so that deletion has no commit.

## Files

| Path | Purpose | How it is implemented |
|---|---|---|
| `common/__init__.py` | Makes `common` a package. | Empty file. |
| `common/interfaces/__init__.py` | Makes `common.interfaces` a package. | Empty file. |
| `scheduler/__init__.py` | Makes `scheduler` a package. | Empty file. |
| `common/types/security.py` | Trust states B2 consumes. | `SecurityState` enum: `TRUSTED`, `SUSPICIOUS`, `QUARANTINED`, `REVOKED`, with lowercase string values. |
| `common/types/resource.py` | A1 resource contract. | `ResourceStats` (`free_ram` bytes, `cpu_usage` 0–100, `health` 0.0–1.0). `__post_init__` raises `ValueError` outside those ranges. |
| `common/types/network.py` | B1 network contract. | `NetworkStats` (`latency` ms, `bandwidth` Mbps). Negative values raise `ValueError`. |
| `common/types/node.py` | Merged node state used by the scheduler. | `NodeStats` plus `access_frequency` (0.0–1.0), which B2 fills with its own placement frequency. `security_state` must be a `SecurityState` or construction raises `TypeError`. |
| `common/types/placement.py` | Decision returned to A2. | `PlacementDecision(object_id, node_id, score=None)`. |
| `common/types/scheduler.py` | Policy name and scoring weights. | `SchedulerConfig`, default policy `balanced`. Negative weights, or a weight sum of zero or less, raise `ValueError`. |
| `common/interfaces/scheduler.py` | API A2 depends on for an explicit node list. | Abstract `Scheduler.select_node`. `select_node_from_state` is not on this ABC. |
| `common/interfaces/node_state.py` | How the scheduler pulls current nodes. | Abstract `NodeStateProvider.get_nodes`. |
| `common/interfaces/resource_state.py` | A1 provider. | Abstract `ResourceStateProvider.get_resource_state(node_id)`. |
| `common/interfaces/network_state.py` | B1 provider. | Abstract `NetworkStateProvider.get_network_state(node_id)`. |
| `common/interfaces/security_state.py` | M5 provider. | Abstract `SecurityStateProvider.get_trust_state(node_id) -> SecurityState`. |
| `scheduler/policies/base.py` | Policy interface. | Abstract `PlacementPolicy.select`. |
| `scheduler/policies/ram.py` | Free-RAM placement. | `RAMAwarePolicy` picks the maximum `free_ram`. |
| `scheduler/policies/cpu.py` | CPU placement. | `CPUAwarePolicy` picks the minimum `cpu_usage`. |
| `scheduler/policies/latency.py` | Latency placement. | `LatencyAwarePolicy` picks the minimum `latency`. |
| `scheduler/policies/bandwidth.py` | Bandwidth placement. | `BandwidthAwarePolicy` picks the maximum `bandwidth`. |
| `scheduler/policies/security.py` | Security filter. | `SecurityPolicy.is_allowed` is true only when `security_state is SecurityState.TRUSTED`. |
| `scheduler/scoring/node_scorer.py` | Balanced score. | Min-max normalization. Higher is better for RAM and bandwidth. Lower is better for CPU, latency, and placement frequency. Health is used as the raw 0.0–1.0 value. The weighted sum is divided by the total weight. |
| `scheduler/policies/balanced.py` | Weighted placement. | `BalancedPolicy` selects the highest `NodeScorer` score and exposes `score`. |
| `scheduler/load_balancing/load_balancer.py` | Placement counts. | `LoadBalancer` counts assignments per node. Frequencies are count divided by the total for the nodes in the current decision. This is not application access frequency. |
| `scheduler/node_state.py` | Merges A1, B1, and M5. | `NodeStatsAggregator.get_nodes` calls each provider for every id in the list M5/integration supplies. A provider exception is not caught. |
| `scheduler/scheduler.py` | Placement entry point. | `DefaultScheduler` refreshes trust from `security_provider` on every `select_node`, writes placement frequency onto the nodes, keeps nodes with `free_ram >= object_size` and `TRUSTED`, then runs the policy. `select_node_from_state` loads nodes from `NodeStateProvider` first. |
| `scheduler/factory.py` | Builds a scheduler. | `create_scheduler` accepts `ram`, `cpu`, `latency`, `bandwidth`, and `balanced`. It receives `node_state_provider` and `security_provider`. It does not build the aggregator. |
| `scheduler/config.py` | Loads JSON config. | `load_scheduler_config` passes the JSON object into `SchedulerConfig`. |
| `scheduler/benchmark.py` | Scaling results. | `ScalingResult` stores the experiment inputs. `calculate_scaling` sets `speedup = T1 / TN` and `efficiency = speedup / N` using the smallest `node_count` as T1. `network_overhead` defaults to `0.0`. |
| `scheduler/performance.py` | Eight graphs. | Matplotlib line plots. This module does not measure CPU, memory, or the network. |
| `configs/scheduler.json` | Default weights. | Policy `balanced` with weights 0.30, 0.20, 0.15, 0.15, 0.10, 0.10. |
| `requirements.txt` | Graph dependency. | `matplotlib>=3.8`. |

## Commit history

```text
9328e8f feat(common): add empty common package init
248d6a4 feat(common): add empty interfaces package init
c2edf2a feat(scheduler): add empty scheduler package init
e5fd456 feat(common): add SecurityState
59bf25e feat(common): add ResourceStats
fa8f2aa feat(common): add NetworkStats
799cea8 feat(common): add NodeStats
e1696c3 feat(common): add PlacementDecision
6278036 feat(common): add SchedulerConfig
cefc8e7 feat(common): define Scheduler interface
715cab6 feat(common): add NodeStateProvider
df5ce81 feat(common): add ResourceStateProvider
3705f43 feat(common): add NetworkStateProvider
68156be feat(common): add SecurityStateProvider
8e99aa8 feat(scheduler): add PlacementPolicy
dc1dbbd feat(scheduler): add RAMAwarePolicy
51a2980 feat(scheduler): add CPUAwarePolicy
bfec06a feat(scheduler): add LatencyAwarePolicy
03b1f43 feat(scheduler): add BandwidthAwarePolicy
0e28611 feat(scheduler): add SecurityPolicy
b7026bf feat(scheduler): add NodeScorer
815281d feat(scheduler): add BalancedPolicy
a6ebdef feat(scheduler): add LoadBalancer
10aa84f feat(scheduler): add NodeStatsAggregator
57a6e9a feat(scheduler): add DefaultScheduler
052d6be feat(scheduler): add create_scheduler
cecd615 feat(scheduler): add scheduler config loader
7cc298b feat(scheduler): add ScalingResult
429546f feat(scheduler): add performance graphs
84f4ea6 feat(scheduler): add scheduler.json
276a251 chore(deps): add matplotlib>=3.8
```

Commits for this report and `docs/B2_TEAM_SYNC.md` follow the implementation commits above.

## Sanity check

Python 3.14.0 in `.venv`. `compileall` succeeded. Matplotlib 3.11.2 was installed in `.venv` for the check. NumPy was installed only because Matplotlib depends on it. B2 code does not import NumPy, and `requirements.txt` does not list it.

All 26 B2 modules imported. Fake A1, B1, and M5 providers were used. Results:

| Check | Result |
|---|---|
| Only a TRUSTED node is chosen, even when a SUSPICIOUS node has more free RAM (RAM policy) | Pass. Selected `trusted-big`. |
| `free_ram < object_size` leaves no eligible node | Pass. `RuntimeError: No eligible node available for placement.` |
| Trust is read again on every decision | Pass. Each decision called `get_trust_state` twice per node (once in the aggregator, once in `DefaultScheduler`). After `trusted-big` was changed to `REVOKED`, the next decision selected `trusted-small`. |
| `select_node_from_state` returns `PlacementDecision` | Pass. `PlacementDecision(object_id='obj-123', node_id='trusted-big', score=None)` for the RAM policy. Balanced policy returned a decision with `score=0.99`. |
| A provider exception fails the decision | Pass. `RuntimeError: trust unavailable` propagated. |
| `configs/scheduler.json` loads policy `balanced` | Pass. |
| Speedup is `T1 / TN` | Pass. 1 node → 1.0, 2 nodes → 1.666... |
| Eight PNG graphs from sample `ScalingResult` rows | Pass. Files were written under a temp directory and were not committed. |

Graph files: `nodes_vs_execution_time.png`, `nodes_vs_speedup.png`, `nodes_vs_efficiency.png`, `nodes_vs_throughput.png`, `memory_utilization.png`, `cpu_utilization.png`, `network_latency.png`, `network_throughput.png`.

## Suggested modifications

- `__pycache__/` is not in `.gitignore`. Bytecode from the sanity check was deleted and not committed. Adding `__pycache__/` to `.gitignore` would keep it out of future commits.
- `configs/scheduler_config.yaml` is still in the repo and does not match `SchedulerConfig`. `load_scheduler_config` will not read it. Leaving it avoids a silent delete. A later cleanup can remove or replace it once the team agrees.
- `network_overhead` has no unit in the field. B1 should name the unit before the scaling report treats the number as seconds, milliseconds, or something else.
- A1 total RAM and used RAM still have no provider method. The experiment runner is expected to turn those readings into `ScalingResult.memory_utilization` before calling B2. No new interface was added.

## Step 1 answers

| Topic | Answer used |
|---|---|
| Integration path | Providers → `NodeStatsAggregator` → `NodeStats` → scheduler. |
| Who builds the scheduler | M5 integration/bootstrap. B2 did not add `main.py`. |
| `node_ids` | Supplied by M5/integration to `NodeStatsAggregator`. |
| Security provider | The same instance is passed to the aggregator and to `create_scheduler`. |
| Provider failures | Fail the scheduling decision. Exceptions are not caught. |
| SUSPICIOUS, QUARANTINED, REVOKED | Hard exclusion. |
| TRUSTED | Eligible when `free_ram >= object_size`. |
| `network_overhead` | Optional float field, default `0.0`, plus the same non-negative check as the other metrics. |
| Latency | Milliseconds. |
| Network throughput | Mbps. |
| Scheduler throughput | Objects/sec. |
| `workload_distribution` | Placement counts. |
| `access_frequency` | B2 placement frequency, not application access frequency. |
| `configs/scheduler_config.yaml` | Left untouched. |
| `configs/scheduler.json` | Added. |
| `tests/unit/test_scheduler.py` | Deleted, not replaced. |
| `__init__.py` | Exactly three empty files. |
| Matplotlib | `matplotlib>=3.8` in `requirements.txt`. |
| psutil, NumPy, asyncio, protobuf, node-address config | Not added. |
| `Scheduler` ABC | Does not declare `select_node_from_state`. |
| Git | Branch `B2/Scheduler`, conventional commits, push this branch only. |
