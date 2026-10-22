"""One real comparison request for the three previously source-checked facts."""

import argparse
import json
import time
import urllib.request
from pathlib import Path

from baikal.core import ROOT, atomic_json, read_json, sha256
from baikal.local_models import profile_7b
from baikal.novelty import (
    adopt,
    make_input,
    response_schema,
    check_result,
    PROMPT,
    save_reviewed_draft,
)
from baikal.typed_fact_context import preflight
from baikal.updates import digest, save_packet

try:
    from scripts.evaluate_source_first import local_server, reserve_attempt
except ModuleNotFoundError:
    from evaluate_source_first import local_server, reserve_attempt

PINS = [
    "baikal/novelty.py",
    "scripts/evaluate_novelty.py",
    "docs/NOVELTY_PROTOCOL.md",
    "datasets/typed_facts_evaluation.json",
    "datasets/typed_evidence_2015.json",
    "baikal/source_first.py",
    "baikal/typed_facts.py",
    "baikal/typed_fact_context.py",
    "scripts/evaluate_source_first.py",
]


def prepare(root=ROOT):
    case, supported, review = adopt(root)
    packet = {
        "version": 1,
        "mode": "reference_only_novelty_experimental",
        "max_requests": 1,
        "published": False,
        "expert_review": False,
        "pins": {p: sha256(root / p) for p in PINS},
        "source_review": review,
        "supported": supported,
        "input": make_input(root, case, supported, review),
        "model_profile": profile_7b(),
        "prompt": PROMPT,
        "max_tokens": 1200,
    }
    pid, path = save_packet(root, "novelty_experiments", packet)
    return {"experiment_id": pid, "path": path, "generation_requests": 0}


def load(root, pid):
    if len(pid) != 64 or any(c not in "0123456789abcdef" for c in pid):
        raise ValueError("Invalid experiment ID")
    m = read_json(root / "data/wiki_updates/novelty_experiments" / f"{pid}.json")
    if (
        digest(m) != pid
        or m["max_requests"] != 1
        or m["published"] is not False
        or m["model_profile"] != profile_7b()
        or m["prompt"] != PROMPT
    ):
        raise ValueError("Experiment integrity mismatch")
    if m["pins"] != {p: sha256(root / p) for p in PINS}:
        raise ValueError("Preregistered code/protocol/evidence changed")
    case, supported, review = adopt(root)
    if (
        m["supported"] != supported
        or m["source_review"] != review
        or m["input"] != make_input(root, case, supported, review)
    ):
        raise ValueError("Immutable facts, sources or Wiki changed")
    return m


def call(root, pid, m, endpoint, opener, runtime):
    payload = {
        "model": "baikal-local-evaluation",
        "temperature": 0,
        "max_tokens": m["max_tokens"],
        "messages": [
            {"role": "system", "content": m["prompt"]},
            {"role": "user", "content": json.dumps(m["input"], ensure_ascii=False)},
        ],
        "response_format": {
            "type": "json_schema",
            "json_schema": {
                "name": "novelty_relations",
                "strict": True,
                "schema": response_schema(m["input"]),
            },
        },
    }
    budget = preflight(endpoint, opener, payload)
    record = {
        "experiment_id": pid,
        "request": payload,
        "runtime": runtime,
        "published": False,
        "context_preflight": budget,
    }
    save_packet(root, "novelty_inputs", record)
    reserve_attempt(root, pid, "selenga-sediments", "compare")
    started = time.monotonic()
    raw = parsed = checked = response_data = None
    errors = []
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
        response_data = json.loads(raw)
        choice = response_data["choices"][0]
        if choice["finish_reason"] != "stop":
            raise ValueError("Incomplete response, not accepted")
        parsed = json.loads(choice["message"]["content"])
        checked = check_result(parsed, m["input"])
        load(root, pid)
    except (ValueError, OSError, KeyError, TypeError, IndexError) as exc:
        checked = None
        errors = [f"{type(exc).__name__}: {exc}"]
    run = {
        **record,
        "raw_response": raw,
        "parsed": parsed,
        "checked": checked,
        "errors": errors,
        "elapsed_seconds": round(time.monotonic() - started, 3),
        "usage": response_data.get("usage")
        if isinstance(response_data, dict)
        else None,
    }
    rid, path = save_packet(root, "novelty_runs", run)
    return {
        "experiment_id": pid,
        "run_id": rid,
        "path": path,
        "errors": errors,
        "published": False,
    }


def execute(args, root=ROOT):
    m = load(root, args.experiment)
    marker = (
        root
        / "data/llm-evaluation"
        / f"source-first-{args.experiment}.attempts/selenga-sediments-compare.json"
    )
    if marker.exists():
        raise ValueError("Single generation slot already consumed; no retry")
    with local_server(
        root, args.model_file.resolve(), args.server_binary.resolve(), profile_7b()
    ) as (endpoint, opener, runtime):
        outcome = call(root, args.experiment, m, endpoint, opener, runtime)
    return {**outcome, "runtime": runtime, "expert_review": False}


if __name__ == "__main__":
    p = argparse.ArgumentParser(description=__doc__)
    mode = p.add_mutually_exclusive_group(required=True)
    mode.add_argument("--prepare", action="store_true")
    mode.add_argument("--run", action="store_true")
    mode.add_argument("--draft", action="store_true")
    p.add_argument("--experiment")
    p.add_argument("--run-record", type=Path)
    p.add_argument("--semantic-review", type=Path)
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
        p.error("Output exists; preserve previous results")
    if not args.prepare and not args.experiment:
        p.error("Experiment ID required")
    if args.draft and (not args.run_record or not args.semantic_review):
        p.error("Run record and separate semantic review required")
    try:
        if args.prepare:
            outcome = prepare()
        elif args.run:
            outcome = execute(args)
        else:
            m = load(ROOT, args.experiment)
            run = read_json(args.run_record)
            outcome = {
                "experiment_id": args.experiment,
                "draft": save_reviewed_draft(
                    ROOT, m, digest(run), run, read_json(args.semantic_review)
                ),
                "published": False,
            }
        atomic_json(args.output, outcome)
        print(json.dumps(outcome, ensure_ascii=False))
    except (ValueError, OSError, KeyError) as exc:
        p.exit(1, f"Novelty comparison stopped: {exc}\n")
