"""Wiring check for the Docker scaffold. Replace with the real services later.

SERVICE_ROLE=coordinator  listens on LISTEN_PORT and answers every connection.
Any other role            connects to SECURITY_HOST:SECURITY_PORT every 10 seconds
                          and logs the answer, which proves Docker DNS and the
                          published port work.
"""

import asyncio
import os
import socket

ROLE = os.environ.get("SERVICE_ROLE", "unknown")
HOST = socket.gethostname()


async def run_coordinator(port: int) -> None:
    async def handle(reader: asyncio.StreamReader, writer: asyncio.StreamWriter) -> None:
        line = (await reader.readline()).decode().strip()
        print(f"[coordinator] received: {line}", flush=True)
        writer.write(f"ack from coordinator to: {line}\n".encode())
        await writer.drain()
        writer.close()

    server = await asyncio.start_server(handle, "0.0.0.0", port)
    print(f"[coordinator] listening on {port}", flush=True)
    async with server:
        await server.serve_forever()


async def run_peer(host: str, port: int) -> None:
    while True:
        try:
            reader, writer = await asyncio.open_connection(host, port)
            writer.write(f"hello from {ROLE} ({HOST})\n".encode())
            await writer.drain()
            answer = (await reader.readline()).decode().strip()
            print(f"[{ROLE}] {answer}", flush=True)
            writer.close()
        except OSError as exc:
            print(f"[{ROLE}] cannot reach {host}:{port}: {exc}", flush=True)
        await asyncio.sleep(10)


async def main() -> None:
    if ROLE == "coordinator":
        await run_coordinator(int(os.environ.get("LISTEN_PORT", "8443")))
    else:
        await run_peer(os.environ.get("SECURITY_HOST", "coordinator"),
                       int(os.environ.get("SECURITY_PORT", "8443")))


if __name__ == "__main__":
    asyncio.run(main())