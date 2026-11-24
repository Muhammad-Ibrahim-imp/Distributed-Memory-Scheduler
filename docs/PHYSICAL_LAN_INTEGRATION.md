# Physical LAN deployment and verification

This procedure uses one coordinator and at least two separate memory-node computers. It has **not** been run on physical LAN machines. Use isolated test machines and test data. The coordinator certificate must contain the DNS name `coordinator`; clients connect using that name so TLS hostname verification remains enabled.

## 1. Install Python dependencies

Run on every machine from a checked-out integration branch:

```powershell
py -3.12 -m venv .venv
.\.venv\Scripts\Activate.ps1
python -m pip install --upgrade pip
python -m pip install -r requirements.txt
```

## 2. Find the coordinator LAN address

On the coordinator computer:

```powershell
Get-NetIPConfiguration | Where-Object { $_.IPv4DefaultGateway } | Select-Object InterfaceAlias, IPv4Address
$CoordinatorIP = (Get-NetIPConfiguration | Where-Object { $_.IPv4DefaultGateway } | Select-Object -First 1 -ExpandProperty IPv4Address).IPAddress
$CoordinatorIP
```

Create a DNS A record `coordinator` for this address. If the LAN has no DNS administration, add the mapping as Administrator on every node and client computer:

```powershell
Add-Content -LiteralPath "$env:SystemRoot\System32\drivers\etc\hosts" -Value "$CoordinatorIP coordinator"
Resolve-DnsName coordinator
```

## 3. Generate and provision unique certificates

On a protected provisioning computer with this repository checked out, create a fresh isolated working directory and issue one certificate per node:

```powershell
$ProvisionRoot = 'C:\DSM-Provision'
New-Item -ItemType Directory -Force $ProvisionRoot | Out-Null
Set-Location $ProvisionRoot
python 'C:\path\to\Distributed-Memory-Scheduler\scripts\gen_certs.py'
python 'C:\path\to\Distributed-Memory-Scheduler\scripts\gen_certs.py' --issue-node node-2
python 'C:\path\to\Distributed-Memory-Scheduler\scripts\gen_certs.py' --issue-node node-3
Get-FileHash certs\nodes\node-1.pem,certs\nodes\node-2.pem,certs\nodes\node-3.pem -Algorithm SHA256
```

Store `certs\ca.key` offline after issuance. Never copy it to runtime machines. Transfer the following files through an encrypted, access-controlled channel:

| Destination | Files |
|---|---|
| Coordinator | `ca.pem`, `server.pem`, `server.key` |
| Each node | `ca.pem`, that node's unique `nodes\node-N.pem` and matching `.key` |
| Client machine | `ca.pem`, `client.pem`, `client.key` |

Restrict private key ACLs on Windows. For example, on each destination after copying:

```powershell
icacls C:\DSM\certs\server.key /inheritance:r /grant:r "$env:USERNAME:(R)"   # coordinator only
icacls C:\DSM\certs\nodes\node-2.key /inheritance:r /grant:r "$env:USERNAME:(R)" # matching node only
```

Generate a long random node enrollment secret and a different client enrollment secret using an approved password manager. Provision each secret through your secret manager or an encrypted channel; do not put secrets in source control or command history. Enter one into the current PowerShell process without echoing it:

Transfer certificate files with Windows OpenSSH `scp` over an approved SSH endpoint. Example (create the destination folders first and substitute the actual machine account names):

```powershell
$CoordinatorUser = 'dsmadmin'
$Node2User = 'dsmnode'
$ClientUser = 'dsmclient'
ssh "$CoordinatorUser@coordinator" 'New-Item -ItemType Directory -Force C:\DSM\certs | Out-Null'
ssh "$Node2User@node-2" 'New-Item -ItemType Directory -Force C:\DSM\certs\nodes | Out-Null'
ssh "$ClientUser@dsm-client" 'New-Item -ItemType Directory -Force C:\DSM\certs | Out-Null'
scp "$ProvisionRoot\certs\ca.pem" "$CoordinatorUser@coordinator:C:/DSM/certs/"
scp "$ProvisionRoot\certs\server.pem" "$ProvisionRoot\certs\server.key" "$CoordinatorUser@coordinator:C:/DSM/certs/"
scp "$ProvisionRoot\certs\ca.pem" "$ProvisionRoot\certs\nodes\node-2.pem" "$ProvisionRoot\certs\nodes\node-2.key" "$Node2User@node-2:C:/DSM/certs/nodes/"
scp "$ProvisionRoot\certs\ca.pem" "$ProvisionRoot\certs\client.pem" "$ProvisionRoot\certs\client.key" "$ClientUser@dsm-client:C:/DSM/certs/"
```

Repeat the node transfer for each node with its distinct PEM/key pair. Do not transfer `ca.key`. Store enrollment secrets in a password manager or deployment secret store and retrieve them interactively on each host.

```powershell
$secure = Read-Host 'Enrollment secret' -AsSecureString
$ptr = [Runtime.InteropServices.Marshal]::SecureStringToBSTR($secure)
$env:DSM_NODE_ENROLLMENT_SECRET = [Runtime.InteropServices.Marshal]::PtrToStringBSTR($ptr)
[Runtime.InteropServices.Marshal]::ZeroFreeBSTR($ptr)
```

Use `DSM_CLIENT_ENROLLMENT_SECRET` on the client and coordinator in the same way. Clear environment values when done with `Remove-Item Env:DSM_NODE_ENROLLMENT_SECRET` (and the client variable). Node labels are unique operator-selected labels; the coordinator creates the logical UUID identity at first enrollment.

## 4. Open the coordinator firewall port

Run on the coordinator in an elevated PowerShell window. Limit `-RemoteAddress` to the deployment subnet where possible:

```powershell
New-NetFirewallRule -DisplayName 'DSM coordinator TLS 8443' -Direction Inbound -Action Allow -Protocol TCP -LocalPort 8443 -RemoteAddress LocalSubnet
```

## 5. Start coordinator and nodes

On the coordinator, set paths and secrets, then start the service:

```powershell
$env:SECURITY_HOST = '0.0.0.0'
$env:SECURITY_PORT = '8443'
$env:DSM_CERT_DIR = 'C:\DSM\certs'
$env:DSM_SECURITY_CONFIG = 'C:\DSM\configs\security.yaml'
$env:DSM_SCHEDULER_CONFIG = 'C:\DSM\configs\scheduler.json'
$env:DSM_NODE_ENROLLMENT_SECRET = '<retrieve securely>'
$env:DSM_CLIENT_ENROLLMENT_SECRET = '<retrieve securely>'
$env:DSM_TOKEN_KEY = '<shared token key from secret manager>'
python scripts\coordinator_main.py
```

On each physical node, use a distinct `NODE_ID`, capacity, cert, and key. Example for node 2:

```powershell
$env:SECURITY_HOST = 'coordinator'
$env:SECURITY_PORT = '8443'
$env:DSM_CERT_DIR = 'C:\DSM\certs'
$env:DSM_NODE_CERT_FILE = 'C:\DSM\certs\nodes\node-2.pem'
$env:DSM_NODE_KEY_FILE = 'C:\DSM\certs\nodes\node-2.key'
$env:NODE_ID = 'node-2'
$env:NODE_CAPACITY_BYTES = '268435456'
$env:NODE_HEARTBEAT_INTERVAL = '2'
$env:DSM_NODE_ENROLLMENT_SECRET = '<retrieve securely>'
python scripts\node_main.py
```

Repeat on other node computers with unique labels, capacities, and certificate file paths. Do not duplicate node private keys. The coordinator cert and client trust roots can also be set individually with `DSM_SERVER_CERT_FILE`, `DSM_SERVER_KEY_FILE`, `DSM_CLIENT_CERT_FILE`, `DSM_CLIENT_KEY_FILE`, and `DSM_CA_CERT_FILE`.

## 6. Run smoke, image, matrix, and placement checks

On a client computer, ensure DNS resolves `coordinator`, then:

```powershell
$env:DSM_COORDINATOR_HOST = 'coordinator'
$env:DSM_COORDINATOR_PORT = '8443'
$env:DSM_CA_CERT_FILE = 'C:\DSM\certs\ca.pem'
$env:DSM_CLIENT_CERT_FILE = 'C:\DSM\certs\client.pem'
$env:DSM_CLIENT_KEY_FILE = 'C:\DSM\certs\client.key'
$env:DSM_CLIENT_ENROLLMENT_SECRET = '<retrieve securely>'
python e2e_test.py
python e2e_big.py --objects 6 --size 5000
```

For real image inversion, grayscale, Gaussian blur, and non-divisible matrix multiplication through `RemoteDSMAPI`:

```powershell
python scripts\benchmark_remote.py --host coordinator --port 8443 --sizes 4096 65536 --max-bytes 1048576 --format json --output C:\DSM\results\lan-smoke.json
```

For a distributed placement check, copy node-side stdout logs to the client machine after the allocations and writes, then provide one file per node. The script asserts that successful write requests for generated IDs appear in at least two distinct node logs:

```powershell
python e2e_multi.py --host coordinator --port 8443 --objects 3 --size 8388608 --node-log C:\DSM\logs\node-1.log --node-log C:\DSM\logs\node-2.log --node-log C:\DSM\logs\node-3.log
```

Choose object size and count to fit the configured per-client quotas and total capacities. A successful object count alone is not placement evidence.

## 7. Failure, restart, and shutdown procedure

Use a disposable test workload. First stop one node with Ctrl+C and verify new work still runs on healthy nodes; restart it and observe fresh heartbeats and placement eligibility. Then stop the coordinator, restart it, verify old object IDs are rejected, and reauthenticate the client. Do not interpret restart as safe to reuse old node storage until A1 cleanup, B1 fencing, and B2 readiness are integrated and verified; the current Level 1 contract requires those gates.

For an orderly shutdown, stop client workloads, free objects, stop each node with Ctrl+C, then stop the coordinator. Remove the firewall rule only if the port is no longer needed:

```powershell
Remove-NetFirewallRule -DisplayName 'DSM coordinator TLS 8443'
```

Do not delete certificate databases, SQLite security databases, or volumes as part of normal shutdown. Record machine names, OS/Python versions, capacities, scheduler config, and result files for the final physical report.
