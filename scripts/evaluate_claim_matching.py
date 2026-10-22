"""Bounded hybrid comparison of a preregistered balanced set; no publication."""

import argparse
import json
import time
import urllib.request
from pathlib import Path

from baikal.core import ROOT, atomic_json, read_json, sha256
from baikal.local_models import profile_7b
from baikal.claim_matching import (
    PROMPT,
    validate_registry,
    validate_benchmark,
    structural_compare,
    comparison_input,
    schema,
    check_response,
)
from baikal.typed_fact_context import preflight
from baikal.updates import digest, save_packet

try:
    from scripts.evaluate_source_first import local_server
except ModuleNotFoundError:
    from evaluate_source_first import local_server

PINS = [
    "baikal/claim_matching.py",
    "scripts/evaluate_claim_matching.py",
    "docs/CLAIM_MATCHING_PROTOCOL.md",
    "datasets/selenga_claim_registry.json",
    "datasets/claim_comparison_benchmark.json",
    "datasets/typed_evidence_2015.json",
    "datasets/novelty_evaluation.json",
    "baikal/typed_fact_context.py",
    "scripts/evaluate_source_first.py",
]


def inputs(root):
    registry = read_json(root / "datasets/selenga_claim_registry.json")
    benchmark = read_json(root / "datasets/claim_comparison_benchmark.json")
    validate_registry(registry, root, require_local=True)
    validate_benchmark(benchmark, registry, root)
    return registry, benchmark


def prepare(root=ROOT):
    registry, benchmark = inputs(root)
    jobs = []
    for case in benchmark["examples"]:
        route = structural_compare(case, registry)
        jobs.append(
            {
                "case_id": case["case_id"],
                "route": route,
                "input": comparison_input(case, registry)
                if route["route"] == "llm_required"
                else None,
            }
        )
    model_cases = [j["case_id"] for j in jobs if j["input"] is not None]
    if model_cases != ["A2", "N1", "N2"]:
        raise ValueError("Preregistered model scope changed")
    manifest = {
        "version": 1,
        "max_requests": 3,
        "allowed_cases": model_cases,
        "jobs": jobs,
        "pins": {p: sha256(root / p) for p in PINS},
        "prompt": PROMPT,
        "model_profile": profile_7b(),
        "max_tokens": 512,
        "input_limit_tokens": 3500,
        "published": False,
        "expert_review": False,
    }
    pid, path = save_packet(root, "claim_comparison_experiments", manifest)
    return {"experiment_id": pid, "path": path, "generation_requests": 0}


def load(root, pid):
    if len(pid) != 64 or any(c not in "0123456789abcdef" for c in pid):
        raise ValueError("Invalid experiment ID")
    manifest = read_json(
        root / "data/wiki_updates/claim_comparison_experiments" / f"{pid}.json"
    )
    if (
        digest(manifest) != pid
        or manifest["max_requests"] != 3
        or manifest["allowed_cases"] != ["A2", "N1", "N2"]
        or manifest["prompt"] != PROMPT
        or manifest["model_profile"] != profile_7b()
        or manifest["published"] is not False
    ):
        raise ValueError("Manifest integrity mismatch")
    if manifest["pins"] != {p: sha256(root / p) for p in PINS}:
        raise ValueError("Preregistered code, evidence or criteria changed")
    registry, benchmark = inputs(root)
    for job, case in zip(manifest["jobs"], benchmark["examples"], strict=True):
        route = structural_compare(case, registry)
        if job != {
            "case_id": case["case_id"],
            "route": route,
            "input": comparison_input(case, registry)
            if route["route"] == "llm_required"
            else None,
        }:
            raise ValueError("Frozen routing/context changed")
    return manifest


def marker(root, pid, cid):
    return (
        root
        / "data/llm-evaluation"
        / f"claim-comparison-{pid}.attempts"
        / f"{cid}.json"
    )


def call(root, pid, manifest, job, endpoint, opener, runtime):
    cid = job["case_id"]
    if cid not in manifest["allowed_cases"] or job["input"] is None:
        raise ValueError("This case must not use the model")
    supplied = job["input"]
    payload = {
        "model": "baikal-local-evaluation",
        "temperature": 0,
        "max_tokens": manifest["max_tokens"],
        "messages": [
            {"role": "system", "content": manifest["prompt"]},
            {"role": "user", "content": json.dumps(supplied, ensure_ascii=False)},
        ],
        "response_format": {
            "type": "json_schema",
            "json_schema": {
                "name": "scoped_claim_relation",
                "strict": True,
                "schema": schema(supplied),
            },
        },
    }
    budget = preflight(endpoint, opener, payload)
    if budget["prompt_tokens_native"] > manifest["input_limit_tokens"]:
        raise ValueError("Compact context budget exceeded; no generation")
    record = {
        "experiment_id": pid,
        "case_id": cid,
        "request": payload,
        "runtime": runtime,
        "context_preflight": budget,
        "published": False,
    }
    save_packet(root, "claim_comparison_inputs", record)
    slot = marker(root, pid, cid)
    slot.parent.mkdir(parents=True, exist_ok=True)
    with slot.open("x") as stream:
        json.dump({"experiment_id": pid, "case_id": cid}, stream)
    raw = parsed = checked = data = None
    errors = []
    started = time.monotonic()
    try:
        request = urllib.request.Request(
            endpoint,
            data=json.dumps(payload).encode(),
            headers={"Content-Type": "application/json"},
        )
        with opener.open(request, timeout=600) as response:
            body = response.read(2 * 1024**2 + 1)
            if len(body) > 2 * 1024**2:
                raise ValueError("Response too large")
            raw = body.decode()
        data = json.loads(raw)
        choice = data["choices"][0]
        if choice["finish_reason"] != "stop":
            raise ValueError("Incomplete answer rejected")
        parsed = json.loads(choice["message"]["content"])
        checked = check_response(parsed, supplied)
        load(root, pid)
    except (OSError, ValueError, TypeError, KeyError, IndexError) as exc:
        checked = None
        errors = [f"{type(exc).__name__}: {exc}"]
    run = {
        **record,
        "raw_response": raw,
        "parsed": parsed,
        "checked": checked,
        "errors": errors,
        "elapsed_seconds": round(time.monotonic() - started, 3),
        "usage": data.get("usage") if isinstance(data, dict) else None,
    }
    rid, path = save_packet(root, "claim_comparison_runs", run)
    return {"case_id": cid, "run_id": rid, "path": path, "errors": errors}


def execute(args, root=ROOT):
    manifest = load(root, args.experiment)
    if any(
        marker(root, args.experiment, cid).exists() for cid in manifest["allowed_cases"]
    ):
        raise ValueError("Generation slots already used; no rerun or retry")
    results = []
    with local_server(
        root, args.model_file.resolve(), args.server_binary.resolve(), profile_7b()
    ) as (endpoint, opener, runtime):
        for job in manifest["jobs"]:
            if job["input"] is not None:
                result = call(
                    root, args.experiment, manifest, job, endpoint, opener, runtime
                )
                results.append(result)
                print(json.dumps(result), flush=True)
    return {
        "experiment_id": args.experiment,
        "results": results,
        "runtime": runtime,
        "published": False,
        "expert_review": False,
    }


if __name__ == "__main__":
    p = argparse.ArgumentParser(description=__doc__)
    mode = p.add_mutually_exclusive_group(required=True)
    mode.add_argument("--prepare", action="store_true")
    mode.add_argument("--run", action="store_true")
    p.add_argument("--experiment")
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
    if args.run and not args.experiment:
        p.error("Experiment ID required")
    try:
        result = prepare() if args.prepare else execute(args)
        atomic_json(args.output, result)
        print(
            json.dumps(
                {"output": str(args.output), "experiment_id": result["experiment_id"]}
            )
        )
    except (ValueError, OSError, KeyError) as exc:
        p.exit(1, f"Claim comparison stopped: {exc}\n")
