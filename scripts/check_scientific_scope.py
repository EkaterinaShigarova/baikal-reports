"""Verify partial cross-topic registries, source scope and unpublished packets."""
import argparse
import hashlib
import json
from baikal.core import ROOT, read_json, sha256
from baikal.updates import digest
from baikal.cross_topic import validate, compare, presence
try:
    from scripts.evaluate_cross_topic import inputs, PINS
except ModuleNotFoundError:
    from evaluate_cross_topic import inputs, PINS


def verify(root=ROOT, require_local=False):
    bundle, benchmark, coverage = inputs(root, require_local)
    report = read_json(root / "datasets/cross_topic_evaluation.json")
    if set(report["pins"]) != set(PINS) or any(sha256(root/p) != h for p,h in report["pins"].items()):
        raise ValueError("Preregistered input changed")
    if report["published"] is not False or report["expert_review"] is not False:
        raise ValueError("Unapproved publication/expert status")
    scope = read_json(root / report["selenga_review"]["path"])
    research = read_json(root / report["research_plans"]["path"])
    if (sha256(root/report["selenga_review"]["path"]) != report["selenga_review"]["sha256"]
        or sha256(root/report["research_plans"]["path"]) != report["research_plans"]["sha256"]):
        raise ValueError("Source-scope assessment changed")
    old = read_json(root / "datasets/selenga_claim_registry_v2.json")
    article = root / scope["article"]["path"]
    if (scope["article"] != old["article"] or sha256(article) != scope["article"]["sha256"]
        or sha256(root/"datasets/selenga_claim_registry_v2.json") != scope["registry_v2_file_sha256"]
        or sha256(root/"datasets/coverage_evaluation.json") != scope["coverage_report_sha256"]):
        raise ValueError("Selenga historical version changed")
    if scope["coverage"]["semantic_completeness_established"] is not False or scope["published"] is not False:
        raise ValueError("Unverified Selenga completeness/publication")
    source_checks = coverage["exact_local_excerpts_checked"]
    source_checks += validate({"sources":scope["sources"],"articles":[],"expert_review":False},root,require_local)["exact_local_excerpts_checked"]
    source_checks += validate(research,root,require_local)["exact_local_excerpts_checked"]
    src = {s["id"]:s for s in scope["sources"]}
    units = {u["id"]:u for u in old["inventory"]}
    for facet in scope["atomic_facets"]:
        s = src[facet["source_evidence_id"]]
        if (facet["article_evidence"] != units[facet["article_evidence"]["id"]]
            or facet["support_quote"] not in s["quote"]
            or facet["source_id"] != s["source_id"] or facet["sha256"] != s["sha256"]
            or facet["physical_page"] != s["page"] or facet["expert_review"] is not False):
            raise ValueError("Atomic facet lost source attribution")
    for issue in scope["issues"]:
        if issue["article_evidence"] != units[issue["unit_id"]] or issue["verified_environmental_fact"] is not False:
            raise ValueError("Methodological context promoted to an environmental fact")
    obs = read_json(root / "datasets/observations.json")
    repo = scope["repository_evidence"]
    if (sha256(root/repo["observations_file"]) != repo["sha256"]
        or len(obs) != repo["reviewed_rows"]
        or sum('селенг' in r["territory"].lower() for r in obs) != repo["selenga_territory_rows"]
        or sorted({r["indicator"] for r in obs}) != repo["indicators"]):
        raise ValueError("Dataset-scope claim no longer holds")
    editorial = read_json(root / report["selenga_review"]["editorial_packet"])
    if (sha256(root/report["selenga_review"]["editorial_packet"]) != report["selenga_review"]["editorial_packet_sha256"]
        or editorial["article"] != old["article"]
        or editorial["editorial_decision"] is not None or editorial["expert_review"] is not False
        or editorial["published"] is not False):
        raise ValueError("Unapproved Selenga editorial packet")
    body = {k:v for k,v in editorial.items() if k not in ["local_packet_id","local_packet_path"]}
    if digest(body) != editorial["local_packet_id"]:
        raise ValueError("Editorial content differs from the archived packet")
    prior_cases = read_json(root / "datasets/selenga_comparison_cases_v2.json")["examples"]
    expected_facts = [c["fact"] for c in prior_cases if c["case_id"] in ["V02","V12"]]
    if editorial["candidate_facts"] != expected_facts:
        raise ValueError("Existing source candidates were changed")
    semantic = report["semantic_review"]
    model = report["model"]
    correct = 0
    for result, case in zip(report["case_results"],benchmark["examples"],strict=True):
        route = compare(case["fact"],case["target"])
        actual = route["relation"]
        if actual == "semantic_required":
            if (semantic["fact_sha256"] != case["fact_sha256"] or semantic["target_sha256"] != case["target_sha256"]
                or semantic["expert_review"] is not False or semantic["run_id"] != model["run_id"]
                or model["parsed"]["fact_id"] != case["fact"]["id"]
                or model["parsed"]["target_id"] != case["target"]["id"]):
                raise ValueError("Semantic result not bound to its fact/target")
            actual = "semantic_equivalent" if model["parsed"]["relation"] == "entails" and not model["errors"] and semantic["decision"] == "semantic_equivalent" else "unresolved"
        if (result["id"] != case["id"] or result["structural_result"] != route
            or result["actual_relation"] != actual or result["correct"] != (actual==case["expected"])):
            raise ValueError("Reported comparison not reproducible")
        correct += result["correct"]
    if correct != report["summary"]["correct"]:
        raise ValueError("Misreported correct comparisons")
    candidate = report["candidate"]
    new = next(c["fact"] for c in benchmark["examples"] if c["id"]=="T02")
    if (presence(new,bundle["articles"][0],absence_review=report["absence_review"]) != "reviewed_new_detail_candidate_only"
        or candidate["fact"] != new or candidate["absence_review"] != report["absence_review"]
        or candidate["published"] is not False or candidate["editorial_decision"] is not None
        or candidate["expert_review"] is not False):
        raise ValueError("Candidate lacks full-article review or has false approval")
    if digest({k:v for k,v in candidate.items() if k not in ["id","path"]}) != candidate["id"]:
        raise ValueError("Candidate packet content changed")
    if candidate["previous_complete_article"]["sha256"] != bundle["articles"][0]["article"]["sha256"]:
        raise ValueError("Candidate archived another article version")
    actual_run_checked = False
    path = root/model["path"]
    if require_local or path.exists():
        run = read_json(path)
        if not run: raise FileNotFoundError(path)
        if (digest(run) != model["run_id"] or run["experiment_id"] != report["experiment"]["id"]
            or digest(run["request"]) != model["request_sha256"]
            or hashlib.sha256(run["raw_response"].encode()).hexdigest() != model["raw_response_sha256"]
            or any(run[k] != model[k] for k in ["parsed","checked","errors","runtime","elapsed_seconds","usage","context_preflight"])):
            raise ValueError("Actual model response/metrics changed")
        choice = json.loads(run["raw_response"])["choices"][0]
        if choice["finish_reason"] != "stop" or json.loads(choice["message"]["content"]) != model["parsed"]:
            raise ValueError("Parsed model decision differs from real text")
        actual_run_checked = True
    path = root/report["experiment"]["path"]
    if require_local or path.exists():
        manifest = read_json(path)
        if (digest(manifest) != report["experiment"]["id"] or manifest["pins"] != report["pins"]
            or manifest["max_requests"] != 1):
            raise ValueError("Real experiment manifest changed")
        if actual_run_checked and (
            json.loads(run["request"]["messages"][1]["content"]) != manifest["wire"]
            or run["request"]["messages"][0]["content"] != manifest["prompt"]
        ):
            raise ValueError("Model did not receive the frozen context")
    for entry in [candidate,editorial]:
        name = "path" if entry is candidate else "local_packet_path"
        pid = entry["id"] if entry is candidate else entry["local_packet_id"]
        path = root/entry[name]
        if require_local or path.exists():
            if digest(read_json(path)) != pid:
                raise ValueError("Unpublished packet no longer matches its public description")
    for packet in [candidate,editorial]:
        archived = packet["previous_complete_article"]
        path = root/archived["path"]
        if require_local or path.exists():
            if sha256(path) != archived["sha256"]:
                raise ValueError("Complete previous article was not preserved")
    return {"errors":[], "claims":coverage["claims"], "articles":coverage["articles"],
            "new_examples":len(benchmark["examples"]),"correct":correct,
            "incorrect":len(benchmark["examples"])-correct,"checked_local_excerpts":source_checks,
            "actual_local_run_checked":actual_run_checked,"expert_review":False}


if __name__ == "__main__":
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--require-local",action="store_true")
    args=parser.parse_args()
    try: print(json.dumps(verify(require_local=args.require_local),ensure_ascii=False,indent=2))
    except (ValueError,OSError,KeyError) as exc: parser.exit(1,f"Scientific scope check failed: {exc}\n")
