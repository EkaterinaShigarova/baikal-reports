"""Lossless citation deduplication and local preflight, without model generation."""

import copy
import json
import urllib.parse
import urllib.request
from .assessment_v2 import numbers
from .source_first import comparison_input


def comparison_wire(case, supported, review):
    wire = comparison_input(case, supported)
    sources = {}

    def compact(record):
        out = copy.deepcopy(record)
        refs = out.pop("evidence")
        ids = []
        for ref in refs:
            matches = [f"S{i + 1}" for i, r in enumerate(case["evidence"]) if r == ref]
            if len(matches) != 1:
                raise ValueError("Citation does not uniquely match frozen source")
            sid = matches[0]
            sources[sid] = copy.deepcopy(ref)
            ids.append(sid)
        out["evidence_ids"] = ids
        return out

    wire["facts"] = [compact(f) for f in supported["facts"]]
    wire["source_conflicts"] = [compact(c) for c in supported["source_conflicts"]]
    wire["source_fragments"] = [
        {"id": sid, **ref} for sid, ref in sorted(sources.items())
    ]
    wire["source_review_notes"] = {
        "fact_decisions": {
            f["fact_id"]: copy.deepcopy(review["fact_decisions"][f["fact_id"]])
            for f in supported["facts"]
        },
        "conflict_decisions": {
            c["conflict_id"]: copy.deepcopy(
                review["conflict_decisions"][c["conflict_id"]]
            )
            for c in supported["source_conflicts"]
        },
    }
    wire["wire_format"] = "shared_exact_citations_v1"
    # Assert lossless representation before an LLM sees anything.
    if (
        expand_records(wire, "facts") != supported["facts"]
        or expand_records(wire, "source_conflicts") != supported["source_conflicts"]
    ):
        raise ValueError("Compacted facts differ from source-checked originals")
    return wire


def expand_records(wire, group):
    refs = {
        r["id"]: {k: v for k, v in r.items() if k != "id"}
        for r in wire["source_fragments"]
    }
    output = []
    for record in wire[group]:
        out = copy.deepcopy(record)
        ids = out.pop("evidence_ids")
        out["evidence"] = [copy.deepcopy(refs[i]) for i in ids]
        output.append(out)
    return output


def validate_reason_bindings(result, supplied):
    facts = {f["fact_id"]: f for f in supplied["facts"]}
    known = numbers(supplied["wiki_context"])
    for decision in result["decisions"]:
        fact = facts.get(decision["fact_id"])
        if fact is None:
            raise ValueError("Unknown immutable fact ID in rationale")
        allowed = (
            numbers(fact["text"]) | numbers(str(fact.get("observation_year"))) | known
        )
        if numbers(decision["reason"]) - allowed:
            raise ValueError(
                "Rationale imports a number not belonging to this fact or Wiki"
            )


def count_prompt(endpoint, opener, payload):
    url = urllib.parse.urlsplit(endpoint)
    if (
        url.scheme != "http"
        or url.hostname != "127.0.0.1"
        or url.username
        or url.password
    ):
        raise ValueError("Tokenizer diagnostics are loopback-only")
    base = f"{url.scheme}://{url.netloc}"

    def post(path, body):
        req = urllib.request.Request(
            base + path,
            data=json.dumps(body).encode(),
            headers={"Content-Type": "application/json"},
        )
        with opener.open(req, timeout=30) as response:
            raw = response.read(2 * 1024**2 + 1)
            if len(raw) > 2 * 1024**2:
                raise ValueError("Tokenizer response too large")
            return json.loads(raw)

    formatted = post("/apply-template", payload)
    if not isinstance(formatted.get("prompt"), str):
        raise ValueError("Unexpected native chat template result")
    tokens = post(
        "/tokenize",
        {"content": formatted["prompt"], "add_special": False, "parse_special": True},
    ).get("tokens")
    if not isinstance(tokens, list) or any(type(t) is not int for t in tokens):
        raise ValueError("Unexpected tokenizer result")
    return len(tokens)


def preflight(endpoint, opener, payload, context_tokens=8192):
    count = count_prompt(endpoint, opener, payload)
    reserve = 128
    if (
        type(payload.get("max_tokens")) is not int
        or payload["max_tokens"] <= 0
        or count + payload["max_tokens"] + reserve > context_tokens
    ):
        raise ValueError(
            f"Context budget exceeded before generation: prompt={count}, output={payload.get('max_tokens')}, reserve={reserve}, context={context_tokens}"
        )
    return {
        "prompt_tokens_native": count,
        "output_tokens_reserved": payload["max_tokens"],
        "safety_reserve": reserve,
        "context_tokens": context_tokens,
        "generation_requests": 0,
    }
