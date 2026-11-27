# Final Experimental Results

## Test environment and scope

Measurements in this report come from the authenticated TLS integration test on one Windows host: Intel Core i5-1235U, 10 cores / 12 logical CPUs. The coordinator and three independent resource-node processes ran on that host. The test capped each node's DSM storage at 10 MiB, each worker at two slots, and BLAS/OpenMP at one thread. These are logical DSM limits over processes sharing one physical machine; they do not demonstrate aggregation of physically separate RAM.

The controlled comparison ran each case twice with the same three nodes, node budgets, worker slots, thread settings, TLS and authorization, input seed, tile dimensions, operation, locality scheduler, and output collection. The only changed setting was coordinator-mediated versus authorized direct local reads. The inputs were intentionally small: 8x11 RGB images and a 7x5 by 5x6 float64 matrix product. Results therefore characterize framework overhead and protocol bytes at small sizes, not a large-workload performance crossover.

## Mediated versus direct reads

Times are two-run medians; the range is the minimum and maximum of those two runs.

| Workload | Mediated time (s) | Direct time (s) | Mediated input local / remote (B) | Direct input local / remote (B) |
|---|---:|---:|---:|---:|
| Image invert | 2.393 (2.267–2.519) | 2.349 (2.257–2.441) | 0 / 528 | 528 / 0 |
| Grayscale | 2.456 (2.361–2.552) | 2.381 (2.207–2.554) | 0 / 528 | 528 / 0 |
| Gaussian blur | 2.395 (2.316–2.474) | 2.370 (2.356–2.384) | 0 / 720 | 720 / 0 |
| Matrix multiply | 2.036 (2.021–2.051) | 1.996 (1.988–2.005) | 0 / 1,040 | 560 / 480 |

The measurements do not show a reliable end-to-end time advantage at this workload size. Differences are within the observed two-run spread. Native NumPy/OpenCV and local-tiled timings were much smaller, so these small distributed calls are dominated by setup, authorization, task, polling, and collection overhead.

Framed application-protocol counters include serialized protobuf messages and the 4-byte frame header. Client-to-coordinator and coordinator-side node-connection counts are reported separately; each coordinator/node link is counted once, from the coordinator endpoint. These counters are not TLS record or physical-wire byte counts. Direct reads are process-local and add no connection traffic.

| Workload | Mediated coordinator/node sent + received (B) | Direct coordinator/node sent + received (B) | Reduction in those framed bytes |
|---|---:|---:|---:|
| Image invert | 38,934 | 33,182 | 14.8% |
| Grayscale | 38,962 | 34,352 | 11.8% |
| Gaussian blur | 39,489 | 34,502 | 12.6% |
| Matrix multiply | 816,925 | 27,012 | 96.7% |
| **All four rows** | **934,310** | **129,048** |

Direct sends totaled 46,246 B and receives totaled 82,802 B; combined coordinator/node traffic fell by **86.2%**. Client-to-coordinator framed totals across these rows were 120,402 sent / 27,844 received in mediated mode and 119,241 sent / 27,720 received in direct mode. Client traffic is similar; locality removes worker input retrieval from the coordinator/node connections, with the strongest effect on the matrix's repeated tile reads. Direct mode did retain 480 remote input bytes in the small matrix case because some selected worker tasks did not have their tiles locally.

Using `local_input_bytes / (local_input_bytes + remote_input_bytes)`, measured locality was 0% in mediated mode and 82.95% in direct mode over these four rows and their repetitions. Direct image inputs were local; the direct matrix case was 53.8% local.

During each whole four-workload benchmark process, the 50 ms RSS sampler recorded a client peak of 49.8 MiB, coordinator peak of 4.12 MiB, and 4.12 MiB per node process; the aggregate sampled peak was 66.3 MiB in both runs. This sampler covers the entire benchmark process, not an isolated operation.

Both benchmark result files include per-workload RSS samples, request counts, per-payload counters, and scheduler metadata: [mediated JSON](../results/controlled-mediated-3nodes.json) and [direct JSON](../results/controlled-direct-3nodes.json). Graphs are [time comparison](../results/controlled-access-time.png) and [input bytes](../results/controlled-access-bytes.png).

## 256-square matrix comparison

The file-backed harness ran two repetitions each for a 256x256 float32 product with 128-square tiles on the same three-node cluster. Every result was checked against NumPy in bounded row chunks. Native file-backed NumPy took a 3.39 ms median; local-tiled DSM execution took 6.98 s (5.09–8.88 s); direct-read distributed execution took 7.24 s (7.22–7.27 s) and used all three worker processes. The distributed time comprised about 4.77 s input preparation, 1.66 s task execution, and 0.81 s output collection at the median. Local tiled and distributed paths are dominated by DSM allocation, transfer, task, and collection work at this dimension; distribution did not benefit this workload.

Raw per-repetition and summary rows, including RSS and per-connection traffic, are in [matrix-256-results.json](../results/matrix-256-results.json); the corresponding [dimension/time plot](../results/matrix-dimension-execution-time.png) shows the one measured dimension. This is one matrix dimension and one tile size, not a dimension or tile-size sweep.

## Bounded-memory capacity experiment

The integration experiment generated two 1024x1024 float32 matrices in deterministic 64-row chunks, mapped the `.npy` inputs read-only, uploaded DSM tiles, and wrote the result incrementally into a file-backed `.npy` output. The dense storage requirement for A+B+C was 12,582,912 B (12 MiB), greater than each node's configured 10 MiB and less than the three-node configured total of 31,457,280 B (30 MiB). Numerical output was checked in 64-row chunks. The worker results came from all three resource processes and recorded 16 MiB of direct local input reads.

| Resource node | Configured DSM storage | Stored after inputs | Peak sampled stored bytes |
|---|---:|---:|---:|
| Node 1 | 10 MiB | 0 MiB | 8 MiB |
| Node 2 | 10 MiB | 7 MiB | 9 MiB |
| Node 3 | 10 MiB | 1 MiB | 3 MiB |

| Process | Baseline RSS | Peak sampled RSS |
|---|---:|---:|
| Client/test process | 73.8 MiB | 94.7 MiB |
| Coordinator | 4.12 MiB | 4.12 MiB |
| Node 1 | 4.11 MiB | 4.11 MiB |
| Node 2 | 4.12 MiB | 4.12 MiB |
| Node 3 | 4.12 MiB | 4.12 MiB |
| Aggregate sampled peak across processes | — | 111.2 MiB |

The RSS sampler ran every 50 ms and the DSM stored-byte sampler every 250 ms; short peaks can fall between samples. Configured storage limits are scheduler budgets, not claims about physical RAM. The experiment demonstrates the tested 12 MiB dense workload across logical limits on one host, not a general maximum supported size. Raw measurements are in [capacity JSON](../results/capacity-1024-f32-3nodes.json).

## File-backed formats

- Matrix adapter `.npy` inputs are opened with NumPy memory mapping and converted/uploaded tile by tile. Output tiles are written incrementally to an `.npy` memory map. Deterministic matrix inputs can be generated in bounded row chunks.
- Image adapter `.npy` inputs use memory mapping and tile/halo reads; output tiles are written incrementally to `.npy`.
- PNG/JPEG inputs still use OpenCV's conventional full-image decoder and materialize the decoded image. The file-backed bounded-memory claim does not apply to those compressed formats.

## What remains unmeasured or unimplemented

- The benchmark harness supports file-backed square matrix dimensions and tile sizes, but only the 1024 capacity workload and the small controlled matrix case have results at this point. A 256x256 native/local-tiled/distributed run is part of final regression verification; larger dimensions, rectangular performance sweeps, and image sizes 512 and above are not claimed here.
- A worker-side tile cache and a controlled enabled/disabled cache comparison are not implemented.
- Bounded retry/recovery after worker loss, abandoned-output reclamation after abrupt disconnect, and safe pin cleanup across coordinator failure are not implemented. Existing normal task-failure cleanup is not equivalent to worker-loss recovery.
- Client resource enabled/disabled A/B, one/two/three-node timing and scheduling-policy comparisons have not been run as controlled experiments.
- Stage-level profiling is partial: task and worker metrics, per-connection framed bytes, operation-level wall time, and RSS exist, but timings for every authorization, allocation, queue, poll, read, output-commit, and cleanup phase are not separately attributed. No optimization is claimed from unmeasured stages.
- Physical LAN and Docker experiments were unavailable. See [deployment preparation](DISTRIBUTED_COMPUTE_DEPLOYMENT.md); no remote-machine measurements are presented as results.

## Reproducing the file-backed matrix sweep

With the authenticated TLS cluster running and `DSM_CLIENT_ENROLLMENT_SECRET` set, run the benchmark on the client. Set thread limits before starting Python so NumPy's BLAS backend inherits them:

```powershell
$env:OPENBLAS_NUM_THREADS = '1'
$env:OMP_NUM_THREADS = '1'
$env:MKL_NUM_THREADS = '1'
python scripts\benchmark_matrix_memmap.py --host 192.0.2.10 --server-name coordinator --port 8443 --dimensions 256 512 1024 --block-sizes 128 256 512 --modes native local_tiled distributed_compute --input-access-mode direct --repeats 3 --work-dir results\matrix-work --output results\matrix-memmap-results.json
```

The harness validates each file-backed output and records operation stages, per-task local/remote bytes, client and coordinator/node framed traffic, process RSS, hardware metadata, and per-case median/min/max. Use a separate identical run with `--input-access-mode mediated` for the baseline; run policy comparisons in separate cluster configurations rather than treating a CLI label as a live scheduler change.
