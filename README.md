# Distributed-Memory-Scheduler

## 1. Project Goal

Build a **Level 1 Distributed Memory Scheduler (DSM)** that combines the RAM of multiple computers into a shared, network-accessible memory system.

Applications interact with the DSM through a generic API:

```text
dsm_alloc(size)  → object_id
dsm_read(object_id) → data
dsm_write(object_id, data)
dsm_free(object_id)
```

The DSM decides where objects are stored and retrieves them from the appropriate memory node.

The DSM engine is **application-independent**.

Initial application adapters:

```text
Image Processing
Matrix Multiplication
```

Additional application adapters may be added later without modifying the DSM core.

---

# 2. Level 1 Definition

Level 1 is an **application-aware DSM**.

Applications explicitly use the DSM API through an application adapter.

```text
Application
     ↓
Application Adapter
     ↓
DSM Runtime / API
     ↓
DSM Engine
     ↓
Scheduler
     ↓
Network
     ↓
Memory Nodes
```

The DSM does not transparently replace normal:

```text
malloc()
free()
```

or operating-system virtual memory.

Therefore, an arbitrary existing application cannot automatically use DSM without modification.

Transparent OS-level DSM belongs to a future Level 2 implementation.

---

# 3. System Architecture

```text
                         ┌───────────────────────┐
                         │      Application      │
                         └───────────┬───────────┘
                                     │
                                     ▼
                         ┌───────────────────────┐
                         │ Application Adapter   │
                         │ Image / Matrix / ...  │
                         └───────────┬───────────┘
                                     │
                                     ▼
                         ┌───────────────────────┐
                         │     DSM Runtime       │
                         │   Generic DSM API     │
                         └───────────┬───────────┘
                                     │
                    ┌────────────────┴────────────────┐
                    │                                 │
                    ▼                                 ▼
          ┌───────────────────┐             ┌───────────────────┐
          │  Object Manager   │             │      Cache        │
          └─────────┬─────────┘             └───────────────────┘
                    │
                    ▼
          ┌───────────────────┐
          │     Directory     │
          └─────────┬─────────┘
                    │
                    ▼
          ┌───────────────────┐
          │     Scheduler     │
          └─────────┬─────────┘
                    │
                    ▼
          ┌───────────────────┐
          │   Network Layer   │
          └─────────┬─────────┘
                    │
          ┌─────────┼─────────┐
          ▼         ▼         ▼
       Node 1    Node 2    Node 3
        RAM       RAM       RAM
```

---

# 4. Core Components

## 4.1 DSM Runtime

Provides the public DSM API used by applications.

Responsibilities:

```text
dsm_alloc()
dsm_read()
dsm_write()
dsm_free()
```

The runtime hides internal DSM implementation details from applications.

Applications should not need to know:

- which node stores an object
- how the object is transferred
- how the scheduler selects a node
- how networking works
- how security is implemented internally

---

## 4.2 Memory Node

A memory node contributes part of its local RAM to the DSM.

Responsibilities:

- local memory allocation
- object storage
- object retrieval
- object deletion
- object metadata
- memory capacity tracking
- free-memory tracking
- CPU/load statistics
- object isolation
- quotas
- secure deletion
- integrity checking
- node health reporting

---

## 4.3 Object Manager

Maintains the logical lifecycle of DSM objects.

Responsibilities:

- object creation
- object identification
- object lookup
- object deletion
- ownership information
- object metadata
- interaction with the directory
- interaction with the scheduler

---

## 4.4 Directory

Maintains the mapping between logical DSM objects and physical memory nodes.

Example:

```text
Object ID       Node
---------------------
OBJ-001         Node-2
OBJ-002         Node-1
OBJ-003         Node-3
```

Responsibilities:

- object lookup
- node lookup
- ownership information
- object location updates
- node failure handling

---

## 4.5 Scheduler

Determines where DSM objects should be stored.

Scheduling decisions may consider:

```text
Free RAM
CPU load
Network latency
Network bandwidth
Access frequency
Node health
Security state
```

The scheduler produces a placement decision based on the current state of available nodes.

---

## 4.6 Network Layer

Provides communication between:

```text
DSM Runtime
Memory Nodes
Scheduler
Directory
Security Services
```

Responsibilities:

- request transmission
- response transmission
- serialization
- connection management
- node discovery
- heartbeats
- timeout handling
- retries
- secure communication

---

## 4.7 Cache

The DSM client may cache recently accessed objects.

Responsibilities:

- local object caching
- cache lookup
- cache insertion
- cache eviction
- cache invalidation

Caching must respect the selected consistency model.

---

## 4.8 Consistency Manager

Maintains correctness when DSM objects are accessed or modified.

Responsibilities:

- object versions
- write coordination
- cache invalidation
- stale-data detection
- synchronization

The Level 1 implementation should use a clearly defined and manageable consistency model rather than attempting to implement a fully general DSM consistency system.

---

# 5. Security Architecture

Security is distributed according to the layer each member already owns.

```text
┌─────────────────────────────────────────────────────────┐
│                    Central Security                     │
│ Authentication / Authorization / Policy / Audit         │
│                       M5                                │
└──────────────────────────┬──────────────────────────────┘
                           │
             ┌─────────────┼─────────────┐
             ▼             ▼             ▼
       Memory Node      Network       Scheduler
           A1              B1             B2
           │               │              │
       Local security   TLS/session   Security-aware
       isolation        protection    placement
       quotas           validation    quarantine rules
       integrity        replay        node exclusion
```

Security mechanisms include:

- authentication
- authorization
- TLS encryption
- secure sessions
- request IDs
- timestamps/nonces
- request expiration
- rate limiting
- quotas
- object isolation
- SHA-256 integrity checks
- version validation
- audit logs
- node revocation
- node quarantine

Security responsibilities are deliberately separated so that no single member owns the entire security implementation.

---

# 6. Application Adapter Architecture

Application-specific logic belongs inside adapters.

```text
Application
     ↓
Application Adapter
     ↓
Generic DSM API
     ↓
DSM Engine
```

The DSM core must never contain application-specific implementation such as:

```text
if application == "image":
    ...

if application == "matrix":
    ...
```

A new application should only require:

```text
New Application
      ↓
New Adapter
      ↓
Existing DSM API
```

without modifying the DSM core.

---

# 7. Image Processing Adapter

The Image Processing Adapter converts image data into DSM-managed objects.

Example:

```text
Large Image
     ↓
Split into chunks
     ↓
DSM allocations
     ↓
Distributed memory nodes
     ↓
Image processing
     ↓
Result aggregation
     ↓
Reconstructed image
```

Possible operations:

- image loading
- image chunking
- distributed storage
- image transformation
- result reconstruction

The adapter uses only the generic DSM API.

### Owner

**M5 — Application Integration**

---

# 8. Matrix Multiplication Adapter

The Matrix Multiplication Adapter divides matrices into blocks.

Example:

```text
A = [A11 A12]
    [A21 A22]

B = [B11 B12]
    [B21 B22]
```

Block operations:

```text
C11 = A11B11 + A12B21
C12 = A11B12 + A12B22
C21 = A21B11 + A22B21
C22 = A21B12 + A22B22
```

Matrix blocks can be stored across DSM nodes and processed using the generic DSM API.

### Owner

**M5 — Application Integration**

---

# 9. Generic Application Adapter Interface

All application adapters must follow a common interface.

Conceptually:

```python
class ApplicationAdapter:

    def prepare(self, input_data):
        ...

    def execute(self):
        ...

    def collect_result(self):
        ...
```

The exact interface is defined in:

```text
common/interfaces/
```

The DSM engine must not depend on a particular adapter implementation.

---

# 10. Parallel Development Strategy

The system is divided so that members can develop independently against stable interfaces.

```text
                    Common Interfaces
                           │
       ┌──────────┬────────┼────────┬──────────┐
       ▼          ▼        ▼        ▼          ▼
      A1         A2       B1       B2         M5
       │          │        │        │          │
 Memory Node    DSM     Network  Scheduler  Security
 + local       Core    + TLS    + security  + adapters
 security                                   + integration
       │          │        │        │          │
       └──────────┴────────┴────────┴──────────┘
                           │
                           ▼
                    Integration Phase
```

### Shared interfaces must be defined early

Before implementation, define:

- DSM API
- Memory Node API
- Scheduler API
- Network protocol
- Object metadata format
- Node status format
- Security status format
- Application Adapter interface

This allows members to use mocks/stubs instead of waiting for another member's implementation.

---

# 11. Team Responsibilities

## A1 — Memory Node + Local Security + Capacity Experiment

### Core responsibilities

- local RAM allocator
- object storage
- object metadata
- memory capacity tracking
- free-memory tracking
- CPU/load statistics
- object isolation
- memory quotas
- secure deletion
- local integrity checks
- node health reporting

### Security responsibilities

A1 owns **memory-node-level security**:

- object isolation
- per-client/object access boundaries
- local quotas
- secure deletion
- SHA-256 integrity verification
- local resource abuse protection
- node-side validation of allowed operations

A1 does not own global authentication or authorization policy.

### Experiment responsibility

- capacity scaling experiment
- memory utilization measurement

### Primary tools

```text
Python          — A1
asyncio         — A1
psutil          — A1
hashlib         — A1
pytest          — A1
NumPy           — A1
Git/GitHub      — A1
Docker          — A1
```

---

# 12. A2 — DSM Abstraction

A2's responsibilities remain unchanged.

### Responsibilities

- DSM Runtime
- generic DSM API
- Object Manager
- global object namespace
- Directory
- object ownership
- object lifecycle
- object metadata
- object location management
- consistency model
- object versioning
- client-side cache
- cache invalidation
- stale-data handling

### Primary tools

```text
Python          — A2
asyncio         — A2
pytest          — A2
Git/GitHub      — A2
Docker          — A2
```

A2 does not own application-specific logic or central security policy.

---

# 13. B1 — Network + Transport Security + Network Benchmarking

### Core responsibilities

- TCP communication
- request/response protocol
- serialization
- connection management
- node discovery
- heartbeats
- timeouts
- retries
- connection pooling
- network error handling

### Security responsibilities

B1 owns **network and transport security**:

- TLS
- secure connections
- certificate handling at the transport layer
- request validation
- secure session transport
- replay protection mechanisms
- request IDs
- timestamps/nonces
- connection-level rate limiting
- connection authentication hooks

M5 defines the central security policy; B1 implements the network mechanisms required by that policy.

### Experiment responsibility

- network latency benchmarking
- network throughput benchmarking
- serialization overhead
- local vs remote access comparison
- network overhead analysis

### Primary tools

```text
Python              — B1
asyncio             — B1
socket              — B1
ssl                 — B1
Protocol Buffers    — B1
Wireshark           — B1
pytest               — B1
Git/GitHub           — B1
Docker               — B1
```

---

# 14. B2 — Scheduler + Security-Aware Placement + Performance Scaling

### Core responsibilities

- node scoring
- placement policy
- load balancing
- free-RAM-aware scheduling
- CPU-aware scheduling
- latency-aware scheduling
- bandwidth-aware scheduling
- access-frequency-aware scheduling
- placement decisions
- scheduling configuration

### Security responsibilities

B2 owns **security-aware scheduling**:

- consume node security state
- exclude revoked nodes
- exclude quarantined nodes
- restrict suspicious nodes
- prevent placement on unavailable nodes
- integrate security score/state into placement decisions

Example:

```text
TRUSTED       → eligible
SUSPICIOUS    → restricted
QUARANTINED  → excluded
REVOKED       → excluded
```

B2 does not implement authentication or central authorization.

### Experiment responsibility

- multi-node performance scaling
- scheduler comparison
- workload distribution
- speedup measurement
- efficiency measurement
- CPU utilization
- memory utilization
- scheduling overhead

### Primary tools

```text
Python          — B2
NumPy           — B2
asyncio         — B2
psutil          — B2
pytest          — B2
Matplotlib      — B2
Git/GitHub      — B2
Docker          — B2
```

---

# 15. M5 — Central Security + Application Adapters + Integration

M5 owns the **central security layer and application-specific integration**, but not the lower-level security mechanisms implemented by A1, B1, and B2.

### Central security responsibilities

- authentication
- authorization
- central security policy
- access-control policy
- session policy
- node registration
- node trust management
- node revocation
- node quarantine
- security event management
- audit logging
- security configuration
- central security testing

### Application responsibilities

- Image Processing Adapter
- Matrix Multiplication Adapter
- common application-adapter framework
- adapter-specific testing

### Integration responsibilities

- define integration contracts with other members
- end-to-end system integration
- cross-component testing
- integration test suite
- system configuration
- final workflow verification
- failure-path integration testing

M5 does not implement:

```text
Memory Node internals
Network protocol internals
Scheduler internals
DSM abstraction internals
```

### Security ownership

```text
A1 → Local memory/node security

B1 → Network/transport security

B2 → Security-aware scheduling

M5 → Central security policy/control
```

### Primary tools

```text
Python              — M5
cryptography        — M5
pytest               — M5
OpenCV               — M5
NumPy                — M5
Docker               — M5
Git/GitHub           — M5
```

---

# 16. Technology Stack

## Programming Language

```text
Python 3.12+
```

Python is used throughout the Level 1 implementation.

---

## Networking

```text
asyncio
socket
TLS/SSL
Protocol Buffers
```

Usage:

```text
asyncio             — A1, A2, B1, B2, M5
socket              — B1
ssl                 — B1
Protocol Buffers    — B1
```

---

## Serialization

```text
Protocol Buffers
```

Used for structured:

```text
AllocateRequest
ReadRequest
WriteRequest
FreeRequest
NodeHeartbeat
NodeStats
ErrorResponse
SecurityEvent
```

### Owner

**B1**

---

# 17. Security Technology

```text
TLS
cryptography
hashlib
secrets
request IDs
timestamps
nonces
```

Usage:

```text
TLS/SSL             — B1
cryptography        — M5
hashlib/SHA-256     — A1
secrets/nonces      — B1
authentication      — M5
authorization       — M5
```

---

# 18. System Monitoring

```text
psutil
```

Used to obtain:

- CPU utilization
- RAM utilization
- available RAM
- system statistics

Usage:

```text
A1 — Memory-node monitoring
B2 — Scheduler metrics
```

---

# 19. Numerical Computing

```text
NumPy
```

Used for:

- matrix operations
- matrix blocks
- numerical data
- benchmark workloads

Primary owner:

**M5 — Matrix Adapter**

B2 uses NumPy for scheduler experiments and benchmark analysis where required.

---

# 20. Image Processing

```text
OpenCV
NumPy
```

Used for:

- image loading
- image processing
- image chunking
- image reconstruction
- image benchmark workloads

Primary owner:

**M5 — Image Processing Adapter**

---

# 21. Testing

## pytest

All members use pytest.

```text
A1 — Memory-node tests
A2 — DSM abstraction tests
B1 — Network/security-transport tests
B2 — Scheduler tests
M5 — Central-security/adapter/integration tests
```

Testing levels:

```text
Unit Tests
    ↓
Component Tests
    ↓
Integration Tests
    ↓
Security Tests
    ↓
Performance Tests
    ↓
End-to-End Tests
```

---

# 22. Docker

Docker is used to reproduce multiple DSM nodes on one physical machine.

Example:

```text
Docker Host
│
├── Node 1
├── Node 2
├── Node 3
├── Node 4
└── Node 5
```

Useful for:

- multi-node development
- integration testing
- failure testing
- reproducible experiments

Primary owner:

**M5 — Integration**

All members may use Docker for component testing.

### 22.1 Deployment Model — Local Testing vs. Final Integration/Demo

**Docker, as described above, is used for individual component development and local testing throughout the project.** It is not the deployment model for the final Integration Phase or for the two required experiments.

For the Integration Phase (§28), the Capacity Experiment (§29), and the Performance Scaling Experiment (§30), nodes are deployed across the team's **actual, separate physical machines**, connected over a real network (Wi-Fi/LAN) — matching the project's stated goal (§1) of combining RAM across real, separate computers, rather than simulating that separation entirely inside one machine's Docker containers.

This distinction matters concretely for two of the required experiments:

- The Performance Scaling Experiment's `Speedup(N)`/`Efficiency(N)` measurements are only meaningful with genuine network latency between real machines — Docker's internal bridge network has near-zero latency, which would understate or flatten the scaling behavior the experiment is meant to demonstrate.
- The Capacity Experiment (workload larger than one machine's RAM but smaller than the combined pool) is a stronger, more literal proof on real separate machines' RAM than on `mem_limit`-capped containers sharing one machine's actual physical RAM underneath.

Docker continues to be used, as before, for each member's own local development, unit/component testing, and reproducing failure scenarios — this does not change. Configuration for node/service addressing should be read from `configs/` rather than hardcoded, so switching between a single-Docker-host setup and a real multi-machine setup is a configuration change, not a code change.

---

# 23. Wireshark

Wireshark is used for network analysis.

Use cases:

- TCP debugging
- packet inspection
- latency investigation
- protocol verification
- TLS verification
- connection debugging

Primary owner:

**B1**

Secondary:

**M5**

---

# 24. Matplotlib

Used to visualize benchmark results.

Required graphs include:

```text
Nodes vs Execution Time
Nodes vs Speedup
Nodes vs Efficiency
Nodes vs Throughput
Memory Utilization
CPU Utilization
Network Latency
Network Throughput
```

Primary owner:

**B2**

---

# 25. Git and GitHub

Git/GitHub are used by all members for:

- version control
- branches
- pull requests
- code review
- issue tracking
- collaboration

Recommended branches:

```text
main
│
├── feature/memory-node
├── feature/dsm-abstraction
├── feature/network
├── feature/scheduler
└── feature/security-adapters
```

---

# 26. Repository Structure

```text
DSM/
│
├── common/
│   ├── protocol/
│   ├── types/
│   └── interfaces/
│
├── memory_node/
│   ├── allocator/
│   ├── storage/
│   ├── metadata/
│   └── security/
│
├── dsm/
│   ├── runtime/
│   ├── object_manager/
│   ├── directory/
│   ├── cache/
│   └── consistency/
│
├── network/
│   ├── tcp/
│   ├── serialization/
│   ├── discovery/
│   └── security/
│
├── scheduler/
│   ├── policies/
│   ├── scoring/
│   └── load_balancing/
│
├── security/
│   ├── authentication/
│   ├── authorization/
│   ├── sessions/
│   ├── monitoring/
│   └── quarantine/
│
├── applications/
│   ├── image_processing/
│   └── matrix_multiplication/
│
├── tests/
│   ├── unit/
│   ├── integration/
│   ├── security/
│   └── performance/
│
├── configs/
├── requirements.txt
├── README.md
└── .gitignore
```

---

# 27. Development Phases

## Phase 1 — Shared Interfaces

All members agree on:

```text
DSM API
Memory Node API
Scheduler API
Network protocol
Object metadata
Node status
Security status
Application Adapter interface
```

This phase minimizes cross-member waiting.

---

## Phase 2 — Parallel Component Development

### A1

```text
Memory Node
+
Local Security
+
Capacity Experiment
```

### A2

```text
DSM Runtime
+
Object Manager
+
Directory
+
Cache
+
Consistency
```

### B1

```text
Network
+
Protocol
+
TLS
+
Network Benchmarking
```

### B2

```text
Scheduler
+
Security-Aware Placement
+
Performance Benchmarking
```

### M5

```text
Central Security
+
Image Adapter
+
Matrix Adapter
+
Integration Test Framework
```

All members can work simultaneously after Phase 1 interfaces are frozen.

---

# 28. Integration Phase

Connect:

```text
Application
    ↓
Application Adapter
    ↓
DSM Runtime
    ↓
Object Manager
    ↓
Directory
    ↓
Scheduler
    ↓
Network
    ↓
Memory Nodes
```

Security is integrated across each layer:

```text
A1 → Local node security
B1 → Network security
B2 → Secure placement
M5 → Central security policy
```

---

# 29. Capacity Experiment

Demonstrate that DSM can support a workload larger than the memory available on a single machine but smaller than the combined memory of multiple machines.

Example:

```text
Node 1 = 16 GB
Node 2 = 16 GB
Node 3 = 16 GB

DSM capacity = 48 GB

Workload = 30–40 GB
```

Measure:

- maximum workload size
- allocation success/failure
- execution time
- memory utilization
- distribution of objects

### Owner

**A1**

---

# 30. Performance Scaling Experiment

Run the same workload with different numbers of nodes.

Example:

```text
1 node
2 nodes
5 nodes
10 nodes
```

Measure:

- execution time
- speedup
- efficiency
- throughput
- network overhead
- CPU utilization
- memory utilization
- workload distribution

Use:

```text
Speedup(N) = T1 / TN
```

and:

```text
Efficiency(N) = Speedup(N) / N
```

Actual results must be experimentally measured.

### Owner

**B2**

---

# 31. Network Benchmark

Measure:

```text
Local memory access
vs.
Remote DSM access
```

Metrics:

- request latency
- throughput
- serialization time
- deserialization time
- data-transfer time
- connection overhead
- retry overhead

### Owner

**B1**

---

# 32. Security Testing

Test:

```text
Unauthorized client
Unauthorized node
Invalid credentials
Expired session
Invalid request
Replay attempt
Tampered request
Excessive requests
Revoked node
Quarantined node
Unauthorized object access
```

Verify:

- rejection
- detection
- audit logging
- node quarantine
- node revocation
- scheduler exclusion
- secure communication

### Owner

**M5**

A1, B1, and B2 test the security mechanisms belonging to their respective components.

---

# 33. Performance Model

Total execution time can be approximated as:

```text
T_total =
    T_chunking
  + T_transfer
  + T_allocation
  + T_processing
  + T_aggregation
```

DSM is expected to provide greater benefit when:

- the workload requires large memory
- computation per chunk is significant
- chunks are relatively independent
- communication overhead is relatively small

DSM provides less benefit when:

- chunks communicate frequently
- data transfer dominates computation
- the workload is strongly sequential
- intermediate results are extremely large

---

# 34. Core Design Principles

## Principle 1 — Application Independence

The DSM engine must not contain application-specific logic.

## Principle 2 — Adapter-Based Applications

Application-specific logic belongs inside adapters.

## Principle 3 — Distributed Security

Security mechanisms belong to the layer where they can be implemented most effectively.

```text
A1 → Node security
B1 → Network security
B2 → Scheduling security
M5 → Central security
```

## Principle 4 — Parallel Development

Members must be able to implement their components using interfaces, mocks, and test stubs without waiting for the complete implementation of another component.

## Principle 5 — Component Isolation

Every major component must have a clearly defined interface.

## Principle 6 — Measurable Performance

Performance claims must be supported by measured experiments.

## Principle 7 — Failure Awareness

The system must handle:

```text
Node failure
Network failure
Timeout
Invalid request
Security violation
```

without corrupting DSM state.

## Principle 8 — Extensibility

A future application adapter must be addable without modifying:

```text
DSM Runtime
Object Manager
Directory
Scheduler
Network Layer
Memory Node
```

---

# 35. Final Team Distribution

```text
┌─────────────────────────────────────────────────────────┐
│ A1 — MEMORY NODE                                       │
│                                                         │
│ Memory management                                      │
│ Object storage                                         │
│ Node monitoring                                        │
│ Local security                                         │
│ Capacity experiment                                    │
└─────────────────────────────────────────────────────────┘

┌─────────────────────────────────────────────────────────┐
│ A2 — DSM ABSTRACTION                                   │
│                                                         │
│ DSM API                                                │
│ Object Manager                                         │
│ Directory                                              │
│ Cache                                                  │
│ Consistency                                            │
└─────────────────────────────────────────────────────────┘

┌─────────────────────────────────────────────────────────┐
│ B1 — NETWORK                                            │
│                                                         │
│ TCP                                                   │
│ Protocol                                               │
│ Serialization                                          │
│ TLS                                                    │
│ Transport security                                     │
│ Network benchmarking                                    │
└─────────────────────────────────────────────────────────┘

┌─────────────────────────────────────────────────────────┐
│ B2 — SCHEDULER                                         │
│                                                         │
│ Placement                                              │
│ Load balancing                                         │
│ Resource-aware scheduling                              │
│ Security-aware scheduling                              │
│ Performance scaling                                    │
└─────────────────────────────────────────────────────────┘

┌─────────────────────────────────────────────────────────┐
│ M5 — SECURITY + APPLICATIONS + INTEGRATION             │
│                                                         │
│ Authentication                                         │
│ Authorization                                          │
│ Central security policy                                │
│ Audit/monitoring                                       │
│ Node trust/revocation                                  │
│ Image Processing Adapter                               │
│ Matrix Multiplication Adapter                          │
│ End-to-end integration                                 │
│ System/security testing                                │
└─────────────────────────────────────────────────────────┘
```

The intended distribution is approximately balanced: **A1 gets additional node-level security, B1 gets transport security, B2 gets security-aware scheduling, while M5 retains the application adapters and central security/integration responsibilities.**