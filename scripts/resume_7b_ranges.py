"""Continue this task's pinned shards with four bounded HTTP ranges; no retries."""

import argparse
import json
import shutil
from pathlib import Path
from baikal.core import ROOT, read_json
from baikal.local_models import profile_7b, check_budget
from baikal.evaluation import resources
from baikal.range_download import resume_bundle
from download_evaluation_model import open_model_url

if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--directory", type=Path, default=ROOT / "data/models/qwen25-7b-q4km"
    )
    args = parser.parse_args()
    try:
        info = read_json(args.directory / "download-plan.json", {})
        p = profile_7b()
        for key in [
            "repo",
            "revision",
            "license",
            "max_total_bytes",
            "disk_reserve_bytes",
        ]:
            if info.get(key) != p[key]:
                raise ValueError("Pinned acquisition plan mismatch")
        if len(info.get("files", [])) != 2:
            raise ValueError("Exactly two shards required")
        for actual, expected in zip(info["files"], p["files"]):
            if any(actual.get(k) != v for k, v in expected.items()):
                raise ValueError("Shard plan mismatch")
            expected_url = f"https://huggingface.co/{p['repo']}/resolve/{p['revision']}/{expected['filename']}?download=true"
            if actual.get("url") != expected_url:
                raise ValueError("Pinned source URL mismatch")
        check_budget(p, resources(), sum(f["bytes"] for f in info["files"]))
        resume_bundle(
            info, args.directory, open_model_url, shutil.disk_usage(args.directory).free
        )
        print(
            json.dumps(
                {
                    "status": "verified",
                    "files": p["files"],
                    "total_bytes": info["total_bytes"],
                }
            )
        )
    except (OSError, ValueError) as exc:
        parser.exit(1, f"Range acquisition stopped: {exc}\n")
