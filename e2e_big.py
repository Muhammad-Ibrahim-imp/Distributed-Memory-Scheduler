import asyncio
from network.tcp.dsmapi_stub import RemoteDSMAPI


async def main():
    c = RemoteDSMAPI("localhost", 8443, "big-client", "")
    await c.connect()
    ids = [await c.alloc(5000) for _ in range(6)]
    print("allocated 6 objects of 5000 bytes")
    for oid in ids:
        await c.free(oid)
    print("BIG OK")
    await c.close()


asyncio.run(main())