"""Verify novelty experiment provenance; this is not a semantic expert review."""

import argparse
import hashlib
import json

from baikal.core import ROOT, read_json, sha256
from baikal.novelty import check_result
from baikal.updates import digest

try:
    from scripts.evaluate_novelty import load
except ModuleNotFoundError:
    from evaluate_novelty import load


def verify(root=ROOT, require_local=False):
    report = read_json(root / "datasets/novelty_evaluation.json")
    for path, expected in report["pins"].items():
        if sha256(root / path) != expected:
            raise ValueError(f"Preregistered file changed: {path}")
    previous = read_json(root / "datasets/typed_facts_evaluation.json")
    prior = next(c for c in previous["cases"] if c["topic"] == "selenga-sediments")[
        "extraction"
    ]
    old_facts = {f["fact_id"]: f for f in prior["source_checked_records"]}
    refs = {
        s["id"]: {k: v for k, v in s.items() if k != "id"}
        for s in report["source_evidence"]
    }
    view = read_json(root / "datasets/typed_evidence_2015.json")
    for fragment in view["source_fragments"]:
        if refs[fragment["id"]]["quote"] != fragment["text"]:
            raise ValueError("Exact source quote changed")
    ids = []
    for row in report["reused_records"]:
        fact = row["fact"]
        ids.append(fact["fact_id"])
        if (
            fact != old_facts[fact["fact_id"]]
            or digest({**fact, "evidence": [refs[i] for i in row["evidence_ids"]]})
            != row["binding_sha256"]
        ):
            raise ValueError("Reused fact/source binding changed")
        if row["cell"] != next(
            c for c in view["table_view"]["cells"] if c["cell_id"] == fact["cell_id"]
        ):
            raise ValueError("Table cell changed")
    if ids != prior["fully_source_supported_fact_ids"]:
        raise ValueError("Previously rejected or missing facts")
    article = report["article"]
    if (
        sha256(root / article["path"]) != article["sha256"]
        or "\n\n".join(p["text"] for p in article["paragraphs"])
        != (root / article["path"]).read_text()
    ):
        raise ValueError("Current article/notes or paragraph inventory changed")
    supplied = {
        "facts": [r["fact"] for r in report["reused_records"]],
        "wiki": {"paragraphs": article["paragraphs"]},
        "source_conflicts": [
            {"conflict_id": k}
            for k in report["semantic_review"]["decision"]["conflicts"]
        ],
    }
    check_result(report["actual_model_decisions"], supplied)
    expected = {
        r["fact"]["fact_id"]: r["expected_classification_preregistered"]
        for r in report["reused_records"]
    }
    correct = sum(
        d["classification"] == expected[d["fact_id"]]
        for d in report["actual_model_decisions"]["decisions"]
    )
    if (
        report["summary"]["semantically_correct_classifications"] != correct
        or report["published"] is not False
        or report["expert_review"] is not False
    ):
        raise ValueError(
            "Reported decision count or review/publication status mismatch"
        )
    local = root / report["run"]["path"]
    checked_local = False
    if require_local or local.exists():
        m = load(root, report["experiment_id"])
        run = read_json(local)
        if (
            digest(run) != report["run"]["id"]
            or sha256(local) != report["run"]["file_sha256"]
            or run["parsed"] != report["actual_model_decisions"]
            or run["checked"] != run["parsed"]
        ):
            raise ValueError("Actual saved run changed")
        if (
            digest(run["request"]) != report["run"]["request_sha256"]
            or digest(m["input"]) != report["input_sha256"]
            or hashlib.sha256(run["raw_response"].encode()).hexdigest()
            != report["run"]["raw_response_sha256"]
        ):
            raise ValueError("Raw response/request/context changed")
        review_path = root / report["semantic_review"]["path"]
        review = read_json(review_path)
        if (
            review != report["semantic_review"]["decision"]
            or digest(review) != report["semantic_review"]["id"]
            or sha256(review_path) != report["semantic_review"]["file_sha256"]
        ):
            raise ValueError("Separate semantic review changed")
        if sha256(root / article["preceding_version"]["path"]) != article["sha256"]:
            raise ValueError("Previous complete article/notes not preserved")
        checked_local = True
    return {
        "errors": [],
        "reused_fact_bindings": len(ids),
        "source_fragments": len(refs),
        "unique_source_pages": len(
            {(r["source_id"], r["sha256"], r["page"]) for r in refs.values()}
        ),
        "structural_decisions": len(ids),
        "semantically_correct_reported": correct,
        "checked_actual_local_run": checked_local,
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
        p.exit(1, f"Novelty provenance check failed: {exc}\n")
