# Benchmark handoff

## What the harness measures

`scripts/benchmark_remote.py` uses a real authenticated `RemoteDSMAPI` over TLS. It measures remote read/write latency and throughput over bounded object sizes, then verifies checksum, image inversion/grayscale/blur, and non-divisible matrix multiplication. It writes JSON or CSV, can attach node-log placement evidence and an operator-supplied per-node statistics JSON snapshot, and records the configured scheduler-policy label and audit-log byte growth.

These measurements are end-to-end local or LAN observations, not isolated cryptographic overhead. TLS, token validation, replay checks, rate limiting, and audit are always enabled; the harness does not turn security off to create a misleading baseline. Image and matrix computation runs in the client adapters; DSM distributes storage and does not by itself distribute computation. The existing `scripts/benchmark_adapters.py` is a mock-backed adapter-only model and must be labeled as such.

## Exact commands

With local coordinator and node processes already running and secrets/certs configured:

```powershell
$env:DSM_CLIENT_ENROLLMENT_SECRET = '<retrieve securely into this process environment>'
python scripts\benchmark_remote.py --host 127.0.0.1 --port 8443 --sizes 4096 65536 --max-bytes 1048576 --repeats 3 --image-size 64 --tile-size 16 --block-size 3 --scheduler-policy configured-on-coordinator --format json --output benchmarks\results\local-smoke.json
python scripts\benchmark_remote.py --host 127.0.0.1 --port 8443 --sizes 4096 65536 --max-bytes 1048576 --repeats 3 --format csv --output benchmarks\results\local-smoke.csv
```

The second run can attach node-side evidence and statistics exported from the deployment monitoring process:

```powershell
python scripts\benchmark_remote.py --host coordinator --port 8443 --sizes 4096 65536 262144 --max-bytes 1048576 --repeats 10 --scheduler-policy round_robin --node-log C:\DSM\logs\node-1.log --node-log C:\DSM\logs\node-2.log --node-stats-json C:\DSM\results\node-stats.json --audit-jsonl C:\DSM\data\audit.jsonl --format json --output C:\DSM\results\lan-round-robin.json
```

Capacity scaling is controlled by `--sizes` and bounded by `--max-bytes` (one DSM object at a time). Change the coordinator scheduler configuration between runs to measure different policies; `--scheduler-policy` labels the selected configuration and does not change it. Use identical machine, node capacities, security config, and workload seed when comparing policies. For the existing mock-only model:

```powershell
python scripts\benchmark_adapters.py --sizes 8 16 --image-size 33 --matrix-size 13 --repeats 1 --warmups 0 --output benchmarks\results\adapter-model.csv
```

## Workload ownership

| Member | Deliverable / measurement responsibility |
|---|---|
| A1 | Confirm bounded allocations against node capacity; expose generation-aware allocated/free byte and object counts for trustworthy per-node utilization snapshots. |
| B1 | Review reconnect and request timeout semantics; validate TLS transport latency, throughput, and response deadlines on physical LAN. |
| B2 | Run the same workload against each scheduler config, validate eligibility and placement evidence, and provide generation-fresh per-node resource snapshots. |
| M5 | Run audit-enabled end-to-end workloads; interpret audit byte/event growth and authenticated latency without disabling policy. Maintain result schema and application checks. |

## Current evidence limits

No benchmark numbers are claimed here. The smoke command must be executed and its produced artifact reviewed before reporting measurements. Physical-machine latency, bandwidth, capacity efficiency, scheduler comparison, and security overhead require coordinated physical LAN runs. Per-node `--node-stats-json` is an input snapshot; B2/A1 still need to provide an authoritative export path. Do not call a workload distributed computation unless computation itself executes across nodes.
