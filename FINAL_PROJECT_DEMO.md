# Final DSM demonstration

## What this benchmark measures

`scripts/benchmark_final.py` uses deterministic float32 `.npy` inputs. `native` runs NumPy `A @ B`; `local_tiled` performs blocked NumPy operations directly on local memory maps and makes no DSM calls; `distributed_compute` uploads through the existing `MatrixMultiplicationAdapter` and dispatches its block tasks through `NetworkComputeAPI`. Distributed input preparation, upload, result collection, and cleanup are included in end-to-end time. Compute time is reported separately. Each repetition is checked at sampled output cells using an independent float64 dot product; this is sampled verification, not full validation.

The old `scripts/benchmark_matrix_memmap.py` `local_tiled` label was not a true local baseline: it constructed the adapter over `RemoteDSMAPI`. Use the new script for the local comparison. Its file-backed local mode bounds resident input/output matrices, although the operating system page cache and BLAS workspace still consume memory. The storage preflight estimates three float32 matrices and rejects configurations above `--max-storage-gib`; native mode additionally caps that estimate at 70% of currently available RAM.

## Laptop baselines

Set BLAS limits before Python starts (Windows PowerShell):

```powershell
$env:OPENBLAS_NUM_THREADS = '1'
$env:OMP_NUM_THREADS = '1'
$env:MKL_NUM_THREADS = '1'
python scripts\benchmark_final.py --mode native --dimensions 512 1024 --tile 256 --repeats 3 --output results\native.json
python scripts\benchmark_final.py --mode local_tiled --dimensions 512 1024 2048 --tile 256 --repeats 3 --output results\local-tiled.json
```

These produce JSON and sibling CSV. Increase dimensions only after reviewing available RAM, free disk, expected storage (three matrices × 4 bytes × N² for local output), and the O(N³) arithmetic cost. The benchmark defaults avoid launching the 4096–16384 workloads blindly.

## Authenticated LAN execution

Use the certificate issuance and provisioning steps in [docs/PHYSICAL_LAN_INTEGRATION.md](docs/PHYSICAL_LAN_INTEGRATION.md). In short, create the CA/server/client and unique node certificates with `python scripts\gen_certs.py` and `python scripts\gen_certs.py --issue-node node-2`; distribute only the runtime certificates and private keys to their matching hosts, and keep `ca.key` offline. Configure `DSM_CERT_DIR`, coordinator host/port, enrollment secrets, and node identity through environment variables; source changes are not needed to change addresses.

Laptop 1 runs `python scripts\coordinator_main.py` and `python scripts\node_main.py` in separate shells with its own node certificate and `NODE_ID=node-1`. Laptop 2+ runs `python scripts\node_main.py` with a unique identity and certificate. The coordinator listens on TLS port 8443 by default. From the client laptop, check registration with the node/cluster status command documented in that guide, then run:

```powershell
$env:DSM_COORDINATOR_HOST = 'coordinator'
$env:DSM_COORDINATOR_PORT = '8443'
$env:DSM_CLIENT_ENROLLMENT_SECRET = '<retrieve securely>'
python scripts\benchmark_final.py --mode direct --dimensions 512 1024 --tile 256 --repeats 3 --output results\lan-matrix.json
```

Distributed modes require registered worker handlers and authenticated client access. Run `--mode mediated` for coordinator-mediated input access and `--mode direct` for direct authorized local reads.

### Laptop counts and copy-paste run commands

Use the setup commands above and `docs/PHYSICAL_LAN_INTEGRATION.md` to provision certificates. On every machine, install the same checkout and dependencies. Each node must have its own `DSM_NODE_CERT_FILE`, `DSM_NODE_KEY_FILE`, and unique `NODE_ID`; set `DSM_WORKER_TILE_CACHE_BYTES=0` to disable the new bounded immutable-tile cache (default 64 MiB). Worker handlers and compute slots must be enabled as described in the deployment guide.

**One laptop (client + coordinator + one worker):** in separate PowerShell windows, run the coordinator command from section 5 of the LAN guide and then on the same laptop run:

```powershell
$env:SECURITY_HOST='127.0.0.1'; $env:SECURITY_PORT='8443'; $env:DSM_CERT_DIR="$PWD\certs"; $env:DSM_NODE_CERT_FILE="$PWD\certs\nodes\node-1.pem"; $env:DSM_NODE_KEY_FILE="$PWD\certs\nodes\node-1.key"; $env:NODE_ID='node-1'; $env:DSM_NODE_ENROLLMENT_SECRET='<node secret>'; $env:DSM_WORKER_TILE_CACHE_BYTES='67108864'; python scripts\node_main.py
```

In a client shell with the client certificate and enrollment secret configured:

```powershell
python scripts\benchmark_final.py --mode direct --dimensions 512 1024 --tile 256 --repeats 3 --output results\one-laptop.json
```

**Two laptops (coordinator/client/worker on laptop 1, worker on laptop 2):** start the second worker using the coordinator hostname/IP and node-2 certificate, then execute on the client laptop:

```powershell
python scripts\benchmark_final.py --mode direct --dimensions 512 1024 2048 --tile 256 --repeats 3 --output results\two-laptops.json
```

**Three laptops (coordinator/client/worker on laptop 1, workers on laptops 2 and 3):** start node-2 and node-3 with their unique certificates and labels, then execute:

```powershell
python scripts\benchmark_final.py --mode direct --dimensions 512 1024 2048 --tile 256 --repeats 3 --output results\three-laptops.json
```

Collect the named JSON and sibling CSV from the client laptop:

```powershell
Copy-Item results\one-laptop.json,results\one-laptop.csv,results\two-laptops.json,results\two-laptops.csv,results\three-laptops.json,results\three-laptops.csv C:\DSM\results\
```

For each node-count experiment, record its node log and cluster stats before and after. Execute separate runs after changing the coordinator scheduler configuration; `--scheduler` currently records the requested policy only and does not remotely mutate coordinator policy. Example comparisons:

```powershell
python scripts\benchmark_final.py --mode mediated --dimensions 512 1024 --tile 256 --repeats 3 --output results\mediated.json
python scripts\benchmark_final.py --mode direct --input-access direct --dimensions 512 1024 --tile 256 --repeats 3 --output results\direct.json
$env:DSM_WORKER_TILE_CACHE_BYTES='0'; python scripts\benchmark_final.py --mode optimized --dimensions 512 1024 --tile 256 --repeats 3 --output results\cache-off.json
$env:DSM_WORKER_TILE_CACHE_BYTES='67108864'; python scripts\benchmark_final.py --mode optimized --dimensions 512 1024 --tile 256 --repeats 3 --output results\cache-on.json
python scripts\benchmark_final.py --mode client_compute --dimensions 512 1024 --tile 256 --repeats 3 --output results\client-compute.json
```

The optimized script now exports client framed bytes/requests, coordinator-to-node framed byte deltas, per-node RSS-related resource fields available from heartbeats (used/available DSM memory and CPU), worker task/node participation, local/remote reads, cache hits/misses/bytes avoided, and locality ratio. Physical comparison runs are still required before asserting savings. Modes `mediated`, `direct`, and `optimized` select worker computation and direct-read behavior; `client_compute` stores remotely but computes in the client process.

## Resource and safety checks

Run a small correctness workload first, then scale one dimension at a time. The matrix workload requires at least 3N²×4 bytes across input and output backing storage; distributed DSM additionally needs input tile copies, partial products, protocol buffers, and worker workspaces. Ensure aggregate node capacity and per-client quotas cover these allocations. Stop the client workload, then workers, then coordinator with Ctrl+C. The physical LAN procedure has not been executed on physical laptops in this session; do not report LAN scaling or speedup without collecting those measurements.

## Existing measurements and limits

The repository includes prior JSON/PNG experiments under `results/`. The latest run measured 256 and 512 square workloads with three repetitions and sampled output-cell validation. The end-to-end clock now starts before deterministic input generation. On this laptop, local tiled medians were 31.91 ms and 48.56 ms; native medians were 16.95 ms and 25.15 ms. Variability is high for the 256 runs because of outliers (129 ms spread for local tiled and 99 ms for native); use the raw repetitions, not just these medians. Raw rows are `results/final-local.json` and `results/final-native.json`, with sibling CSV files. The plot is `results/final-matrix-comparison.png`. These small local timings are not distributed performance claims.

The full regression suite was rerun with pytest temporary files redirected into `.test-tmp`: **537 passed, 1 skipped** in 283.06 seconds. A first attempt using the system temporary path failed during fixture setup due to Windows access denial; the workspace-temp rerun completed.

The latest complete suite after bounded I/O, cache, cleanup, submission, and benchmark telemetry changes: **538 passed, 1 skipped** in 288.65 seconds. The multi-process placement integration test also passed with the default serial upload setting.

The adapter bounds tile upload concurrency (`io_concurrency`, default 1). Higher values can change placement and require deployment-specific verification; increase with `--io-concurrency` only after confirming multi-node placement. Remote task submissions run in bounded dependency-aware waves. Cleanup RPCs run in bounded groups. These remain individual authenticated RPC messages rather than a new multi-object allocation/write/seal/free protocol. The worker cache is configurable through `DSM_WORKER_TILE_CACHE_BYTES` and checks task authorization before reading cache entries. Cache metrics are included per worker task and exported by the distributed benchmark. The measurement pipeline separates total wall time, preparation/allocation/transfer/seal wall time, adapter tile-conversion time, schedule+compute phase wall time, summed worker compute duration, result collection, and cleanup. Do not add worker durations to estimate total wall time because workers run concurrently. Client RSS is sampled during the workload. Per-node RSS is not yet included in heartbeats, which expose DSM capacity/usage, available memory, and CPU. Locality versus round-robin is only the coordinator's configured scheduler policy and must be changed in coordinator config/environment between runs. Do not interpret the benchmark's `--scheduler` field alone as changing that policy.

No physical LAN machines were available for this run. Memory capacity scaling, distributed speedup, locality traffic reduction, and one/two/three-laptop results therefore remain unverified. The workload must be run on actual laptops before reporting those outcomes.
