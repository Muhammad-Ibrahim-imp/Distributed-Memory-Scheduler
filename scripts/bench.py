import sys
import os
sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), '..')))

import asyncio
import time
import statistics
from common.protocol import dsm_pb2
from network.security.tls import server_context, client_context
from network.tcp.connection import Connection
from network.serialization.framing import read_frame, write_frame

PAYLOAD_SIZES = [1024, 64 * 1024, 1024 * 1024] # 1KB, 64KB, 1MB
ITERATIONS = 50

async def echo_server(reader, writer):
    try:
        while True:
            raw = await read_frame(reader)
            # Simulate minimal processing
            await write_frame(writer, raw)
    except (asyncio.IncompleteReadError, ConnectionError):
        pass

async def run_bench():
    if not os.path.exists("certs/server.pem"):
        print("Error: Run 'python scripts/gen_certs.py' first.")
        return

    print("Starting local TLS echo server...")
    srv = await asyncio.start_server(echo_server, "127.0.0.1", 0, ssl=server_context())
    port = srv.sockets[0].getsockname()[1]

    # 1. Connection Overhead
    t0 = time.perf_counter()
    reader, writer = await asyncio.open_connection(
        "127.0.0.1", port, ssl=client_context(), server_hostname="coordinator"
    )
    conn = Connection(reader, writer)
    conn.start()
    conn_overhead_ms = (time.perf_counter() - t0) * 1000
    print(f"\nConnection Overhead (TLS handshake + TCP): {conn_overhead_ms:.2f} ms")

    print(f"\n{'Size (bytes)':<15} | {'Ser (ms)':<10} | {'Deser (ms)':<12} | {'RTT (ms)':<10} | {'Throughput (MB/s)':<15}")
    print("-" * 75)

    for size in PAYLOAD_SIZES:
        ser_times, deser_times, rtts = [], [], []
        payload = b"x" * size
        
        for i in range(ITERATIONS):
            env = dsm_pb2.Envelope()
            env.write.request_id = f"req-{i}"
            env.write.data = payload

            # Serialize
            t0 = time.perf_counter()
            raw = env.SerializeToString()
            ser_times.append((time.perf_counter() - t0) * 1000)

            # Network RTT
            t0 = time.perf_counter()
            await write_frame(writer, raw)
            resp_raw = await read_frame(reader)
            rtts.append((time.perf_counter() - t0) * 1000)

            # Deserialize
            t0 = time.perf_counter()
            resp = dsm_pb2.Envelope()
            resp.ParseFromString(resp_raw)
            deser_times.append((time.perf_counter() - t0) * 1000)

        # Throughput calculation (Total bytes sent+received / total RTT time)
        total_bytes = len(raw) * ITERATIONS * 2
        total_time_sec = sum(rtts) / 1000
        mbps = (total_bytes / total_time_sec) / 1_000_000

        print(f"{size:<15} | {statistics.mean(ser_times):<10.3f} | {statistics.mean(deser_times):<12.3f} | {statistics.mean(rtts):<10.2f} | {mbps:<15.2f}")

    await conn.close()
    srv.close()
    await srv.wait_closed()

if __name__ == "__main__":
    asyncio.run(run_bench())
