"""Verify real multi-node placement from node-side operation logs."""

import argparse
import asyncio
from pathlib import Path

from scripts.e2e_common import add_connection_options, clean, connect, run_cli


async def run(args):
    if len(args.node_log) < 2:
        raise ValueError("pass --node-log for at least two node-side logs to verify placement")
    client = await connect(args)
    object_ids = []
    try:
        for _ in range(args.objects):
            object_ids.append(await asyncio.wait_for(client.alloc(args.size), timeout=20))
        for index, object_id in enumerate(object_ids):
            await asyncio.wait_for(client.write(object_id, f"placement-{index}".encode()), timeout=10)
        logs = [Path(path).read_text(encoding="utf-8", errors="replace") for path in args.node_log]
        placements = {
            index for index, log in enumerate(logs)
            if any(f"write {object_id}" in log for object_id in object_ids)
        }
        if len(placements) < 2:
            raise AssertionError(f"allocation IDs appeared in only {len(placements)} node logs")
        print(f"Verified {len(object_ids)} allocations across {len(placements)} node logs")
    finally:
        await clean(client, object_ids)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    add_connection_options(parser, "e2e-multi")
    parser.add_argument("--node-log", action="append", default=[],
                        help="node-side log file, repeated once per node (copy logs locally first)")
    parser.add_argument("--objects", type=int, default=3)
    parser.add_argument("--size", type=int, default=8 * 1024 * 1024)
    args = parser.parse_args()
    if args.objects < 2 or args.size <= 0:
        parser.error("--objects must be at least 2 and --size must be positive")
    return run_cli(run(args))


if __name__ == "__main__":
    raise SystemExit(main())
