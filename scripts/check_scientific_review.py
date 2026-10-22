"""Validate AI review provenance; never upgrades to expert review or processes PDFs."""

import argparse
import json
from baikal.scientific_review import validate

if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--require-local", action="store_true")
    args = parser.parse_args()
    report = validate(require_local=args.require_local)
    print(json.dumps(report, ensure_ascii=False, indent=2))
    raise SystemExit(bool(report["errors"]))
