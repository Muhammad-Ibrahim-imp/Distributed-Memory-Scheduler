import asyncio
from network.tcp.dsmapi_stub import RemoteDSMAPI


async def main():
    c = RemoteDSMAPI("localhost", 8443, "test-client", "")
    await c.connect()
    oid = await c.alloc(16)
    print("allocated:", oid)
    await c.write(oid, b"hello")
    print("read back:", await c.read(oid))
    await c.free(oid)
    print("E2E OK")
    await c.close()


asyncio.run(main())