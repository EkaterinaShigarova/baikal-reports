"""Verify coverage, independent cases, actual response and unpublished evidence."""

import argparse
import hashlib
import json
from baikal.core import ROOT, read_json, sha256
from baikal.updates import digest
from baikal.claim_coverage import compare_pair, article_presence
from baikal.coverage_audit import audit_statuses

try:
    from scripts.evaluate_coverage import inputs, PINS
except ModuleNotFoundError:
    from evaluate_coverage import inputs, PINS


def verify(root=ROOT, require_local=False):
    if require_local:
        for name in [
            "selenga_claim_registry_v2.json", "selenga_comparison_cases_v2.json"
        ]:
            for source in read_json(root / "datasets" / name)["sources"]:
                page = (root / "data/processed" / source["sha256"]
                        / f"pages/{source['page']:04d}.json")
                if not page.is_file():
                    raise FileNotFoundError(
                        f"Required cached source page unavailable: {page}"
                    )
    registry, b, v, coverage, checks = inputs(root, require_local)
    audit = read_json(root / "datasets/selenga_coverage_audit.json")
    effective = audit_statuses(registry, audit)
    report = read_json(root / "datasets/coverage_evaluation.json")
    if set(report["pins"]) != set(PINS):
        raise ValueError("Incomplete executed-input inventory")
    for p, h in report["pins"].items():
        if sha256(root / p) != h:
            raise ValueError("Executed code/input revision changed")
    if report["audit_sha256"] != sha256(root / "datasets/selenga_coverage_audit.json"):
        raise ValueError("Content audit changed")
    if any(report["coverage"][k] != value for k, value in coverage.items()) or (
        report["coverage"]["effective_source_checked_cards"]
        != effective["effective_source_checked_cards"]
    ):
        raise ValueError("Misreported coverage")
    experiment = report["experiment"]
    path = root / experiment["path"]
    if require_local or path.exists():
        manifest = read_json(path)
        if digest(manifest) != experiment["id"] or manifest["pins"] != report["pins"]:
            raise ValueError("Experiment input snapshot changed")
    if digest(report["review"]) != report["review_packet"]["id"]:
        raise ValueError("Source-review packet changed")
    correct = 0
    semantic = 0
    local_runs_checked = 0
    for result, case in zip(report["case_results"], b["examples"], strict=True):
        actual = compare_pair(case["fact"], case["target"], v)["relation"]
        if actual == "semantic_required":
            model = report["model"]
            parsed = model["parsed"]
            if parsed != {
                "fact_id": case["fact"]["fact_id"],
                "target_id": case["target"]["target_id"],
                "relation": parsed["relation"],
            } or model["experiment_id"] != experiment["id"]:
                raise ValueError("Model decision belongs to another fact/experiment")
            actual = (
                "semantic_equivalent"
                if not model["errors"]
                and parsed["relation"] == "entails"
                and report["review"]["semantic_equivalence"]["relation"]
                == "semantic_equivalent"
                else "unresolved"
            )
            if actual == "semantic_equivalent":
                presence = article_presence(
                    case["fact"],
                    [case["target"]["target_id"]],
                    registry,
                    equivalence_reviews={
                        case["target"]["target_id"]:
                            report["review"]["semantic_equivalence"]
                    },
                    vocabulary=v,
                )
            else:
                presence = article_presence(case["fact"], [], registry)
            semantic += 1
            path = root / model["path"]
            if require_local or path.exists():
                run = read_json(path)
                if (
                    digest(run) != model["run_id"]
                    or run["parsed"] != parsed
                    or run["checked"] != model["checked"]
                    or run["errors"] != model["errors"]
                    or run["experiment_id"] != experiment["id"]
                    or digest(run["request"]) != model["request_sha256"]
                    or any(
                        run[k] != model[k] for k in [
                            "runtime", "usage", "elapsed_seconds", "context_preflight"
                        ]
                    )
                    or hashlib.sha256((run["raw_response"] or "").encode()).hexdigest()
                    != model["raw_response_sha256"]
                ):
                    raise ValueError("Actual response changed")
                body = json.loads(run["raw_response"])["choices"][0]
                if (
                    body["finish_reason"] != model["finish_reason"]
                    or json.loads(body["message"]["content"]) != parsed
                ):
                    raise ValueError("Parsed result does not match actual model text")
                local_runs_checked += 1
        elif case["scope"] == "article":
            presence = article_presence(
                case["fact"],
                [case["target"]["target_id"]] if actual == "same_fact" else [],
                registry,
                vocabulary=v,
            )
        else:
            presence = {"status": "not_applicable_source_pair"}
        good = actual == case["expected"]["relation"]
        if (
            result["case_id"] != case["case_id"]
            or result["fact_sha256"] != case["fact_sha256"]
            or result["target_sha256"] != case["target_sha256"]
            or result["actual_relation"] != actual
            or result["correct"] != good
            or result["article_presence_before_separate_review"] != presence
            or presence["status"] != case["expected"]["article_presence"]
        ):
            raise ValueError("Reported comparison differs from frozen evidence")
        correct += good
    if (
        correct != report["summary"]["correct"]
        or report["summary"]["semantic_requests"] != semantic
        or report["summary"]["structural_decisions"] != checks["cases"] - semantic
        or report["published"] is not False
        or report["expert_review"] is not False
    ):
        raise ValueError("Invalid result/review/publication status")
    for item in report["review"]["absence_reviews"]:
        fact = next(c["fact"] for c in b["examples"] if c["case_id"] == item["case_id"])
        if (
            item["certificate"]["reviewed_unit_ids"]
            != [u["id"] for u in registry["inventory"]]
            or item["certificate"]["source_binding"] != fact["source_binding"]
            or article_presence(fact, [], registry) != item["without_review"]
            or article_presence(fact, [], registry, item["certificate"])
            != item["with_review"]
        ):
            raise ValueError("Global absence improperly inferred")
    packet = report["candidate_packet"]
    if (packet["published"] is not False
        or packet["candidates"] != len(report["review"]["absence_reviews"])):
        raise ValueError("Invalid candidate count/publication status")
    path = root / packet["path"]
    if require_local or path.exists():
        saved = read_json(path)
        if (
            digest(saved) != packet["id"]
            or saved["published"] is not False
            or saved["expert_review"] is not False
            or len(saved["candidates"]) != packet["candidates"]
            or saved["review_id"] != report["review_packet"]["id"]
            or saved["registry_sha256"] != digest(registry)
        ):
            raise ValueError("Candidate packet changed")
        bycase = {c["case_id"]: c for c in b["examples"]}
        reviews = {
            c["case_id"]: c["certificate"] for c in report["review"]["absence_reviews"]
        }
        for candidate in saved["candidates"]:
            if (
                candidate["fact"] != bycase[candidate["case_id"]]["fact"]
                or candidate["absence_review"] != reviews[candidate["case_id"]]
                or candidate["expert_review"] is not False
            ):
                raise ValueError("Unbound evidence candidate")
        if (
            sha256(root / saved["previous_complete_article"]["path"])
            != registry["article"]["sha256"]
        ):
            raise ValueError("Complete article/manual notes not preserved")
    return {
        "errors": [],
        "cards": coverage["cards"],
        "represented_sentences": coverage["represented_units"],
        "sentence_units": coverage["sentence_units"],
        "coverage_percent": coverage["coverage_percent"],
        "effective_source_checked_cards": effective["effective_source_checked_cards"],
        "semantic_completeness_established": False,
        "comparisons": checks["cases"],
        "correct": correct,
        "incorrect": checks["cases"] - correct,
        "unpublished_evidence_candidates": packet["candidates"],
        "local_required": require_local,
        "actual_local_runs_checked": local_runs_checked,
        "expert_review": False,
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
        p.exit(1, f"Coverage provenance check failed: {exc}\n")
