"""Prepare cases or launch an actual, local GGUF runtime. No model downloads."""

import argparse
import json
import os
import resource
import signal
import socket
import subprocess
import time
import uuid
import urllib.request
from pathlib import Path
from baikal.core import ROOT, atomic_json, read_json, sha256
from baikal.evaluation import prepare_cases, resources
from baikal.llm import synthesize
from baikal.local_models import profile_7b, verify_7b


def draft_result(case_id, outcome):
    # synthesize returns status=draft; it must not overwrite the review status.
    return {"case": case_id, **outcome, "status": "draft_pending_semantic_review"}


def frozen_v2_inputs(prepared, baseline_path=ROOT / "datasets/llm_comparison_v2.json"):
    baseline = read_json(baseline_path)
    previous = baseline["v2"]["cases"]
    if (
        prepared.get("assessment_protocol") != "v2"
        or len(prepared["cases"]) != 2
        or len(previous) != 2
        or {c["topic"] for c in prepared["cases"]} != {c["topic"] for c in previous}
    ):
        raise ValueError("7B comparison requires exactly two frozen v2 cases")
    for case in prepared["cases"]:
        old = next(c for c in previous if c["topic"] == case["topic"])
        path = ROOT / old["run_record_path"]
        if sha256(path) != old["run_record_sha256"]:
            raise ValueError("Baseline model run integrity mismatch")
        run = read_json(path)
        current = read_json(ROOT / case["path"])
        messages = [
            {"role": "system", "content": current["system_prompt"]},
            {
                "role": "user",
                "content": json.dumps(current["input"], ensure_ascii=False),
            },
        ]
        if (
            messages != run["request"]["messages"]
            or run["request"]["temperature"] != 0
            or run["request"]["max_tokens"] != 1800
            or old["article_sha256"] != case["article_sha256"]
        ):
            raise ValueError("Frozen evidence/article/prompt/sampling mismatch")
    return {
        "path": str(baseline_path),
        "sha256": sha256(baseline_path),
        "matched_cases": 2,
        "model_messages_identical": True,
    }


def runtime_info(server):
    """Version-only checks do not initialize CPU backends or detect ISA mismatch."""
    outputs = {}
    for flag in ["--version", "--list-devices"]:
        result = subprocess.run(
            [str(server), flag], capture_output=True, text=True, timeout=20
        )
        if result.returncode:
            reason = (
                signal.Signals(-result.returncode).name
                if result.returncode < 0
                else f"exit {result.returncode}"
            )
            raise ValueError(
                f"Local runtime {flag} failed: {reason}; "
                "check CPU compatibility before inference (version alone is insufficient)"
            )
        outputs[flag] = (result.stdout + result.stderr).strip()
    return {
        "server_version": outputs["--version"],
        "backend_probe": {"exit_code": 0, "output": outputs["--list-devices"]},
    }


def run(args, prepared):
    if not args.model_file or not args.model_sha256 or not args.server_binary:
        raise ValueError(
            "--run requires --model-file, --model-sha256 and --server-binary"
        )
    model, server = args.model_file.resolve(), args.server_binary.resolve()
    res = resources()
    profile = getattr(args, "model_profile", "qwen25-1.5b-q4km")
    bundle, comparison, timeout = None, None, 180
    if profile == "qwen25-7b-q4km":
        bundle = verify_7b(model, res)
        if args.model_sha256 != bundle["files"][0]["sha256"]:
            raise ValueError("7B first shard checksum argument differs from profile")
        comparison = frozen_v2_inputs(prepared)
        timeout = profile_7b()["request_timeout_seconds"]
    elif (
        model.stat().st_size > 2 * 1024**3
        or res["memory_limit_bytes"] < 8 * 1024**3
        or res["memory_available_bytes"] < 4 * 1024**3
        or res["disk_free_bytes"] < 2 * 1024**3
    ):
        raise ValueError(
            "Bounded experiment requires model <=2 GiB, RAM limit >=8 GiB, available RAM >=4 GiB and free disk >=2 GiB"
        )
    with model.open("rb") as f:
        if f.read(4) != b"GGUF":
            raise ValueError("Expected a GGUF model header")
    if bundle is None and sha256(model) != args.model_sha256:
        raise ValueError("Model SHA256 mismatch")
    runtime = runtime_info(server)
    with socket.socket() as sock:
        sock.bind(("127.0.0.1", 0))
        port = sock.getsockname()[1]
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
    metadata = {
        "model_sha256": args.model_sha256,
        "model_file_name": model.name,
        "model_bytes": model.stat().st_size,
        "server_sha256": sha256(server),
        "model_profile": profile,
        "model_bundle": bundle,
        "frozen_comparison": comparison,
        "request_timeout_seconds": timeout,
        **runtime,
        "threads": 4,
        "context_tokens": 8192,
        "gpu_layers": 0,
    }
    log_path = ROOT / "data/llm-evaluation" / f"server-{uuid.uuid4().hex}.log"
    log_path.parent.mkdir(parents=True, exist_ok=True)
    metadata["server_log_path"] = str(log_path.relative_to(ROOT))
    opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))
    changed_env = ["BAIKAL_LLM_URL", "BAIKAL_LLM_MODEL", "BAIKAL_LLM_API_KEY"]
    old_env = {k: os.environ.get(k) for k in changed_env}
    started = time.monotonic()
    results = []
    received_generation = False
    with log_path.open("w") as log:
        process = subprocess.Popen(command, stdout=log, stderr=subprocess.STDOUT)
        try:
            deadline = time.monotonic() + 60
            while True:
                if process.poll() is not None:
                    raise ValueError(
                        f"Local runtime exited ({process.returncode}); inspect {log_path}"
                    )
                try:
                    with opener.open(
                        f"http://127.0.0.1:{port}/health", timeout=1
                    ) as response:
                        if response.status == 200:
                            break
                except OSError:
                    if time.monotonic() >= deadline:
                        raise ValueError("Local runtime readiness timed out")
                    time.sleep(0.3)
            os.environ["BAIKAL_LLM_URL"] = (
                f"http://127.0.0.1:{port}/v1/chat/completions"
            )
            os.environ["BAIKAL_LLM_MODEL"] = "baikal-local-evaluation"
            os.environ.pop("BAIKAL_LLM_API_KEY", None)
            for case in prepared["cases"]:
                previous = set((ROOT / "data/wiki_updates/llm_runs").glob("*.json"))
                try:
                    outcome = synthesize(
                        case["topic"],
                        proposal=case["proposal_id"],
                        assessment=True,
                        runtime=metadata,
                        assessment_protocol=prepared.get("assessment_protocol", "v1"),
                        request_timeout=timeout,
                    )
                    results.append(draft_result(case["id"], outcome))
                except ValueError as exc:
                    results.append(
                        {"case": case["id"], "status": "rejected", "reason": str(exc)}
                    )
                for path in (
                    set((ROOT / "data/wiki_updates/llm_runs").glob("*.json")) - previous
                ):
                    record = read_json(path)
                    try:
                        content = json.loads(record.get("raw_response", "{}"))[
                            "choices"
                        ][0]["message"]["content"]
                        received_generation |= bool(content)
                    except (ValueError, KeyError, IndexError, TypeError):
                        pass
        finally:
            process.terminate()
            try:
                process.wait(timeout=10)
            except subprocess.TimeoutExpired:
                process.kill()
                process.wait()
            for key, value in old_env.items():
                if value is None:
                    os.environ.pop(key, None)
                else:
                    os.environ[key] = value
    return {
        **prepared,
        "status": "generated_pending_semantic_review"
        if received_generation
        else "inference_failed",
        "model_inference_executed": received_generation,
        "runtime": metadata,
        "resources": res,
        "elapsed_seconds": round(time.monotonic() - started, 3),
        "peak_child_rss_kib": resource.getrusage(resource.RUSAGE_CHILDREN).ru_maxrss,
        "results": results,
        "model_generated_updates": sum(
            r["status"] == "draft_pending_semantic_review" for r in results
        ),
        "semantic_quality_evaluated": False,
        "hallucinations_assessed": False,
    }


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    mode = parser.add_mutually_exclusive_group()
    mode.add_argument("--prepare-only", action="store_true")
    mode.add_argument("--run", action="store_true")
    parser.add_argument("--model-file", type=Path)
    parser.add_argument("--model-sha256")
    parser.add_argument(
        "--model-profile",
        choices=["qwen25-1.5b-q4km", "qwen25-7b-q4km"],
        default="qwen25-1.5b-q4km",
    )
    parser.add_argument("--server-binary", type=Path)
    parser.add_argument("--assessment-protocol", choices=["v1", "v2"], default="v1")
    parser.add_argument(
        "--output", type=Path, default=ROOT / "data/llm-evaluation/result.json"
    )
    args = parser.parse_args()
    try:
        result = prepare_cases(assessment_protocol=args.assessment_protocol)
        result["resources"] = resources()
        if args.run:
            result = run(args, result)
        atomic_json(args.output, result)
        print(
            json.dumps(
                {
                    "status": result["status"],
                    "cases": len(result["cases"]),
                    "model_inference_executed": result["model_inference_executed"],
                    "model_generated_updates": result["model_generated_updates"],
                    "output": str(args.output),
                },
                ensure_ascii=False,
            )
        )
    except (ValueError, OSError, subprocess.SubprocessError) as exc:
        parser.exit(1, f"Local evaluation failed: {exc}\n")
