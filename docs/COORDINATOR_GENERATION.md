# Coordinator generation contract

The coordinator lifecycle owner supplies a non-empty `coordinator_generation`
string when constructing `Runtime`, or calls `Runtime.start_generation()` when
activating a later generation. A generation must not contain `.` because A2
uses that delimiter in generation-qualified object IDs.

A2 IDs have the form `<coordinator_generation>.<uuid4>`. The ID remains an
opaque `str` to clients. A2 records the generation in each `ObjectRecord`;
Directory activation clears records, locks, and versions, and Object Manager
activation clears the object cache and per-node revocation bookkeeping.
Therefore an older ID cannot alias an object allocated in a later generation,
even if the UUID portion is repeated.

Reads and writes of obsolete IDs raise `ObjectNotFoundError`. Freeing an
obsolete-generation ID also raises `ObjectNotFoundError`; freeing an unknown or
already-freed ID from the active generation remains idempotent.

`RequestContext.coordinator_generation` is the A2-to-B1 NodeClient contract.
Runtime contexts always carry the active generation. B1 must place this value
in the authenticated coordinator-to-node protocol, and A1 must use it to
securely reclaim allocations from older generations. The protobuf/wire change
and node-side reclamation are owned by B1 and A1 respectively; this change
does not implement either.

A temporary request timeout or network disconnection does not rotate the
generation or remove Directory state. Explicit node failure handling marks
objects unavailable and invalidates their cached values; trust transitions
continue to apply the existing quarantine/revocation policy. A failed write
does not advance the version and invalidates the cache. A failed free retains
Directory/version metadata for retry and invalidates cached bytes.
Free is denied for a revoked node by SECURITY_POLICY.md section 6.3; its
Directory metadata remains until generation-aware cleanup can safely reclaim it.
Network integrations must distinguish temporary disconnection from confirmed
node failure and call `handle_node_failure()` only for the latter.

This lifecycle is generation invalidation, not persistent Directory recovery,
replication, or distributed crash recovery.
