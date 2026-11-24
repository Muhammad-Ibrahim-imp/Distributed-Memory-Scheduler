"""Minimal generic-DSM adapter that returns a stored payload's SHA-256."""

from __future__ import annotations

import hashlib

from common.interfaces.application_adapter import ApplicationAdapter, PreparedWorkload


class ChecksumAdapter(ApplicationAdapter):
    async def prepare(self, input_data: bytes) -> PreparedWorkload:
        if not isinstance(input_data, bytes):
            raise TypeError("checksum input must be bytes")
        workload = PreparedWorkload(object_ids=[])
        try:
            object_id = await self.dsm.alloc(len(input_data) or 1)
            workload.object_ids.append(object_id)
            await self.dsm.write(object_id, input_data)
            return workload
        except Exception:
            await self.cleanup(workload)
            raise

    async def execute(self, workload: PreparedWorkload) -> PreparedWorkload:
        if len(workload.object_ids) != 1:
            raise ValueError("checksum workload must contain one object")
        return workload

    async def collect_result(self, workload: PreparedWorkload) -> str:
        payload = await self.dsm.read(workload.object_ids[0])
        return hashlib.sha256(payload).hexdigest()
