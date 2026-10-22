"""Check cited evidence against cached pages; never opens/reprocesses PDFs."""

import argparse
import json
from baikal.encyclopedia import check_evidence

if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument(
        "--require-local",
        action="store_true",
        help="Fail if cited cached page is unavailable",
    )
    args = parser.parse_args()
    result = check_evidence(require_local=args.require_local)
    print(json.dumps(result, ensure_ascii=False, indent=2))
    raise SystemExit(bool(result["errors"]))
