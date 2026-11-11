# Week 4 M5 Deliverables

## Adapter benchmark

From the repository root, run:

```text
python -m scripts.benchmark_adapters --repeats 5 --warmups 1
```

The runner uses a fixed seed, identical input sizes for each chunk size, one warmup, and median phase timings. It writes CSV to `benchmarks/week4_adapter_benchmarks.csv` by default. Image tiles and matrix blocks use 64, 128, and 256 by default; input sizes and output path can be overridden on the command line. It checks each result against a local reference and fails if either adapter leaves DSM objects allocated.

The CSV reports chunking, processing, aggregation, and end-to-end wall time separately. It runs against `MockDSMAPI`, so the results isolate adapter-side work and do not represent network, scheduler, or physical-node performance. Keep the generated CSV with the experiment notes; do not compare its wall times to the real multi-machine scaling run.

## Security test harness

`malicious_client` is a function-scoped fixture that exercises bad credentials, expired tokens, replayed nonces, tampered signed tokens, and a request flood against configured component limits. Run it with:

```text
pytest -q tests/security/test_malicious_client.py
```

The tampering case changes a signed token. The current security policy defines token integrity failures as B1's `TAMPER_DETECTED`; it does not define an application payload-integrity check.

## In-process integration checks

The `integration_components` fixture composes A2's real in-process `Runtime` with the existing node, scheduler, and security fakes. Tests run each adapter through that runtime, compare results to local references, verify owner checks were reached, and confirm cleanup. Run them with:

```text
pytest -q tests/integration/test_m5_adapters.py
```

This is an adapter-to-runtime integration rehearsal using in-process component fakes. It does not replace the later real-network and multi-node end-to-end tests.

The local interface review confirms that adapters use the async `DSMAPI` (`alloc`, `read`, `write`, `free`), while the runtime tests exercise its identity-bound handle. Security event reporting is synchronous through the configured audit module, and node identity, audit `actor_id`, and the scheduler's `node_id` share the same identity value. These are repository-level checks; confirmations from A1/B1/B2 and the actual two-laptop run still require team coordination.

## Two-laptop LAN trial

The compose file currently starts `scripts.stub_service`, a TCP reachability stub. It can verify LAN addressing, port publishing, firewall access, and container-to-coordinator connectivity, but it does not exercise DSM, TLS, or node enrollment. Use two laptops on the same reachable network:

1. On the coordinator laptop, allow inbound TCP port `8443` through the OS firewall, then run `docker compose up -d --build coordinator`.
2. Find that laptop's LAN IPv4 address. From the node laptop, verify `Test-NetConnection <coordinator-ip> -Port 8443` succeeds (or use `nc -vz <coordinator-ip> 8443` on Linux/macOS).
3. On the node laptop, run `docker compose run --rm --no-deps -e SECURITY_HOST=<coordinator-ip> -e SECURITY_PORT=8443 node`.
4. Confirm the coordinator logs a `hello from node` line and the node logs its acknowledgement. Stop it with Ctrl+C and run `docker compose down -v` on the coordinator laptop.

Record the date, both machine/OS details, coordinator LAN IP, preflight result, expected/observed log lines, and any firewall or routing changes here after the trial. This physical trial has not been run from the single-machine workspace.
