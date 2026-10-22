"""Verify unpublished drafts and their version-bound source review."""
import argparse
import json
from baikal.draft_review import validate

if __name__=='__main__':
    p=argparse.ArgumentParser(description=__doc__)
    p.add_argument('--manifest',default='datasets/editorial_draft_provenance.json')
    p.add_argument('--require-local',action='store_true')
    args=p.parse_args()
    try:print(json.dumps(validate(manifest_path=args.manifest,require_local=args.require_local),ensure_ascii=False,indent=2))
    except (ValueError,OSError,KeyError) as exc:p.exit(1,f'Editorial provenance check failed: {exc}\n')
