"""Check provenance and editorial boundaries, without running a model or publishing."""

import argparse
import hashlib
import json
from baikal.core import ROOT, read_json, sha256
from baikal.local_models import profile_7b
from baikal.topic_discovery import PROMPT, check_bundle, context, gate
from baikal.updates import digest
try:
    from scripts.discover_topics import PINS
except ModuleNotFoundError:
    from discover_topics import PINS


def check(root=ROOT, require_local=False):
    report = read_json(root / "datasets/topic_discovery_evaluation.json")
    if report["published"] or report["expert_review"] or report["editorial_decision"] is not None:
        raise ValueError("Experimental outputs cannot carry publication/expert approval")
    for p, expected in report["pins"].items():
        if sha256(root / p) != expected:
            raise ValueError(f"Frozen input changed: {p}")
    suite = read_json(root / "datasets/topic_discovery_tasks.json")
    cases, local, pdfs = [], 0, {}
    for bundle in suite["tasks"]:
        local += check_bundle(bundle, root, require_local)
        cases.append({"bundle": bundle, "wire": context(bundle, root)})
        for e in bundle["evidence"]:
            pdfs[e["sha256"]] = root / "data/raw" / f"{e['sha256']}.pdf"
    frozen = {"pins": {p: sha256(root / p) for p in [*PINS, "datasets/topic_discovery_tasks.json"]},
              "cases": cases, "max_requests": len(cases), "prompt": PROMPT,
              "model_profile": profile_7b(), "published": False,
              "tasks_path": "datasets/topic_discovery_tasks.json", "expert_review": False}
    if digest(frozen) != report["experiment_id"]:
        raise ValueError("Frozen model input cannot be reconstructed")
    if len(report["cases"]) != len(cases) or len(cases) > 4:
        raise ValueError("Bounded task list changed")
    drafts, local_runs, checksums = 0, 0, 0
    decisions_correct = program_passed = 0
    for output, case in zip(report["cases"], cases):
        bundle, wire = case["bundle"], case["wire"]
        if output["case_id"] != bundle["id"]:
            raise ValueError("Case order/identity changed")
        if output.get("parsed") is not None and output.get("check") is not None:
            if gate(output["parsed"], bundle, wire) != output["check"]:
                raise ValueError("Program gate result changed")
        review = output["content_review"]
        actual = output["parsed"] or {}
        correct = actual.get("decision") == bundle["expected"]["decision"] and actual.get("target_id") == bundle["expected"]["target_id"]
        if review["decision_matches_preregistered"] != correct:
            raise ValueError("Outcome differs from frozen expected decision")
        decisions_correct += correct
        program_passed += bool(output.get("check") and output["check"]["program_gate_passed"])
        run_path = root / output["path"]
        if run_path.exists():
            run = read_json(run_path)
            if (digest(run) != output["run_id"] or run["parsed"] != output["parsed"]
                    or run["request"]["messages"][1]["content"] != json.dumps(wire, ensure_ascii=False)):
                raise ValueError("Local real run differs from report")
            raw = run["raw_response"]
            if raw and hashlib.sha256(raw.encode()).hexdigest() != output["raw_response_sha256"]:
                raise ValueError("Raw response checksum differs from report")
            local_runs += 1
        elif require_local:
            raise ValueError("Local real run unavailable")
        retained = output.get("retained")
        if retained and retained.get("draft"):
            path = root / retained["draft"]
            if not path.resolve().is_relative_to((root / "docs/drafts/discovery").resolve()):
                raise ValueError("Draft outside unpublished folder")
            if sha256(path) != retained["draft_sha256"]:
                raise ValueError("Unpublished plan changed")
            text = path.read_text()
            selected = set(output["check"]["evidence_ids"])
            for e in bundle["evidence"]:
                if e["id"] in selected:
                    block = "\n".join("> " + line for line in e["quote"].splitlines())
                    if block not in text or f".md#page-{e['page']}" not in text:
                        raise ValueError("Draft lost exact evidence/page link")
            if require_local:
                packet = read_json(root / retained["packet_path"])
                if digest(packet) != retained["packet_id"] or packet["markdown"] != text:
                    raise ValueError("Retained draft packet changed")
                for archive in packet["archives"]:
                    if sha256(root / archive["path"]) != archive["sha256"]:
                        raise ValueError("Previous full article archive changed")
            drafts += 1
    lead_references = 0
    sources = {s["id"]: s for s in read_json(root / "config/sources.json")["sources"]}
    for lead in report["scan"]["additional_leads"]:
        if lead["status"] != "unreviewed_research_lead_not_new_article":
            raise ValueError("Unreviewed heading promoted to scientific topic")
        for e in lead["example_evidence"]:
            if sources[e["source_id"]]["sha256"] != e["sha256"]:
                raise ValueError("Heading source revision changed")
            p = root / "data/processed" / e["sha256"] / "pages" / f"{e['page']:04d}.json"
            if p.exists():
                if sha256(p) != e["checkpoint_sha256"] or e["quote"] not in read_json(p)["text"]:
                    raise ValueError("Heading excerpt/checkpoint changed")
                lead_references += 1
            elif require_local:
                raise ValueError("Heading checkpoint unavailable")
            pdfs[e["sha256"]] = root / "data/raw" / f"{e['sha256']}.pdf"
    for expected, path in pdfs.items():
        if path.exists():
            if sha256(path) != expected:
                raise ValueError("Immutable PDF checksum mismatch")
            checksums += 1
        elif require_local:
            raise ValueError("Immutable PDF unavailable")
    for path, expected in report["prior_drafts"].items():
        if sha256(root / path) != expected:
            raise ValueError("Existing analytical draft changed")
    counts = report["summary"]
    if (counts["real_model_responses"] != len(cases)
            or counts["correct_topic_decisions"] != decisions_correct
            or counts["incorrect_topic_decisions"] != len(cases)-decisions_correct
            or counts["program_gate_passed"] != program_passed
            or counts["new_model_markdown_plans"] != drafts
            or counts["editorially_approved"] != 0 or counts["new_published_articles"] != 0
            or counts["new_reviewed_observations"] != 0):
        raise ValueError("Summary counts or editorial boundaries changed")
    prior = read_json(root / "datasets/editorial_scientific_review.json")
    candidate_ids = {c["id"] for c in prior["candidates"]}
    for addition in report["editorial_queue"]["additions"]:
        if addition["candidate"] not in candidate_ids:
            raise ValueError("Missing prior editorial candidate")
    return {"errors": [], "cases": len(cases), "local_real_runs_checked": local_runs,
            "local_excerpt_references": local, "pdf_checksums_checked": checksums,
            "local_heading_references": lead_references,
            "unpublished_plans": drafts, "published": False, "expert_review": False}


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--require-local", action="store_true")
    args = parser.parse_args()
    try:
        print(json.dumps(check(require_local=args.require_local), ensure_ascii=False, indent=2))
    except (ValueError, OSError, KeyError, TypeError) as exc:
        parser.exit(1, f"Topic report check stopped: {exc}\n")
