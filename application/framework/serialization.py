"""Self-describing array <-> bytes for DSM objects (DSM stores opaque bytes).

Layout: b"DSMA" | ver:u8 | dtype_len:u8 | dtype_str | ndim:u8 | shape:u64*ndim | raw C-order data
No pickle: payloads come back from remote nodes and must be treated as untrusted.
"""
from __future__ import annotations

import struct

import numpy as np

from application.framework.errors import InvalidInputError

_MAGIC = b"DSMA"
_VERSION = 1
_MAX_NDIM = 8


def pack_array(arr: np.ndarray) -> bytes:
    if arr.dtype.hasobject or arr.dtype.kind in "OV":
        raise InvalidInputError(f"unsupported dtype {arr.dtype}")
    if arr.ndim > _MAX_NDIM:
        raise InvalidInputError("too many dimensions")
    dt = arr.dtype.str.encode("ascii")
    head = _MAGIC + struct.pack("<BB", _VERSION, len(dt)) + dt
    head += struct.pack("<B", arr.ndim) + struct.pack(f"<{arr.ndim}Q", *arr.shape)
    return head + np.ascontiguousarray(arr).tobytes()


def unpack_array(data: bytes, copy: bool = True) -> np.ndarray:
    try:
        if data[:4] != _MAGIC:
            raise InvalidInputError("bad magic")
        ver, dlen = struct.unpack_from("<BB", data, 4)
        if ver != _VERSION:
            raise InvalidInputError(f"unsupported version {ver}")
        off = 6
        dtype = np.dtype(data[off:off + dlen].decode("ascii"))
        off += dlen
        if dtype.hasobject or dtype.kind in "OV":
            raise InvalidInputError(f"unsupported dtype {dtype}")
        (ndim,) = struct.unpack_from("<B", data, off)
        off += 1
        if ndim > _MAX_NDIM:
            raise InvalidInputError("too many dimensions")
        shape = struct.unpack_from(f"<{ndim}Q", data, off)
        off += 8 * ndim
    except InvalidInputError:
        raise
    except (struct.error, TypeError, ValueError, UnicodeError) as exc:
        raise InvalidInputError(f"corrupt array payload: {exc}") from None
    expected = int(np.prod(shape, dtype=object)) * dtype.itemsize if shape else dtype.itemsize
    if len(data) - off != expected:
        raise InvalidInputError(f"payload length {len(data) - off} != expected {expected}")
    arr = np.frombuffer(data, dtype=dtype, offset=off).reshape(shape)
    return arr.copy() if copy else arr