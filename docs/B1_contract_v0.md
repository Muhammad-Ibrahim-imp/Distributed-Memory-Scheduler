\# B1 Contract v0



\## Wire format

\- 4-byte big-endian length prefix + protobuf payload

\- Max frame: 64 MB + 1 MB header

\- Messages: `common/protocol/dsm.proto`



\## Envelope

Every request: `protocol\_version`, `identity`, `token`, `nonce`, `timestamp`.



\## Units

\- latency: ms

\- bandwidth: Mbps

\- sizes: bytes

\- timestamps: float seconds



\## Error codes

`OBJECT\_NOT\_FOUND`, `ACCESS\_DENIED`, `OBJECT\_UNAVAILABLE`, `CAPACITY\_EXCEEDED`,

`QUOTA\_EXCEEDED`, `INTEGRITY\_FAILURE`, `INVALID\_REQUEST`, `TIMEOUT`,

`AUTH\_REJECTED`, `TOKEN\_INVALID`, `TOKEN\_EXPIRED`, `RATE\_LIMITED`



\## Pending

\- `AllocateRequest`: awaiting A1/A2 confirmation. Currently absent.



\## Not yet implemented (weeks 2–5)

TLS, replay, rate limit, heartbeats, NetworkStateProvider, benchmarks.

