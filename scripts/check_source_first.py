"""Check source-first provenance and local raw records, not scientific truth."""

import argparse
import hashlib
import json

from baikal.core import ROOT, read_json, sha256
from baikal.updates import digest


def check(root=ROOT, require_local=False):
    report = read_json(root / "datasets/source_first_evaluation.json")
    errors, missing = [], []
    fragments_checked = runs_checked = 0
    for name, expected in [
        (report["protocol_path"], report["protocol_sha256"]),
        ("baikal/source_first.py", report["source_first_module_sha256"]),
        ("scripts/evaluate_source_first.py", report["runner_sha256"]),
        ("datasets/llm_7b_comparison.json", report["baseline_report_sha256"]),
    ]:
        if not (root / name).is_file() or sha256(root / name) != expected:
            errors.append(f"Committed provenance mismatch: {name}")
    cfg = {s["id"]: s for s in read_json(root / "config/sources.json")["sources"]}
    previous = read_json(root / "datasets/llm_7b_comparison.json")
    for case in report["cases"]:
        if sha256(root / case["article_path"]) != case["article_sha256"]:
            errors.append("Article changed since source-first experiment")
        for ref in case["source_fragments"]:
            current = cfg.get(ref["source_id"], {})
            if current.get("sha256") != ref["sha256"]:
                errors.append("Source revision mismatch")
            source_page = root / "wiki/sources" / f"{ref['source_id']}.md"
            if (
                not source_page.is_file()
                or f'id="page-{ref["page"]}"' not in source_page.read_text()
            ):
                errors.append("Missing source Wiki anchor")
            cache = (
                root
                / "data/processed"
                / ref["sha256"]
                / "pages"
                / f"{ref['page']:04d}.json"
            )
            if not cache.is_file():
                missing.append(str(cache.relative_to(root)))
            elif ref["quote"] not in read_json(cache).get("text", ""):
                errors.append("Cached exact quote mismatch")
            else:
                fragments_checked += 1
        path = root / case["raw_run_path"]
        if not path.is_file():
            missing.append(case["raw_run_path"])
            continue
        run = read_json(path)
        if (
            sha256(path) != case["raw_run_file_sha256"]
            or digest(run) != case["raw_run_id"]
        ):
            errors.append("Raw run integrity mismatch")
        if (
            hashlib.sha256(run["raw_response"].encode()).hexdigest()
            != case["raw_response_sha256"]
            or digest(run["request"]) != case["request_sha256"]
        ):
            errors.append("Response/request hash mismatch")
        if (
            run["parsed"]["facts"] != [a["model_record"] for a in case["fact_audit"]]
            or run["errors"] != case["programmatic_errors"]
        ):
            errors.append("Audit differs from original model output")
        user = json.loads(run["request"]["messages"][1]["content"])
        if set(user) != {"source_fragments"}:
            errors.append("Source-only isolation violated")
        old = next(c for c in previous["cases"] if c["topic"] == case["topic"])
        old_path = root / "data/wiki_updates/llm_runs" / (old["run_id"] + ".json")
        if not old_path.is_file():
            missing.append(str(old_path.relative_to(root)))
        else:
            old_run = read_json(old_path)
            if sha256(old_path) != old["run_record_sha256"]:
                errors.append("Previous controlled run changed")
            if (
                user["source_fragments"]
                != json.loads(old_run["request"]["messages"][1]["content"])[
                    "source_fragments"
                ]
            ):
                errors.append("Source fragments differ from frozen baseline")
        runs_checked += 1
    manifest_path = root / report["manifest_path"]
    if not manifest_path.is_file():
        missing.append(report["manifest_path"])
    elif (
        sha256(manifest_path) != report["manifest_file_sha256"]
        or digest(read_json(manifest_path)) != report["experiment_id"]
    ):
        errors.append("Experiment manifest integrity mismatch")
    attempts = (
        root
        / "data/llm-evaluation"
        / f"source-first-{report['experiment_id']}.attempts"
    )
    if not attempts.is_dir():
        missing.append(str(attempts.relative_to(root)))
    elif {p.name for p in attempts.glob("*.json")} != {
        f"{c['topic']}-extract.json" for c in report["cases"]
    }:
        errors.append("Unexpected generation slots: comparison must remain blocked")
    if require_local and missing:
        errors.append(f"Required local artifacts unavailable: {len(missing)}")
    return {
        "errors": errors,
        "missing_local_artifacts": missing,
        "exact_source_fragments_checked_locally": fragments_checked,
        "raw_runs_checked_locally": runs_checked,
        "semantic_truth_evaluated_by_this_script": False,
    }


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--require-local", action="store_true")
    args = parser.parse_args()
    result = check(require_local=args.require_local)
    print(json.dumps(result, indent=2))
    raise SystemExit(bool(result["errors"]))
