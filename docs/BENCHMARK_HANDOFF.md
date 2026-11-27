# Benchmark handoff

## What the harness measures

`scripts/benchmark_remote.py` uses a real authenticated `RemoteDSMAPI` over TLS. It measures remote read/write latency and throughput over bounded object sizes, then verifies checksum, image inversion/grayscale/blur, and non-divisible matrix multiplication. Application workloads run `--application-repeats` times per mode (default 3); output rows contain median, minimum, maximum, and sample count. It writes JSON or CSV, can attach node-log placement evidence and an operator-supplied per-node statistics JSON snapshot, and records the configured scheduler-policy label and audit-log byte growth.

These measurements are end-to-end local or LAN observations, not isolated cryptographic overhead. TLS, token validation, replay checks, rate limiting, and audit are always enabled. By default the harness measures client-side adapters. Pass `--distributed-compute` to submit image/matrix tasks to registered node workers; output rows include worker node IDs, PIDs, execution time, task counts, and `execution_mode`. Compare locality and round-robin in separate coordinator runs: set `DSM_COMPUTE_SCHEDULER=locality` or `DSM_COMPUTE_SCHEDULER=round_robin` on the coordinator before startup. The client `--scheduler-policy` value only labels the output row. The existing `scripts/benchmark_adapters.py` remains a mock-backed adapter-only model and must be labeled as such.

The harness records actual framed-byte counters on its client/coordinator connection and per-task DSM payload byte counts from workers. Use repeatable `--sample-process ROLE=PID` arguments to sample coordinator/worker RSS; the benchmark client is sampled automatically. With the coordinator and node PIDs known, for example:

```powershell
python scripts\benchmark_remote.py --host coordinator --server-name coordinator --distributed-compute --sample-process coordinator=1234 --sample-process node-1=2345 --sample-process node-2=3456 --application-repeats 3 --output results\measured.json
```

These counters do not yet capture framed bytes on worker-to-coordinator connections, and sampled RSS is not an exact peak.

## Exact commands

With local coordinator and node processes already running and secrets/certs configured:

```powershell
$env:DSM_CLIENT_ENROLLMENT_SECRET = '<retrieve securely into this process environment>'
python scripts\benchmark_remote.py --host 127.0.0.1 --port 8443 --sizes 4096 65536 --max-bytes 1048576 --repeats 3 --image-size 64 --tile-size 16 --block-size 3 --scheduler-policy locality --format json --output benchmarks\results\local-smoke.json
python scripts\benchmark_remote.py --host 127.0.0.1 --port 8443 --sizes 4096 65536 --max-bytes 1048576 --repeats 3 --format csv --output benchmarks\results\local-smoke.csv
python scripts\benchmark_remote.py --host 127.0.0.1 --port 8443 --sizes 4096 --max-bytes 1048576 --repeats 1 --image-size 32 --tile-size 16 --block-size 4 --distributed-compute --scheduler-policy locality --output benchmarks\results\distributed-locality.json
```

To compare placement policies, restart the coordinator once per run with `DSM_COMPUTE_SCHEDULER=locality` and `DSM_COMPUTE_SCHEDULER=round_robin`, then run the same seed and workload. The client option labels the exported rows; the policy is selected by the coordinator environment. Locality is the default.

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

No performance benchmark numbers are claimed here. The harness's correctness smoke is exercised by the full-stack test, but no repeated timing comparison or speedup/efficiency result has been measured. Physical-machine latency, bandwidth, capacity efficiency, worker-count scaling, and security overhead require coordinated physical LAN runs. Per-node `--node-stats-json` is an input snapshot; B2/A1 still need to provide an authoritative export path. Do not call a workload distributed computation unless computation itself executes across nodes.
