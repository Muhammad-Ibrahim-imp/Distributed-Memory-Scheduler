# Distributed Compute Deployment

## Local Docker Compose

Docker is not installed in the current validation environment, so these commands are prepared but not executed here.

1. Create a private `.env` from `.env.example`. Set three different random secrets of at least 32 bytes: `DSM_NODE_ENROLLMENT_SECRET`, `DSM_CLIENT_ENROLLMENT_SECRET`, and `DSM_TOKEN_KEY`.
2. Generate TLS material on the deployment host:

   ```powershell
   python scripts\gen_certs.py
   python scripts\gen_certs.py --issue-node node-2
   python scripts\gen_certs.py --issue-node node-3
   ```

3. Start the coordinator and three nodes:

   ```powershell
   docker compose up --build -d coordinator node-1 node-2 node-3
   docker compose logs -f coordinator node-1 node-2 node-3
   ```

   The nodes run compute workers by default. Set `DSM_COMPUTE_ENABLED=false` for all nodes, or `NODE_3_COMPUTE_ENABLED=false` for a memory-only third node. Tune `NODE_CAPACITY_BYTES`, `NODE_COMPUTE_SLOTS`, `NODE_COMPUTE_MEMORY_BYTES`, `NODE_CPU_LIMIT`, and `NODE_CONTAINER_MEMORY_LIMIT` through `.env`.

4. Run a distributed authenticated smoke benchmark from the client container:

   ```powershell
   docker compose up -d client
   docker compose exec client python scripts/benchmark_remote.py --host coordinator --server-name coordinator --sizes 4096 --max-bytes 1048576 --repeats 1 --application-repeats 3 --image-size 32 --tile-size 16 --block-size 4 --distributed-compute --output /tmp/remote-compute.json
   docker compose exec client python -c "import json; print(json.load(open('/tmp/remote-compute.json'))[-1])"
   ```

   The benchmark rows include execution mode, worker node IDs/PIDs, and worker execution time for image and matrix tasks. A label such as `--scheduler-policy locality` does not itself configure the coordinator; see the scheduler policy setting in the code/configuration before comparing policies.

## Physical LAN

Use one coordinator and one unique node certificate per worker. Never copy a node private key to a different worker. Copy the CA certificate and each machine's own certificate/key over a secure channel.

1. On the coordinator/development machine, generate the CA, server and client certificates, and unique node certificates:

   ```powershell
   python scripts\gen_certs.py
   python scripts\gen_certs.py --issue-node node-2
   python scripts\gen_certs.py --issue-node node-3
   ```

2. Install Python 3.12 and the repository dependencies on each machine:

   ```powershell
   py -3.12 -m venv .venv
   .\.venv\Scripts\python.exe -m pip install -r requirements.txt
   ```

3. On the coordinator machine, set the private secrets, the certificate directory, and the routable bind address, then start the coordinator:

   ```powershell
   $env:DSM_CERT_DIR = 'C:\DSM\certs'
   $env:DSM_NODE_ENROLLMENT_SECRET = '<same private node secret on coordinator and nodes>'
   $env:DSM_CLIENT_ENROLLMENT_SECRET = '<private client enrollment secret>'
   $env:DSM_TOKEN_KEY = '<private token key>'
   $env:DSM_SECURITY_CONFIG = 'C:\DSM\configs\security.yaml'
   $env:DSM_COMPUTE_SCHEDULER = 'locality' # or 'round_robin' for a separate comparison run
   $env:SECURITY_HOST = '0.0.0.0'
   $env:SECURITY_PORT = '8443'
   .\.venv\Scripts\python.exe scripts\coordinator_main.py
   ```

   To contribute the coordinator/client laptop's RAM and CPU, start a resource node in a second terminal on that same laptop, using its own node certificate. The node process hosts both the memory service and compute worker:

   ```powershell
   $env:DSM_CERT_DIR = 'C:\DSM\certs'
   $env:DSM_NODE_ENROLLMENT_SECRET = '<same private node secret>'
   $env:SECURITY_HOST = '127.0.0.1'
   $env:SECURITY_PORT = '8443'
   $env:DSM_TLS_SERVER_NAME = 'coordinator'
   $env:NODE_ID = 'node-1'
   $env:DSM_NODE_CERT_FILE = 'C:\DSM\certs\nodes\node-1.pem'
   $env:DSM_NODE_KEY_FILE = 'C:\DSM\certs\nodes\node-1.key'
   $env:NODE_CAPACITY_BYTES = '268435456'
   $env:NODE_COMPUTE_SLOTS = '2'
   $env:NODE_COMPUTE_MEMORY_BYTES = '67108864'
   .\.venv\Scripts\python.exe scripts\node_main.py
   ```

   This is a separate authenticated resource-node process on the same laptop; it uses in-process direct reads for sealed objects placed in its local RAM.

4. Allow inbound TCP 8443 through the coordinator firewall. From each node machine, set the coordinator's reachable IP and its own certificate paths, then start its node/worker process. Keep `DSM_TLS_SERVER_NAME` equal to a DNS name in the coordinator certificate SAN; make that name resolve to the coordinator IP on every node/client.

   ```powershell
   $env:DSM_CERT_DIR = 'C:\DSM\certs'
   $env:DSM_NODE_ENROLLMENT_SECRET = '<same private node secret>'
   $env:SECURITY_HOST = '192.0.2.10'
   $env:SECURITY_PORT = '8443'
   $env:DSM_TLS_SERVER_NAME = 'coordinator'
   $env:NODE_ID = 'node-2'
   $env:DSM_NODE_CERT_FILE = 'C:\DSM\certs\nodes\node-2.pem'
   $env:DSM_NODE_KEY_FILE = 'C:\DSM\certs\nodes\node-2.key'
   $env:NODE_CAPACITY_BYTES = '268435456'
   $env:NODE_COMPUTE_SLOTS = '2'
   $env:NODE_COMPUTE_MEMORY_BYTES = '67108864'
   .\.venv\Scripts\python.exe scripts\node_main.py
   ```

   On a memory-only node set `$env:DSM_COMPUTE_ENABLED = 'false'`. Repeat with `NODE_ID=node-3` and that machine's separate node certificate/key.

5. On the client machine, install the client certificate/key and CA; set the client secret and use the coordinator's SAN name:

   ```powershell
   $env:DSM_CERT_DIR = 'C:\DSM\certs'
   $env:DSM_CLIENT_ENROLLMENT_SECRET = '<private client enrollment secret>'
   $env:DSM_TLS_SERVER_NAME = 'coordinator'
   $env:OPENBLAS_NUM_THREADS = '1'
   $env:OMP_NUM_THREADS = '1'
   $env:MKL_NUM_THREADS = '1'
   .\.venv\Scripts\python.exe scripts\benchmark_remote.py --host 192.0.2.10 --server-name coordinator --port 8443 --sizes 4096 65536 --max-bytes 1048576 --repeats 3 --application-repeats 5 --image-size 512 --tile-size 128 --block-size 128 --distributed-compute --input-access-mode mediated --format json --output results\lan-mediated.json
   .\.venv\Scripts\python.exe scripts\benchmark_remote.py --host 192.0.2.10 --server-name coordinator --port 8443 --sizes 4096 65536 --max-bytes 1048576 --repeats 3 --application-repeats 5 --image-size 512 --tile-size 128 --block-size 128 --distributed-compute --input-access-mode direct --format json --output results\lan-direct.json
   .\.venv\Scripts\python.exe scripts\benchmark_matrix_memmap.py --host 192.0.2.10 --server-name coordinator --port 8443 --dimensions 256 512 1024 --block-sizes 128 256 --modes native local_tiled distributed_compute --input-access-mode direct --repeats 3 --work-dir results\lan-matrix-work --output results\lan-matrix-direct.json
   .\.venv\Scripts\python.exe scripts\benchmark_matrix_memmap.py --host 192.0.2.10 --server-name coordinator --port 8443 --dimensions 256 512 1024 --block-sizes 128 256 --modes native local_tiled distributed_compute --input-access-mode mediated --repeats 3 --work-dir results\lan-matrix-work-mediated --output results\lan-matrix-mediated.json
   ```

   Run a separate set with `$env:DSM_COMPUTE_SCHEDULER = 'round_robin'` to compare policies; keep node count, budgets, slots, CPU limits, inputs, and tile sizes unchanged between policy runs. Record hardware on every participating laptop with:

   ```powershell
   Get-CimInstance Win32_Processor | Select-Object Name,NumberOfCores,NumberOfLogicalProcessors
   Get-CimInstance Win32_ComputerSystem | Select-Object TotalPhysicalMemory
   Get-CimInstance Win32_OperatingSystem | Select-Object Caption,Version
   ```

6. Confirm rows report `execution_mode=distributed_compute`, worker node IDs, and process IDs different from the client. Image operations use the correctness-checked `benchmark_remote.py`; file-backed matrix sweeps use `benchmark_matrix_memmap.py`. Inspect node logs and the coordinator audit log for registration and task failures. The provided image benchmark path still decodes PNG/JPEG into a full image; use `.npy` adapter calls for bounded-memory image input/output experiments.

The port and certificate workflow above is a runbook, not physical-LAN evidence. Firewall routing, DNS/SAN setup, throughput, and multi-machine security still require execution in the target network.
