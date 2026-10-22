"""Bounded HTTP-range continuation; whole-file SHA256 remains authoritative."""

import concurrent.futures
import hashlib
import threading
from pathlib import Path
from .core import atomic_json, read_json, sha256

CHUNK_BYTES = 256 * 1024**2


def fetch_range(info, start, end, target, opener, cancelled):
    if target.exists():
        if target.stat().st_size != end - start + 1:
            raise ValueError("Existing range size mismatch; left unchanged")
        return
    partial = target.with_name(target.name + ".part")
    created = False
    try:
        with partial.open("xb") as out:
            created = True
            with opener(
                info["url"], timeout=60, headers={"Range": f"bytes={start}-{end}"}
            ) as response:
                expected = f"bytes {start}-{end}/{info['bytes']}"
                if (
                    response.status != 206
                    or response.headers.get("Content-Range") != expected
                ):
                    raise ValueError(
                        "Server did not honor exact HTTP range; download stopped"
                    )
                count = 0
                while data := response.read(1024**2):
                    if cancelled.is_set():
                        raise ValueError(
                            "Companion range failed; acquisition cancelled"
                        )
                    count += len(data)
                    if count > end - start + 1:
                        raise ValueError("Range exceeded declared byte budget")
                    out.write(data)
            if count != end - start + 1:
                raise ValueError("Incomplete HTTP range")
        partial.replace(target)
    finally:
        if created and partial.exists():
            partial.unlink()


def resume_bundle(info, directory, opener, free_bytes):
    directory = Path(directory)
    total_bytes = sum(f["bytes"] for f in info["files"])
    if (
        len(info["files"]) != 2
        or info["total_bytes"] != total_bytes
        or total_bytes > 6 * 1024**3
    ):
        raise ValueError("Only the bounded two-shard 7B bundle is supported")
    # Prefix + ranges + assembled GGUF may coexist; budget both copies explicitly.
    if free_bytes < 2 * total_bytes + info["disk_reserve_bytes"]:
        raise ValueError(
            "Insufficient disk for range continuation and assembly reserve"
        )
    state_path = directory / "range-plan.json"
    state = read_json(state_path, {})
    planned = []
    jobs = []
    for f in info["files"]:
        name = f["filename"]
        final = directory / name
        if final.exists():
            if final.stat().st_size != f["bytes"] or sha256(final) != f["sha256"]:
                raise ValueError("Existing model mismatch; left unchanged")
            continue
        prefix = directory / (name + ".prefix")
        original = directory / (name + ".partial")
        if not prefix.exists():
            if original.exists():
                original.rename(prefix)
            else:
                prefix.touch(exist_ok=False)
        prefix_size = prefix.stat().st_size
        if prefix_size > f["bytes"]:
            raise ValueError("Prefix exceeds pinned shard size")
        prefix_hash = sha256(prefix)
        saved = state.get(name)
        if saved and saved != {"bytes": prefix_size, "sha256": prefix_hash}:
            raise ValueError("Saved prefix changed; left unchanged")
        state[name] = {"bytes": prefix_size, "sha256": prefix_hash}
        chunks = []
        for start in range(prefix_size, f["bytes"], CHUNK_BYTES):
            end = min(start + CHUNK_BYTES, f["bytes"]) - 1
            path = directory / f"{name}.range-{start}-{end}"
            chunks.append(path)
            jobs.append((f, start, end, path))
        planned.append((f, prefix, chunks))
    atomic_json(state_path, state)
    cancelled = threading.Event()
    with concurrent.futures.ThreadPoolExecutor(max_workers=4) as pool:
        futures = [pool.submit(fetch_range, *job, opener, cancelled) for job in jobs]
        try:
            for future in concurrent.futures.as_completed(futures):
                future.result()
        except Exception:
            cancelled.set()
            for future in futures:
                future.cancel()
            raise
    for f, prefix, chunks in planned:
        final = directory / f["filename"]
        partial = directory / (f["filename"] + ".assembling")
        created = False
        try:
            with partial.open("xb") as out:
                created = True
                digest = hashlib.sha256()
                total = 0
                for path in [prefix] + chunks:
                    with path.open("rb") as inp:
                        while data := inp.read(1024**2):
                            total += len(data)
                            if total > f["bytes"]:
                                raise ValueError("Assembly exceeded declared size")
                            digest.update(data)
                            out.write(data)
            if total != f["bytes"] or digest.hexdigest() != f["sha256"]:
                raise ValueError(
                    "Assembled shard size/SHA256 mismatch; prefix and ranges retained"
                )
            with partial.open("rb") as stream:
                if stream.read(4) != b"GGUF":
                    raise ValueError("Not GGUF")
            partial.replace(final)
            atomic_json(directory / (f["filename"] + ".source.json"), f)
        finally:
            if created and partial.exists():
                partial.unlink()
    atomic_json(
        directory / "bundle.source.json",
        {**info, "acquisition": "bounded_ranges", "max_connections": 4},
    )
