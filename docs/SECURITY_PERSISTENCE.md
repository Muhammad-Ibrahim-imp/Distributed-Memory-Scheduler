# Coordinator security persistence

The coordinator uses SQLite at `data/security/security.db` (configurable through
`persistence.database_path` in `configs/security.yaml`). The existing
`coordinator-data` Docker volume mounts `/app/data`, so the database and the
legacy rotating `data/audit.jsonl` survive container replacement.

SQLite stores identities and credential hashes, identity revocation status,
node trust states and transition reasons, authentication/rate-limit violation
timestamps, persistent node-certificate bans, audit events, and the database
schema version. Credential plaintext, Fernet keys, tokens, sessions, nonce and
rate-limit runtime buckets, connections, heartbeats, and resource statistics
are not stored. `DSM_TOKEN_KEY` remains the configured token key; when absent,
the existing development behavior creates an ephemeral key.

The in-memory session registry is intentionally not restored. Token validation
requires a live session record, so all tokens from before a restart fail even
when a configured Fernet key remains unchanged. Identities can authenticate
again with their long-lived credential, subject to persisted revocation and
trust checks. Node registries start empty and only authenticated/reconnected
nodes with current resource information are returned to the scheduler. Trust
state remains independent of connection availability.

Trust transitions update the trust row and corresponding audit event in one
SQLite transaction. Other audit events are written to SQLite and the existing
rotating JSONL log. SQLite uses WAL, `synchronous=FULL`, foreign keys, a
single-process connection guarded by a lock, and zero lock-wait timeout. A
database failure raises `SecurityPersistenceError`; startup fails before the
network server starts, and a failed write is not acknowledged. A marker file
detects a database that disappears after initialization. Schema version 1 is
validated; unsupported versions fail startup rather than being reset.

Object ownership remains A2's DSM metadata and is not duplicated here. The
current M5 code has no independent role/permission ACL policy to persist. The
existing node enrollment certificate denylist remains compatible as a JSON
file and is additionally copied into SQLite when a revocation occurs.

This persists security decisions only. It does not persist DSM objects,
application data, memory-node RAM, or Directory state, and it does not provide
coordinator high availability. SQLite calls remain synchronous because the
current authentication and trust APIs are synchronous; transactions are kept
short and have no lock wait. If storage latency or lock contention becomes
material, M5 and B1 should agree on an async authentication boundary before
moving database work to a worker thread.

## Integration contracts

- **A1:** continue reporting node security events through the audit API; node
  quotas and memory remain A1-owned and ephemeral unless A1 defines durable
  sanctions through the shared event contract.
- **A2:** continue owning object-owner metadata and Directory contents. No DSM
  object ownership is copied into the security database. On REVOKED, M5 calls
  B1's connection-close function, then invokes A2's registered
  `handle_node_revoked(node_id, timestamp)` callback in process.
- **B1:** reconnect nodes and clients after coordinator restart; sessions from
  before restart are invalid. Transport replay caches and active connections
  remain process-local.
- **B2:** use the existing trust-state provider. Restored REVOKED and
  QUARANTINED states remain excluded; a node absent from the fresh registry is
  unavailable until it reconnects and reports health.
