import asyncio

MAX_FRAME_SIZE = 65 * 1024 * 1024  # 64 MB payload + 1 MB header budget


async def write_frame(writer: asyncio.StreamWriter, payload: bytes) -> None:
    if len(payload) > MAX_FRAME_SIZE:
        raise ValueError(f"frame too large: {len(payload)}")
    writer.write(len(payload).to_bytes(4, "big") + payload)
    await writer.drain()


async def read_frame(reader: asyncio.StreamReader) -> bytes:
    header = await reader.readexactly(4)
    size = int.from_bytes(header, "big")
    if size > MAX_FRAME_SIZE:
        raise ValueError(f"frame too large: {size}")
    return await reader.readexactly(size)