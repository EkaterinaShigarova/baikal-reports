"""Download exactly two pinned official Qwen 7B shards; never project evidence."""

import argparse
import concurrent.futures
import json
from pathlib import Path
from baikal.core import ROOT, atomic_json
from baikal.evaluation import resources
from baikal.local_models import profile_7b, plan_7b
from download_evaluation_model import download, open_model_url

if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--metadata-only", action="store_true")
    parser.add_argument(
        "--directory", type=Path, default=ROOT / "data/models/qwen25-7b-q4km"
    )
    args = parser.parse_args()
    p = profile_7b()
    api = f"https://huggingface.co/api/models/{p['repo']}/revision/{p['revision']}?blobs=true"
    try:
        with open_model_url(api, timeout=20) as response:
            meta = json.load(response)
        info = plan_7b(meta, resources())
        if not args.metadata_only:
            args.directory.mkdir(parents=True, exist_ok=True)
            atomic_json(args.directory / "download-plan.json", info)
            # Fixed concurrency 2, exact aggregate budget 6 GiB, bounded streaming.
            with concurrent.futures.ThreadPoolExecutor(max_workers=2) as pool:
                jobs = [
                    pool.submit(download, f, args.directory, info["max_total_bytes"])
                    for f in info["files"]
                ]
                paths = [str(job.result()) for job in jobs]
            info["local_paths"] = paths
            atomic_json(args.directory / "bundle.source.json", info)
        print(json.dumps(info, indent=2))
    except (OSError, ValueError) as exc:
        parser.exit(1, f"7B model acquisition failed: {exc}\n")
