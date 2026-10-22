"""Read-only cached-topic leads; prepare/run a bounded local editorial experiment."""

import argparse
import hashlib
import json
import time
import urllib.request
from pathlib import Path

from baikal.core import ROOT, read_json, sha256, atomic_json
from baikal.local_models import profile_7b
from baikal.topic_discovery import PROMPT, check_bundle, context, schema, gate, retain, scan
from baikal.typed_fact_context import preflight
from baikal.updates import save_packet, digest
try:
    from scripts.evaluate_source_first import local_server
except ModuleNotFoundError:
    from evaluate_source_first import local_server

PINS = ["baikal/topic_discovery.py", "scripts/discover_topics.py",
        "datasets/topic_discovery_tasks.json", "docs/TOPIC_DISCOVERY_PROTOCOL.md",
        "config/encyclopedia.json", "config/sources.json"]


def prepare(root=ROOT, tasks_path="datasets/topic_discovery_tasks.json"):
    suite = read_json(root / tasks_path)
    if not 1 <= len(suite["tasks"]) <= 4:
        raise ValueError("One to four tasks required")
    cases = []
    for bundle in suite["tasks"]:
        check_bundle(bundle, root, True)
        wire = context(bundle, root)
        cases.append({"bundle": bundle, "wire": wire})
    packet = {"pins": {p: sha256(root / p) for p in [*PINS, tasks_path]},
              "cases": cases, "max_requests": len(cases), "prompt": PROMPT,
              "model_profile": profile_7b(), "published": False,
              "tasks_path": tasks_path, "expert_review": False}
    pid, path = save_packet(root, "topic_experiments", packet)
    return {"experiment_id": pid, "path": path, "requests": 0}


def load(root, pid):
    if not isinstance(pid, str) or len(pid) != 64 or any(c not in "0123456789abcdef" for c in pid):
        raise ValueError("Invalid experiment ID")
    packet = read_json(root / "data/wiki_updates/topic_experiments" / f"{pid}.json")
    if digest(packet) != pid or packet["prompt"] != PROMPT:
        raise ValueError("Immutable experiment mismatch")
    for p, h in packet["pins"].items():
        if sha256(root / p) != h:
            raise ValueError("Frozen input changed")
    for case in packet["cases"]:
        check_bundle(case["bundle"], root, True)
        for a in case["wire"]["articles"]:
            if sha256(root / "wiki" / a["path"]) != a["sha256"]:
                raise ValueError("Frozen article changed")
    return packet


def execute(args, root=ROOT):
    packet = load(root, args.experiment)
    marker_dir = root / "data/llm-evaluation" / f"topic-{args.experiment}.attempts"
    if marker_dir.exists():
        raise ValueError("Experiment already reserved; no retries")
    # Reserve the entire bounded experiment, including crash/failure cases.
    marker_dir.mkdir(parents=True)
    atomic_json(marker_dir / "reservation.json", {"slots": packet["max_requests"]})
    results = []
    with local_server(root, args.model_file.resolve(), args.server_binary.resolve(), profile_7b()) as (endpoint, opener, runtime):
        for case in packet["cases"]:
            bundle, wire = case["bundle"], case["wire"]
            payload = {"model": "baikal-local-evaluation", "temperature": 0, "max_tokens": 950,
                       "messages": [{"role": "system", "content": PROMPT},
                                    {"role": "user", "content": json.dumps(wire, ensure_ascii=False)}],
                       "response_format": {"type": "json_schema", "json_schema": {
                           "name": "topic_editorial_plan", "strict": True, "schema": schema(wire)}}}
            budget = preflight(endpoint, opener, payload)
            if budget["prompt_tokens_native"] > 6500:
                raise ValueError("Compact evidence context exceeded")
            raw = body = parsed = checked = None
            errors = []
            started = time.monotonic()
            atomic_json(marker_dir / f"{bundle['id']}.json", {"started": True})
            try:
                request = urllib.request.Request(endpoint, data=json.dumps(payload).encode(),
                                                 headers={"Content-Type": "application/json"})
                with opener.open(request, timeout=600) as response:
                    raw = response.read(2*1024**2+1)
                if len(raw) > 2*1024**2:
                    raise ValueError("Response too large")
                body = json.loads(raw)
                if body["choices"][0]["finish_reason"] != "stop":
                    raise ValueError("Incomplete response")
                parsed = json.loads(body["choices"][0]["message"]["content"])
                checked = gate(parsed, bundle, wire)
                load(root, args.experiment)
            except (ValueError, OSError, KeyError, TypeError, IndexError) as exc:
                errors.append(f"{type(exc).__name__}: {exc}")
            run = {"experiment_id": args.experiment, "case_id": bundle["id"],
                   "request": payload, "raw_response": raw.decode() if raw else None,
                   "parsed": parsed, "check": checked, "errors": errors,
                   "elapsed_seconds": round(time.monotonic()-started, 3),
                   "usage": body.get("usage") if isinstance(body, dict) else None,
                   "context_preflight": budget, "published": False, "expert_review": False}
            rid, path = save_packet(root, "topic_runs", run)
            retained = retain(parsed, bundle, wire, rid, root) if checked and not checked["errors"] else None
            result = {"case_id": bundle["id"], "run_id": rid, "path": path,
                      "raw_response_sha256": hashlib.sha256(raw).hexdigest() if raw else None,
                      "parsed": parsed, "check": checked, "errors": errors,
                      "elapsed_seconds": run["elapsed_seconds"], "usage": run["usage"],
                      "retained": retained, "context_preflight": budget}
            results.append(result)
            # Save partial progress after every real response, not just at exit.
            atomic_json(args.output, {"experiment_id": args.experiment, "cases": results,
                                     "status": "in_progress", "published": False})
            print(json.dumps({"case_id": bundle["id"], "elapsed": run["elapsed_seconds"],
                              "errors": errors, "decision": parsed.get("decision") if parsed else None}), flush=True)
    return {"experiment_id": args.experiment, "cases": results, "runtime": runtime,
            "status": "completed", "published": False, "expert_review": False}


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    mode = parser.add_mutually_exclusive_group(required=True)
    for name in ("scan", "prepare", "run"):
        mode.add_argument("--" + name, action="store_true")
    parser.add_argument("--source", help="Read cached headings for this source only")
    parser.add_argument("--tasks", default="datasets/topic_discovery_tasks.json")
    parser.add_argument("--experiment")
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--model-file", type=Path, default=ROOT / "data/models/qwen25-7b-q4km/qwen2.5-7b-instruct-q4_k_m-00001-of-00002.gguf")
    parser.add_argument("--server-binary", type=Path, default=ROOT / "data/llm-runtime/build-avx2/bin/llama-server")
    args = parser.parse_args()
    if args.output.exists():
        parser.error("Output already exists; choose a new path")
    try:
        result = scan(ROOT, args.source) if args.scan else prepare(ROOT, args.tasks) if args.prepare else execute(args)
        atomic_json(args.output, result)
        print(json.dumps({"output": str(args.output), "status": result.get("status", "saved")}, ensure_ascii=False))
    except (ValueError, OSError, KeyError) as exc:
        parser.exit(1, f"Topic discovery stopped: {exc}\n")
