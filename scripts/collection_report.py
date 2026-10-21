"""Rebuild compact coverage metrics from checkpoints; optionally verify original hashes."""

import argparse
from baikal.core import (
    DATA,
    ROOT,
    load_sources,
    page_dir,
    read_json,
    atomic_json,
    sha256,
)


def report(verify_hashes=False):
    results = []
    seen = {}
    for s in load_sources()["sources"]:
        if not s.get("sha256"):
            continue
        dest = page_dir(s)
        summary = read_json(dest / "summary.json")
        if not summary:
            raise ValueError(f"Missing summary: {s['id']}")
        pages = [read_json(p) for p in sorted((dest / "pages").glob("*.json"))]
        if {p["page"] for p in pages} != set(range(1, s["pages"] + 1)):
            raise ValueError(f"Incomplete page checkpoint coverage: {s['id']}")
        if verify_hashes and sha256(DATA / "raw" / f"{s['sha256']}.pdf") != s["sha256"]:
            raise ValueError(f"Original checksum mismatch: {s['id']}")
        r = {
            k: s.get(k)
            for k in [
                "id",
                "name",
                "report_year",
                "document_type",
                "sha256",
                "bytes",
                "pages",
                "authenticity",
            ]
        }
        r.update(
            {
                k: summary[k]
                for k in [
                    "extracted_pages",
                    "text_pages",
                    "ocr_pages",
                    "review_pages",
                    "tables",
                    "images",
                    "numeric_candidates",
                    "failed_pages",
                    "ocr_failures",
                ]
            }
        )
        results.append(r)
        seen.setdefault(s["sha256"], []).append(s["id"])
    atomic_json(ROOT / "datasets/processing_summary.json", results)
    totals = {
        k: sum(r[k] for r in results)
        for k in [
            "pages",
            "extracted_pages",
            "text_pages",
            "ocr_pages",
            "tables",
            "images",
            "numeric_candidates",
        ]
    }
    return dict(
        documents=len(results),
        unique_hashes=len(seen),
        duplicate_groups=[v for v in seen.values() if len(v) > 1],
        totals=totals,
    )


if __name__ == "__main__":
    import json

    parser = argparse.ArgumentParser()
    parser.add_argument("--verify-hashes", action="store_true")
    args = parser.parse_args()
    print(json.dumps(report(args.verify_hashes), ensure_ascii=False, indent=2))
