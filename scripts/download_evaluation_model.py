"""Optional bounded official GGUF download; never transmits project documents."""

import argparse
import hashlib
import json
import re
import shutil
import urllib.request
from pathlib import Path
from baikal.core import ROOT, atomic_json, sha256
from baikal.evaluation import resources

REPO = "Qwen/Qwen2.5-1.5B-Instruct-GGUF"
FILENAME = "qwen2.5-1.5b-instruct-q4_k_m.gguf"
API = f"https://huggingface.co/api/models/{REPO}?blobs=true"
MAX_BYTES = 2 * 1024**3


def plan(metadata, res):
    if (
        res["memory_limit_bytes"] < 8 * 1024**3
        or res["memory_available_bytes"] < 4 * 1024**3
    ):
        raise ValueError(
            "CPU experiment requires RAM limit >=8 GiB and available RAM >=4 GiB"
        )
    if metadata.get("cardData", {}).get("license") != "apache-2.0":
        raise ValueError(
            "Expected Apache-2.0 model metadata; verify the official model card"
        )
    revision = metadata.get("sha", "")
    entry = next(
        (f for f in metadata.get("siblings", []) if f["rfilename"] == FILENAME), None
    )
    if not entry or not re.fullmatch(r"[0-9a-f]{40}", revision):
        raise ValueError("Official revision/file metadata unavailable")
    lfs = entry.get("lfs", {})
    size = lfs.get("size")
    checksum = lfs.get("sha256", "")
    if (
        type(size) is not int
        or not 0 < size <= MAX_BYTES
        or not re.fullmatch(r"[0-9a-f]{64}", checksum)
    ):
        raise ValueError("Expected bounded file size and published LFS SHA256")
    if res["disk_free_bytes"] < size + 2 * 1024**3:
        raise ValueError("Insufficient disk reserve before model download")
    return {
        "repo": REPO,
        "revision": revision,
        "filename": FILENAME,
        "bytes": size,
        "sha256": checksum,
        "license": "apache-2.0",
        "url": f"https://huggingface.co/{REPO}/resolve/{revision}/{FILENAME}?download=true",
    }


def download(info, directory):
    directory.mkdir(parents=True, exist_ok=True)
    final = directory / FILENAME
    if final.exists():
        if sha256(final) != info["sha256"]:
            raise ValueError("Existing model checksum mismatch; file left unchanged")
        return final
    if shutil.disk_usage(directory).free < info["bytes"] + 2 * 1024**3:
        raise ValueError("Disk reserve changed; download cancelled")
    partial = directory / (FILENAME + ".partial")
    created = False
    try:
        with partial.open("xb") as f:
            created = True
            h = hashlib.sha256()
            total = 0
            with urllib.request.urlopen(info["url"], timeout=60) as response:
                while chunk := response.read(1024 * 1024):
                    total += len(chunk)
                    if total > info["bytes"] or total > MAX_BYTES:
                        raise ValueError("Model exceeded declared resource budget")
                    h.update(chunk)
                    f.write(chunk)
        if total != info["bytes"] or h.hexdigest() != info["sha256"]:
            raise ValueError("Model size or SHA256 differs from published metadata")
        with partial.open("rb") as f:
            if f.read(4) != b"GGUF":
                raise ValueError("Downloaded file is not GGUF")
        partial.replace(final)
        atomic_json(directory / (FILENAME + ".source.json"), info)
        return final
    finally:
        if created and partial.exists():
            partial.unlink()


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--metadata-only", action="store_true")
    parser.add_argument("--directory", type=Path, default=ROOT / "data/models")
    args = parser.parse_args()
    try:
        res = resources()
        with urllib.request.urlopen(API, timeout=20) as response:
            metadata = json.load(response)
        info = plan(metadata, res)
        if not args.metadata_only:
            info["local_path"] = str(download(info, args.directory))
        print(json.dumps(info, ensure_ascii=False, indent=2))
    except (OSError, ValueError) as exc:
        parser.exit(1, f"Model acquisition failed at {API}: {exc}\n")
