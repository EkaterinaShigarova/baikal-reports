"""Bounded typed-fact experiment, reusing the installed local server and gates."""

import argparse
import json
import time
import urllib.request
from pathlib import Path
from baikal.core import ROOT, read_json, atomic_json, sha256
from baikal.source_first import (
    freeze_cases,
    select_supported,
    compare_checked,
    save_draft,
    check_sources,
)
from baikal.typed_facts import (
    PROMPT,
    prepare_view,
    extract_schema,
    extract_checked,
    compare_schema,
    validate_schema,
    validate_view,
)
from baikal.updates import digest, save_packet
from baikal.typed_fact_context import (
    comparison_wire,
    preflight,
    validate_reason_bindings,
)

try:
    from scripts.evaluate_source_first import local_server, reserve_attempt
except ModuleNotFoundError:
    from evaluate_source_first import local_server, reserve_attempt
from baikal.source_first import COMPARE_PROMPT


def prepare(root=ROOT):
    base = freeze_cases(root)
    for c in base["cases"]:
        view = (
            prepare_view(c, root)
            if c["topic"] == "baikal-sturgeon"
            else read_json(root / "datasets/typed_evidence_2015.json")
        )
        validate_view(c, view, root)
        c["typed_view"] = view
    packet = {
        **base,
        "version": 1,
        "mode": "typed_facts_experimental",
        "max_requests": 4,
        "baseline_report_sha256": sha256(
            root / "datasets/source_first_evaluation.json"
        ),
        "protocol_sha256": sha256(root / "docs/TYPED_FACTS_PROTOCOL.md"),
        "module_sha256": sha256(root / "baikal/typed_facts.py"),
        "runner_sha256": sha256(root / "scripts/evaluate_typed_facts.py"),
        "context_module_sha256": sha256(root / "baikal/typed_fact_context.py"),
        "extract_prompt": PROMPT,
        "compare_prompt": COMPARE_PROMPT,
        "max_tokens": 2400,
        "published": False,
        "table_view_sha256": sha256(root / "datasets/typed_evidence_2015.json"),
    }
    pid, path = save_packet(root, "typed_fact_experiments", packet)
    return {"experiment_id": pid, "path": path}


def load(root, pid):
    if len(pid) != 64 or any(x not in "0123456789abcdef" for x in pid):
        raise ValueError("Invalid experiment ID")
    m = read_json(root / "data/wiki_updates/typed_fact_experiments" / f"{pid}.json", {})
    if digest(m) != pid or m.get("max_requests") != 4:
        raise ValueError("Immutable experiment mismatch")
    for rel, key in [
        ("docs/TYPED_FACTS_PROTOCOL.md", "protocol_sha256"),
        ("baikal/typed_facts.py", "module_sha256"),
        ("scripts/evaluate_typed_facts.py", "runner_sha256"),
        ("baikal/typed_fact_context.py", "context_module_sha256"),
        ("datasets/typed_evidence_2015.json", "table_view_sha256"),
        ("datasets/source_first_evaluation.json", "baseline_report_sha256"),
    ]:
        if sha256(root / rel) != m[key]:
            raise ValueError("Preregistered code/evidence/protocol changed")
    for c in m["cases"]:
        validate_view(c, c["typed_view"], root)
        if sha256(root / "wiki" / c["article"]["path"]) != c["article"]["sha256"]:
            raise ValueError("Article snapshot changed")
    return m


def call(
    root, eid, case, stage, supplied, schema, endpoint, opener, runtime, validator
):
    payload = {
        "model": "baikal-local-evaluation",
        "temperature": 0,
        "max_tokens": 2400,
        "messages": [
            {
                "role": "system",
                "content": PROMPT if stage == "extract" else COMPARE_PROMPT,
            },
            {"role": "user", "content": json.dumps(supplied, ensure_ascii=False)},
        ],
        "response_format": {
            "type": "json_schema",
            "json_schema": {"name": "typed_facts", "strict": True, "schema": schema},
        },
    }
    # Native formatting/tokenization are not generation; reject over-budget input
    # before consuming one of the four immutable generation slots.
    budget = preflight(endpoint, opener, payload)
    record = {
        "experiment_id": eid,
        "topic": case["topic"],
        "stage": stage,
        "request": payload,
        "runtime": runtime,
        "published": False,
        "context_preflight": budget,
    }
    save_packet(root, "typed_fact_inputs", record)
    req = urllib.request.Request(
        endpoint,
        data=json.dumps(payload).encode(),
        headers={"Content-Type": "application/json"},
    )
    reserve_attempt(root, eid, case["topic"], stage)
    start = time.monotonic()
    raw = None
    parsed = None
    checked = None
    response_data = None
    errors = []
    try:
        with opener.open(req, timeout=600) as response:
            body = response.read(2 * 1024**2 + 1)
            if len(body) > 2 * 1024**2:
                raise ValueError("Response exceeded 2 MiB")
            raw = body.decode()
        response_data = json.loads(raw)
        choice = response_data["choices"][0]
        if choice.get("finish_reason") != "stop":
            raise ValueError("Incomplete response, not accepted")
        parsed = json.loads(choice["message"]["content"])
        checked = validator(parsed)
        check_sources(case, root)
        if sha256(root / "wiki" / case["article"]["path"]) != case["article"]["sha256"]:
            raise ValueError("Article changed during generation")
    except (OSError, ValueError, KeyError, TypeError, IndexError) as exc:
        checked = None
        errors = [f"{type(exc).__name__}: {exc}"]
    run = {
        **record,
        "raw_response": raw,
        "parsed": parsed,
        "checked": checked,
        "errors": errors,
        "elapsed_seconds": round(time.monotonic() - start, 3),
        "usage": response_data.get("usage")
        if isinstance(response_data, dict)
        else None,
    }
    rid, path = save_packet(root, "typed_fact_runs", run)
    return {
        "topic": case["topic"],
        "run_id": rid,
        "path": path,
        "errors": errors,
        "checked": checked,
        "elapsed_seconds": run["elapsed_seconds"],
    }


def phase(args, root=ROOT):
    m = load(root, args.experiment)
    stage = "extract" if args.extract else "compare"
    jobs = []
    skipped = []
    if stage == "compare":
        prior = read_json(args.extraction)
        review = read_json(args.review)
        if (
            prior["experiment_id"] != args.experiment
            or review.get("experiment_id") != args.experiment
        ):
            raise ValueError("Review/run experiment mismatch")
    for c in m["cases"]:
        if stage == "extract":
            jobs.append(
                (c, c["typed_view"], extract_schema(c["typed_view"]), None, None)
            )
            continue
        old = next(r for r in prior["results"] if r["topic"] == c["topic"])
        run = read_json(root / old["path"])
        if (
            digest(run) != old["run_id"]
            or run["experiment_id"] != args.experiment
            or run["stage"] != "extract"
        ):
            raise ValueError("Extraction integrity mismatch")
        if run["errors"] or run["checked"] is None:
            skipped.append({"topic": c["topic"], "reason": "extraction_rejected"})
            continue
        bundle = extract_checked(run["parsed"], c, c["typed_view"])
        if bundle != run["checked"]:
            raise ValueError("Source checks changed")
        decision = review["cases"][c["topic"]]
        supported = select_supported(bundle, decision)
        review_id, _ = save_packet(
            root,
            "typed_fact_reviews",
            {"experiment_id": args.experiment, "topic": c["topic"], **decision},
        )
        if not supported["facts"]:
            skipped.append({"topic": c["topic"], "reason": "no_supported_facts"})
            continue
        supplied = comparison_wire(c, supported, decision)
        jobs.append((c, supplied, compare_schema(supported), supported, review_id))
    if not jobs:
        return {
            "experiment_id": args.experiment,
            "stage": stage,
            "results": [],
            "skipped": skipped,
            "model_started": False,
        }
    for c, _, _, _, _ in jobs:
        marker = (
            root
            / "data/llm-evaluation"
            / f"source-first-{args.experiment}.attempts"
            / f"{c['topic']}-{stage}.json"
        )
        if marker.exists():
            raise ValueError("Generation slot consumed; no retry")
    from baikal.local_models import profile_7b

    results = []
    with local_server(
        root, args.model_file.resolve(), args.server_binary.resolve(), profile_7b()
    ) as (endpoint, opener, runtime):
        for c, supplied, schema, supported, review_id in jobs:
            if stage == "extract":
                validator = lambda x, c=c: extract_checked(x, c, c["typed_view"])
            else:

                def validator(x, supplied=supplied, schema=schema):
                    validate_schema(x, schema)
                    validate_reason_bindings(x, supplied)
                    return compare_checked(x, supplied)

            result = call(
                root,
                args.experiment,
                c,
                stage,
                supplied,
                schema,
                endpoint,
                opener,
                runtime,
                validator,
            )
            if stage == "compare" and not result["errors"]:
                result["draft"] = save_draft(
                    root, c, supported, result["checked"], result["run_id"], review_id
                )
            results.append(result)
            print(
                json.dumps(
                    {
                        "stage": stage,
                        "topic": c["topic"],
                        "run_id": result["run_id"],
                        "errors": result["errors"],
                    }
                ),
                flush=True,
            )
    return {
        "experiment_id": args.experiment,
        "stage": stage,
        "results": results,
        "skipped": skipped,
        "runtime": runtime,
        "published": False,
        "expert_review": False,
    }


if __name__ == "__main__":
    p = argparse.ArgumentParser(description=__doc__)
    mode = p.add_mutually_exclusive_group(required=True)
    for name in ["prepare", "extract", "compare"]:
        mode.add_argument("--" + name, action="store_true")
    p.add_argument("--experiment")
    p.add_argument("--extraction", type=Path)
    p.add_argument("--review", type=Path)
    p.add_argument(
        "--model-file",
        type=Path,
        default=ROOT
        / "data/models/qwen25-7b-q4km/qwen2.5-7b-instruct-q4_k_m-00001-of-00002.gguf",
    )
    p.add_argument(
        "--server-binary",
        type=Path,
        default=ROOT / "data/llm-runtime/build-avx2/bin/llama-server",
    )
    p.add_argument("--output", type=Path, required=True)
    args = p.parse_args()
    if args.output.exists():
        p.error("Output exists; preserve preceding results")
    if not args.prepare and not args.experiment:
        p.error("Experiment ID required")
    if args.compare and (not args.extraction or not args.review):
        p.error("Extraction and source review required")
    try:
        outcome = prepare() if args.prepare else phase(args)
        atomic_json(args.output, outcome)
        print(
            json.dumps(
                {"output": str(args.output), "experiment_id": outcome["experiment_id"]}
            )
        )
    except (ValueError, OSError, KeyError) as exc:
        p.exit(1, f"Typed-fact experiment stopped: {exc}\n")
