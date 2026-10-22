"""Pinned, bounded model profiles. No downloading or model inference on import."""

from pathlib import Path
from .core import ROOT, read_json, sha256


def profile_7b():
    return read_json(ROOT / "config/llm_7b_profile.json")


def check_budget(profile, res, needed_bytes):
    if (
        res["memory_limit_bytes"] < profile["memory_limit_min_bytes"]
        or res["memory_available_bytes"] < profile["memory_available_min_bytes"]
    ):
        raise ValueError("7B requires RAM limit and available RAM >=16 GiB")
    if needed_bytes > profile["max_total_bytes"] or needed_bytes <= 0:
        raise ValueError("7B bundle exceeds fixed 6 GiB budget")
    if res["disk_free_bytes"] < needed_bytes + profile["disk_reserve_bytes"]:
        raise ValueError("Insufficient disk reserve for 7B")


def plan_7b(metadata, res):
    p = profile_7b()
    if (
        metadata.get("sha") != p["revision"]
        or metadata.get("id") != p["repo"]
        or metadata.get("cardData", {}).get("license") != p["license"]
    ):
        raise ValueError("Official repository/revision/Apache-2.0 metadata mismatch")
    files = {f["rfilename"]: f for f in metadata.get("siblings", [])}
    for expected in p["files"]:
        actual = files.get(expected["filename"], {}).get("lfs", {})
        if (
            actual.get("size") != expected["bytes"]
            or actual.get("sha256") != expected["sha256"]
        ):
            raise ValueError("Published shard size/SHA256 mismatch")
    total = sum(f["bytes"] for f in p["files"])
    check_budget(p, res, total)
    return {
        **p,
        "total_bytes": total,
        "files": [
            {
                **f,
                "repo": p["repo"],
                "revision": p["revision"],
                "license": p["license"],
                "url": f"https://huggingface.co/{p['repo']}/resolve/{p['revision']}/{f['filename']}?download=true",
            }
            for f in p["files"]
        ],
    }


def verify_7b(first, res):
    p = profile_7b()
    check_budget(p, res, sum(f["bytes"] for f in p["files"]))
    first = Path(first).resolve()
    if first.name != p["files"][0]["filename"]:
        raise ValueError("7B must start with pinned first shard")
    verified = []
    for expected in p["files"]:
        path = first.parent / expected["filename"]
        if (
            path.stat().st_size != expected["bytes"]
            or sha256(path) != expected["sha256"]
        ):
            raise ValueError(f"7B shard size/SHA256 mismatch: {path.name}")
        with path.open("rb") as stream:
            if stream.read(4) != b"GGUF":
                raise ValueError("7B shard is not GGUF")
        verified.append(expected)
    return {
        "profile": p["id"],
        "repo": p["repo"],
        "revision": p["revision"],
        "license": p["license"],
        "files": verified,
        "total_bytes": sum(f["bytes"] for f in verified),
    }
