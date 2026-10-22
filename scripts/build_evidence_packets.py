"""Build immutable scoped packets from separately reviewed cards; no PDF/LLM I/O."""

import argparse
import json
from pathlib import Path
from baikal.core import ROOT, read_json, sha256, atomic_json
from baikal.evidence_packets import validate, make_packet, retain_packet, retrieve
from baikal.packet_context import with_article_claims
from baikal.packet_terms import check_terms
from baikal.updates import save_packet


def build(root=ROOT, registry_path="datasets/evidence_packet_registry.json",
          requests_path="datasets/evidence_packet_requests.json", require_local=True):
    data = read_json(root / registry_path)
    checked = validate(data, root, require_local)
    check_terms(data, root)
    suite = read_json(root / requests_path)
    results = []
    for request in suite["requests"]:
        packet = make_packet(request, data, root)
        pid, path = retain_packet(packet, root)
        cid, cpath = save_packet(root, "packet_contexts", with_article_claims(packet))
        results.append({"id": request["id"], "packet_id": pid, "packet_path": path,
                        "prepared_context_id": cid, "prepared_context_path": cpath,
                        "readiness": packet["readiness"], "decision": packet["structural_decision"],
                        "search_leads": retrieve(request["search_terms"], root),
                        "related_articles": [{"id": a["id"], "scope_relation": a["scope_relation"],
                                              "fact_overlap": a["fact_overlap"]} for a in packet["related_articles"]]})
    return {"version": 1, "pins": {p: sha256(root/p) for p in [registry_path, requests_path,
             "baikal/evidence_packets.py", "baikal/packet_context.py", "baikal/packet_terms.py",
             "config/packet_indicators.json", "scripts/build_evidence_packets.py"]},
            "source_check": checked, "packets": results,
            "published": False, "expert_review": False, "editorial_decision": None}


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--registry", default="datasets/evidence_packet_registry.json")
    parser.add_argument("--requests", default="datasets/evidence_packet_requests.json")
    parser.add_argument("--check", action="store_true")
    parser.add_argument("--require-local", action="store_true")
    parser.add_argument("--output", type=Path)
    args = parser.parse_args()
    try:
        if args.check:
            data = read_json(ROOT/args.registry)
            result = {**validate(data, ROOT, args.require_local), **check_terms(data, ROOT)}
        else:
            if not args.output or args.output.exists():
                parser.error("A new output file is required")
            result = build(ROOT, args.registry, args.requests, require_local=True)
            atomic_json(args.output, result)
        print(json.dumps(result, ensure_ascii=False, indent=2))
    except (ValueError, OSError, KeyError) as exc:
        parser.exit(1, f"Evidence packet check stopped: {exc}\n")
