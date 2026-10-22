"""Check registry, benchmark and experiment provenance, not expert validity."""

import argparse
import hashlib
import json

from baikal.core import ROOT, read_json, sha256
from baikal.claim_matching import (
    validate_registry,
    validate_benchmark,
    structural_compare,
    comparison_input,
    check_response,
)
from baikal.updates import digest


def verify(root=ROOT, require_local=False):
    registry = read_json(root / "datasets/selenga_claim_registry.json")
    benchmark = read_json(root / "datasets/claim_comparison_benchmark.json")
    validate_registry(registry, root, require_local=require_local)
    counts = validate_benchmark(benchmark, registry, root)
    cases = {c["case_id"]: c for c in benchmark["examples"]}
    output = {
        "errors": [],
        "registry_claims": len(registry["claims"]),
        "expected_counts": counts,
        "expert_review": False,
    }
    report_path = root / "datasets/claim_matching_evaluation.json"
    if not report_path.exists():
        return {**output, "actual_experiment_report_present": False}
    report = read_json(report_path)
    for path, h in report["pins"].items():
        if sha256(root / path) != h:
            raise ValueError(f"Pinned input/code changed: {path}")
    review = report["semantic_review"]["decision"]
    if (
        digest(review) != report["semantic_review"]["id"]
        or review["expert_review"] is not False
        or review["review_kind"] != "semantic_comparison_non_expert"
    ):
        raise ValueError("Separate review identity/status changed")
    review_path = root / report["semantic_review"]["path"]
    if require_local or review_path.exists():
        if (
            read_json(review_path) != review
            or sha256(review_path) != report["semantic_review"]["file_sha256"]
        ):
            raise ValueError("Separate actual review file changed")
    correct = 0
    model_count = 0
    checked_local = 0
    if {r["case_id"] for r in report["case_results"]} != set(cases):
        raise ValueError("Case inventory mismatch")
    for result in report["case_results"]:
        case = cases[result["case_id"]]
        if result["expected_classification"] != case["expected"]["classification"]:
            raise ValueError("Preregistered expected label changed in the report")
        route = structural_compare(case, registry)
        if route != result["structural_route"]:
            raise ValueError("Structural routing changed")
        if route["route"] == "deterministic":
            actual = route["classification"]
        else:
            model_count += 1
            model = result["model"]
            if review["cases"][result["case_id"]]["run_id"] != model["run_id"]:
                raise ValueError("Semantic review refers to a different model response")
            if model["semantic_review"] != review["cases"][result["case_id"]]:
                raise ValueError("Reported per-case semantic review changed")
            parsed = model["parsed"]
            if model["errors"]:
                actual = None
            else:
                check_response(parsed, comparison_input(case, registry))
                actual = parsed["classification"]
            path = root / model["path"]
            if require_local or path.exists():
                run = read_json(path)
                if (
                    digest(run) != model["run_id"]
                    or sha256(path) != model["file_sha256"]
                    or run["parsed"] != parsed
                    or run["errors"] != model["errors"]
                    or hashlib.sha256((run["raw_response"] or "").encode()).hexdigest()
                    != model["raw_response_sha256"]
                ):
                    raise ValueError("Actual model response changed")
                if json.loads(
                    run["request"]["messages"][1]["content"]
                ) != comparison_input(case, registry):
                    raise ValueError("Model context differs from benchmark")
                checked_local += 1
        is_correct = actual == case["expected"]["classification"] and (
            route["route"] == "deterministic"
            or model["semantic_review"]["status"] == "supported"
        )
        if result["actual_classification"] != actual or result["correct"] != is_correct:
            raise ValueError("Reported classification/count mismatch")
        correct += is_correct
    if (
        report["summary"]["correct"] != correct
        or report["summary"]["incorrect"] != len(cases) - correct
        or report["published"] is not False
        or report["expert_review"] is not False
    ):
        raise ValueError("Misreported results or review status")
    candidate = report["candidate_packet"]
    if candidate is not None:
        path = root / candidate["path"]
        if require_local or path.exists():
            packet = read_json(path)
            if (
                digest(packet) != candidate["id"]
                or packet["published"] is not False
                or packet["expert_review"] is not False
                or len(packet["candidates"]) != candidate["candidates"]
            ):
                raise ValueError("Unpublished candidate packet changed")
            old = root / packet["previous_complete_article"]["path"]
            if sha256(old) != registry["article"]["sha256"]:
                raise ValueError("Previous full article/notes changed")
    return {
        **output,
        "actual_experiment_report_present": True,
        "correct": correct,
        "incorrect": len(cases) - correct,
        "model_decisions": model_count,
        "actual_local_runs_checked": checked_local,
    }


if __name__ == "__main__":
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("--require-local", action="store_true")
    args = p.parse_args()
    try:
        print(
            json.dumps(
                verify(require_local=args.require_local), ensure_ascii=False, indent=2
            )
        )
    except (ValueError, OSError, KeyError) as exc:
        p.exit(1, f"Claim comparison check failed: {exc}\n")
