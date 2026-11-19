import asyncio
from network.tcp.dsmapi_stub import RemoteDSMAPI


async def main():
    c = RemoteDSMAPI("localhost", 8443, "multi-client", "")
    await c.connect()
    ids = []
    for i in range(8):
        oid = await c.alloc(16)
        ids.append(oid)
        print("allocated:", oid)
    for oid in ids:
        await c.free(oid)
    print("MULTI OK (8 alloc + 8 free)")
    await c.close()


asyncio.run(main())