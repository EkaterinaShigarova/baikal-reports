"""Bounded local source-first evaluation: prepare, extract, source gate, compare.

No downloading, publishing, paid API or automatic source-review decisions.
"""

import argparse
import contextlib
import json
import resource
import socket
import subprocess
import time
import urllib.request
from pathlib import Path

from baikal.core import ROOT, atomic_json, read_json, sha256
from baikal.evaluation import resources
from baikal.llm import NoRedirect
from baikal.local_models import profile_7b, verify_7b
from baikal.source_first import (
    EXTRACT_PROMPT,
    COMPARE_PROMPT,
    freeze_cases,
    source_input,
    check_sources,
    extract_checked,
    select_supported,
    comparison_input,
    compare_checked,
    save_draft,
)
from baikal.updates import digest, save_packet

try:  # Both direct CLI execution and unittest package imports.
    from scripts.evaluate_local_llm import runtime_info
except ModuleNotFoundError:
    from evaluate_local_llm import runtime_info


def prepare(root=ROOT):
    packet = {
        "version": 1,
        "mode": "source_first_experimental",
        "max_requests": 4,
        "protocol_sha256": sha256(root / "docs/SOURCE_FIRST_PROTOCOL.md"),
        "module_sha256": sha256(root / "baikal/source_first.py"),
        "runner_sha256": sha256(root / "scripts/evaluate_source_first.py"),
        "extract_prompt": EXTRACT_PROMPT,
        "compare_prompt": COMPARE_PROMPT,
        "model_profile": profile_7b(),
        "published": False,
        **freeze_cases(root),
    }
    pid, path = save_packet(root, "source_first_experiments", packet)
    return {"experiment_id": pid, "path": path, "generation_attempts": 0}


def load_experiment(root, pid):
    if len(pid) != 64 or any(c not in "0123456789abcdef" for c in pid):
        raise ValueError("Invalid experiment ID")
    manifest = read_json(
        root / "data/wiki_updates/source_first_experiments" / f"{pid}.json", {}
    )
    if (
        digest(manifest) != pid
        or manifest.get("max_requests") != 4
        or manifest.get("published") is not False
    ):
        raise ValueError("Immutable experiment mismatch")
    for path, key in [
        ("docs/SOURCE_FIRST_PROTOCOL.md", "protocol_sha256"),
        ("baikal/source_first.py", "module_sha256"),
        ("scripts/evaluate_source_first.py", "runner_sha256"),
        (manifest["baseline_path"], "baseline_sha256"),
    ]:
        if sha256(root / path) != manifest[key]:
            raise ValueError("Protocol/code/baseline changed since preregistration")
    if (
        manifest["model_profile"] != profile_7b()
        or manifest["extract_prompt"] != EXTRACT_PROMPT
        or manifest["compare_prompt"] != COMPARE_PROMPT
    ):
        raise ValueError("Pinned model/prompt changed")
    for case in manifest["cases"]:
        check_sources(case, root)
        if (
            sha256(root / case["baseline_run_path"]) != case["baseline_run_sha256"]
            or sha256(root / "wiki" / case["article"]["path"])
            != case["article"]["sha256"]
        ):
            raise ValueError("Frozen baseline/article changed")
    return manifest


def reserve_attempt(root, experiment_id, topic, stage):
    if topic not in {"baikal-sturgeon", "selenga-sediments"} or stage not in {
        "extract",
        "compare",
    }:
        raise ValueError("Only four fixed generation slots allowed")
    directory = root / "data/llm-evaluation" / f"source-first-{experiment_id}.attempts"
    directory.mkdir(parents=True, exist_ok=True)
    path = directory / f"{topic}-{stage}.json"
    # Exclusive creation consumes the slot before HTTP, even on a crash/error.
    with path.open("x") as stream:
        json.dump(
            {"experiment_id": experiment_id, "topic": topic, "stage": stage}, stream
        )


@contextlib.contextmanager
def local_server(root, model, server, profile):
    res = resources()
    bundle = verify_7b(model, res)
    previous = read_json(root / "datasets/llm_7b_comparison.json")["runtime"]
    if sha256(server) != previous["server_sha256"]:
        raise ValueError("Runtime differs from previous controlled comparison")
    runtime = {
        "model_bundle": bundle,
        "server_sha256": sha256(server),
        **runtime_info(server),
        "threads": 4,
        "context_tokens": 8192,
        "gpu_layers": 0,
        "resources_before": res,
    }
    with socket.socket() as sock:
        sock.bind(("127.0.0.1", 0))
        port = sock.getsockname()[1]
    directory = root / "data/llm-evaluation"
    directory.mkdir(parents=True, exist_ok=True)
    log_path = directory / f"source-first-server-{time.time_ns()}.log"
    runtime["server_log_path"] = str(log_path.relative_to(root))
    runtime["timeout_seconds"] = profile["request_timeout_seconds"]
    command = [
        str(server),
        "-m",
        str(model),
        "--alias",
        "baikal-local-evaluation",
        "-c",
        "8192",
        "-t",
        "4",
        "-tb",
        "4",
        "-ngl",
        "0",
        "--parallel",
        "1",
        "--batch-size",
        "256",
        "--host",
        "127.0.0.1",
        "--port",
        str(port),
    ]
    runtime["command"] = command
    opener = urllib.request.build_opener(NoRedirect(), urllib.request.ProxyHandler({}))
    started = time.monotonic()
    with log_path.open("x") as log:
        process = subprocess.Popen(command, stdout=log, stderr=subprocess.STDOUT)
        try:
            deadline = time.monotonic() + 60
            while True:
                if process.poll() is not None:
                    raise ValueError(
                        f"Runtime exited ({process.returncode}); inspect {log_path}"
                    )
                try:
                    with opener.open(
                        f"http://127.0.0.1:{port}/health", timeout=1
                    ) as response:
                        if response.status == 200:
                            break
                except OSError:
                    if time.monotonic() >= deadline:
                        raise ValueError("Local server readiness timed out")
                    time.sleep(0.3)
            yield f"http://127.0.0.1:{port}/v1/chat/completions", opener, runtime
        finally:
            process.terminate()
            try:
                process.wait(timeout=10)
            except subprocess.TimeoutExpired:
                process.kill()
                process.wait()
            runtime["server_wall_seconds"] = round(time.monotonic() - started, 3)
            runtime["peak_child_rss_kib"] = resource.getrusage(
                resource.RUSAGE_CHILDREN
            ).ru_maxrss


def generate(
    root, experiment_id, case, stage, supplied, endpoint, opener, runtime, validator
):
    payload = {
        "model": "baikal-local-evaluation",
        "temperature": 0,
        "max_tokens": 1800,
        "messages": [
            {
                "role": "system",
                "content": EXTRACT_PROMPT if stage == "extract" else COMPARE_PROMPT,
            },
            {"role": "user", "content": json.dumps(supplied, ensure_ascii=False)},
        ],
    }
    record = {
        "experiment_id": experiment_id,
        "topic": case["topic"],
        "stage": stage,
        "request": payload,
        "runtime": runtime,
        "published": False,
    }
    save_packet(root, "source_first_inputs", record)
    request = urllib.request.Request(
        endpoint,
        data=json.dumps(payload).encode(),
        headers={"Content-Type": "application/json"},
    )
    reserve_attempt(root, experiment_id, case["topic"], stage)
    started = time.monotonic()
    raw, response_data, parsed, checked, errors = None, None, None, None, []
    try:
        with opener.open(request, timeout=600) as response:
            content = response.read(2 * 1024**2 + 1)
            if len(content) > 2 * 1024**2:
                raise ValueError("Response exceeded bounded 2 MiB")
            raw = content.decode("utf-8")
        response_data = json.loads(raw)
        message = response_data["choices"][0]
        if message.get("finish_reason") != "stop":
            raise ValueError("Response not normally completed; no partial acceptance")
        text = message["message"]["content"].strip()
        if text.startswith("```json") and text.endswith("```"):
            text = text[7:-3].strip()
        elif text.startswith("```") and text.endswith("```"):
            text = text[3:-3].strip()
        parsed = json.loads(text)
        checked = validator(parsed)
        check_sources(case, root)
        if sha256(root / "wiki" / case["article"]["path"]) != case["article"]["sha256"]:
            raise ValueError("Article changed during generation")
    except (
        OSError,
        ValueError,
        KeyError,
        IndexError,
        TypeError,
        AttributeError,
    ) as exc:
        errors = [f"{type(exc).__name__}: {exc}"]
        checked = None
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
    run_id, path = save_packet(root, "source_first_runs", run)
    return {
        "topic": case["topic"],
        "run_id": run_id,
        "path": path,
        "errors": errors,
        "checked": checked,
        "elapsed_seconds": run["elapsed_seconds"],
    }


def phase(args, root=ROOT):
    manifest = load_experiment(root, args.experiment)
    profile = manifest["model_profile"]
    stage = "extract" if args.extract else "compare"
    jobs = []
    if args.compare:
        prior = read_json(args.extraction)
        review = read_json(args.review)
        if (
            prior["experiment_id"] != args.experiment
            or review.get("experiment_id") != args.experiment
            or prior.get("stage") != "extract"
        ):
            raise ValueError("Review/extraction experiment mismatch")
    for case in manifest["cases"]:
        if stage == "extract":
            jobs.append((case, source_input(case), None, None))
        else:
            result = next(r for r in prior["results"] if r["topic"] == case["topic"])
            run = read_json(root / result["path"])
            if (
                digest(run) != result["run_id"]
                or run["experiment_id"] != args.experiment
                or run["stage"] != "extract"
                or run["errors"]
                or run["checked"] is None
            ):
                raise ValueError("Valid immutable extraction run required")
            # Recompute checks rather than trusting an edited summary.
            bundle = extract_checked(run["parsed"], case)
            if bundle != run["checked"]:
                raise ValueError("Extraction checks changed")
            source_review = review["cases"][case["topic"]]
            supported = select_supported(bundle, source_review)
            review_id, _ = save_packet(
                root,
                "source_first_reviews",
                {
                    "experiment_id": args.experiment,
                    "topic": case["topic"],
                    **source_review,
                },
            )
            if supported["facts"]:
                jobs.append(
                    (case, comparison_input(case, supported), supported, review_id)
                )
    if not jobs:
        return {
            "experiment_id": args.experiment,
            "stage": stage,
            "status": "skipped_no_supported_facts",
            "results": [],
        }
    # Check used slots before loading weights; reserve again exclusively before HTTP.
    for case, _, _, _ in jobs:
        marker = (
            root
            / "data/llm-evaluation"
            / f"source-first-{args.experiment}.attempts"
            / f"{case['topic']}-{stage}.json"
        )
        if marker.exists():
            raise ValueError("Generation slot already consumed; no retry allowed")
    results = []
    with local_server(
        root, args.model_file.resolve(), args.server_binary.resolve(), profile
    ) as (endpoint, opener, runtime):
        for case, supplied, supported, review_id in jobs:
            validator = (
                (lambda x, c=case: extract_checked(x, c))
                if stage == "extract"
                else (lambda x, s=supplied: compare_checked(x, s))
            )
            result = generate(
                root,
                args.experiment,
                case,
                stage,
                supplied,
                endpoint,
                opener,
                runtime,
                validator,
            )
            if stage == "compare" and not result["errors"]:
                result["draft"] = save_draft(
                    root,
                    case,
                    supported,
                    result["checked"],
                    result["run_id"],
                    review_id,
                )
            results.append(result)
            print(
                json.dumps(
                    {
                        "stage": stage,
                        "topic": case["topic"],
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
        "runtime": runtime,
        "published": False,
        "expert_review": False,
    }


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    mode = parser.add_mutually_exclusive_group(required=True)
    mode.add_argument("--prepare", action="store_true")
    mode.add_argument("--extract", action="store_true")
    mode.add_argument("--compare", action="store_true")
    parser.add_argument("--experiment")
    parser.add_argument(
        "--model-file",
        type=Path,
        default=ROOT
        / "data/models/qwen25-7b-q4km/qwen2.5-7b-instruct-q4_k_m-00001-of-00002.gguf",
    )
    parser.add_argument(
        "--server-binary",
        type=Path,
        default=ROOT / "data/llm-runtime/build-avx2/bin/llama-server",
    )
    parser.add_argument("--extraction", type=Path)
    parser.add_argument("--review", type=Path)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    if not args.prepare and not args.experiment:
        parser.error("--extract/--compare require --experiment")
    if args.compare and (not args.extraction or not args.review):
        parser.error("--compare requires --extraction and --review")
    if args.output.exists():
        parser.error("Output exists; preserve preceding results")
    try:
        outcome = prepare() if args.prepare else phase(args)
        atomic_json(args.output, outcome)
        print(
            json.dumps(
                {
                    "output": str(args.output),
                    "experiment_id": outcome.get("experiment_id"),
                },
                indent=2,
            )
        )
    except (OSError, ValueError, KeyError) as exc:
        parser.exit(1, f"Source-first evaluation stopped: {exc}\n")
