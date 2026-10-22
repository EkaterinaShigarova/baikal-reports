"""Preserve separately reviewed candidates without a Wiki publication bridge."""

import copy
import json

from .core import ROOT, read_json, sha256
from .claim_matching import check_response, validate_registry, validate_benchmark
from .updates import archive_article, digest, save_packet


def save_candidates(root, manifest, runs, review):
    eid = digest(manifest)
    if (
        review.get("experiment_id") != eid
        or review.get("review_kind") != "semantic_comparison_non_expert"
        or review.get("expert_review") is not False
    ):
        raise ValueError("Separate snapshot-bound semantic review required")
    if review.get("test_only") and root.resolve() == ROOT.resolve():
        raise ValueError("Synthetic review forbidden in the real repository")
    registry = read_json(root / "datasets/selenga_claim_registry.json")
    benchmark = read_json(root / "datasets/claim_comparison_benchmark.json")
    validate_registry(registry, root, require_local=True)
    validate_benchmark(benchmark, registry, root)
    for p in [
        "datasets/selenga_claim_registry.json",
        "datasets/claim_comparison_benchmark.json",
        "datasets/novelty_evaluation.json",
    ]:
        if sha256(root / p) != manifest["pins"][p]:
            raise ValueError("Candidate evidence/registry changed")
    expected = set(manifest["allowed_cases"])
    if set(runs) != expected or set(review.get("cases", {})) != expected:
        raise ValueError("Review must cover every actual model attempt")
    if review.get("article_sha256") != registry["article"]["sha256"]:
        raise ValueError("Full-article semantic review version changed")
    previous = read_json(root / "datasets/novelty_evaluation.json")
    sources = {
        s["id"]: {k: v for k, v in s.items() if k != "id"}
        for s in previous["source_evidence"]
    }
    originals = {
        r["fact"]["fact_id"]: {
            **r["fact"],
            "evidence": [sources[i] for i in r["evidence_ids"]],
        }
        for r in previous["reused_records"]
    }
    candidates = []
    for cid in manifest["allowed_cases"]:
        run, decision = runs[cid], review["cases"][cid]
        if (
            decision.get("run_id") != digest(run)
            or run["experiment_id"] != eid
            or run["case_id"] != cid
        ):
            raise ValueError("Reviewed response identity changed")
        if (
            decision.get("status") not in {"supported", "rejected"}
            or not isinstance(decision.get("reason"), str)
            or not decision["reason"].strip()
        ):
            raise ValueError("Substantive review reason required")
        if decision["status"] != "supported":
            continue
        if run["errors"] or run["checked"] is None or run["checked"] != run["parsed"]:
            raise ValueError(
                "Failed or rewritten model result cannot become a candidate"
            )
        job = next(j for j in manifest["jobs"] if j["case_id"] == cid)
        if json.loads(run["request"]["messages"][1]["content"]) != job["input"]:
            raise ValueError("Model input differs from the preregistered context")
        check_response(run["checked"], job["input"])
        if run["checked"]["classification"] != "new_detail":
            continue
        case = next(c for c in benchmark["examples"] if c["case_id"] == cid)
        fact = originals.get(case["fact"]["fact_id"])
        if fact is None or digest(fact) != case["fact"].get("original_fact_sha256"):
            raise ValueError(
                "Only the immutable source-checked original may be retained"
            )
        candidates.append(
            {
                "case_id": cid,
                "original_fact": copy.deepcopy(fact),
                "comparison_result": run["checked"],
                "run_id": digest(run),
                "semantic_review": decision,
                "limitations": case["fact"]["limitations"],
            }
        )
    if not candidates:
        return None
    version = archive_article(root, registry["article"]["path"].removeprefix("wiki/"))
    packet = {
        "version": 1,
        "experiment_id": eid,
        "status": "unpublished_candidates_awaiting_editorial_review",
        "published": False,
        "expert_review": False,
        "review": review,
        "candidates": candidates,
        "previous_complete_article": version,
        "registry_sha256": digest(registry),
        "known_conflicts": registry["known_conflicts"],
        "warning": "Not editorial approval or verified observations. Original fact text requires an explicit mean before publication; do not alter values or uncertainty.",
    }
    pid, path = save_packet(root, "claim_comparison_candidates", packet)
    return {"id": pid, "path": path, "candidates": len(candidates), "published": False}
