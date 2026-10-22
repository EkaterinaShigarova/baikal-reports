"""Reproduce staged updates using three actual cached pages, without a model."""

import argparse
import hashlib
import json
import shutil
import tempfile
from pathlib import Path
from baikal.core import ROOT, read_json, atomic_json
from baikal.updates import propose, load_proposal


def run():
    sources = read_json(ROOT / "config/sources.json")["sources"]
    selected = []
    for year, n in [(2016, 222), (2024, 67), (2015, 26)]:
        source = next(s for s in sources if s.get("report_year") == year)
        path = ROOT / "data/processed" / source["sha256"] / "pages" / f"{n:04d}.json"
        page = read_json(path)
        if not page or page.get("status") != "ok":
            raise ValueError(f"Required cached source page unavailable: {year}/{n}")
        selected.append((source, page))
    with tempfile.TemporaryDirectory(prefix="baikal-update-check-") as temp:
        root = Path(temp)
        for name in [
            "config/sources.json",
            "config/encyclopedia.json",
            "config/update_rules.json",
            "datasets/scientific_review.json",
            "datasets/scientific_review_claims.json",
            "datasets/observations.json",
            "datasets/observations.csv",
        ]:
            dest = root / name
            dest.parent.mkdir(parents=True, exist_ok=True)
            shutil.copyfile(ROOT / name, dest)
        shutil.copytree(ROOT / "wiki/syntheses", root / "wiki/syntheses")
        article_count = len(list((root / "wiki/syntheses").glob("*.md")))
        before = {
            str(p.relative_to(root)): p.read_bytes()
            for p in (root / "wiki/syntheses").glob("*.md")
        }
        before.update(
            {
                name: (root / name).read_bytes()
                for name in ["datasets/observations.json", "datasets/observations.csv"]
            }
        )
        refs = []
        packets = []
        for source, page in selected:
            dest = (
                root
                / "data/processed"
                / source["sha256"]
                / "pages"
                / f"{page['page']:04d}.json"
            )
            atomic_json(dest, {k: page[k] for k in ["status", "page", "text"]})
            report = propose(source["id"], pages=[page["page"]], root=root)
            assert not report["llm_called"] and not report["published"]
            for p in report["proposals"]:
                packet = load_proposal(root, p["id"], p["topic"])
                assert (
                    packet["status"] == "pending_semantic_review"
                    and not packet["published"]
                )
                for old in packet["preceding_versions"]:
                    assert (root / old["path"]).read_bytes() == before[
                        "wiki/" + old["article"]
                    ]
                packets.append(
                    {
                        "id": p["id"],
                        "topic": p["topic"],
                        "kind": p["kind"],
                        "known_issue_ids": packet["known_issue_ids"],
                    }
                )
            refs.append(
                {
                    "source_id": source["id"],
                    "sha256": source["sha256"],
                    "report_year": source["report_year"],
                    "physical_page": page["page"],
                    "text_sha256": hashlib.sha256(page["text"].encode()).hexdigest(),
                }
            )
        events = (root / "data/wiki_updates/events.jsonl").read_bytes()
        for source, page in selected:
            repeat = propose(source["id"], pages=[page["page"]], root=root)
            assert repeat["unchanged_pages"] == 1 and not repeat["proposals"]
        assert events == (root / "data/wiki_updates/events.jsonl").read_bytes()
        assert all((root / p).read_bytes() == content for p, content in before.items())
        assert len(list((root / "wiki/syntheses").glob("*.md"))) == article_count
        return {
            "test_kind": "isolated simulation of arrival of existing cached page texts",
            "documents": 3,
            "pages": 3,
            "source_pages": refs,
            "proposals": packets,
            "proposal_count": len(packets),
            "published_articles_changed": 0,
            "new_articles_published": 0,
            "existing_articles_preserved": article_count,
            "reviewed_observations_changed": 0,
            "repeated_run_new_proposals": 0,
            "preceding_versions_preserved": True,
            "manual_notes_preserved": True,
            "llm_inference_executed": False,
            "model_http_fixture_used": False,
            "new_pdf_downloads": 0,
            "pdf_extraction_or_ocr": False,
            "limitations": [
                "Source discovery/download was not exercised against the network.",
                "Routing and text novelty do not prove new knowledge or semantic contradiction detection.",
                "Semantic generation by a real local LLM remains untested.",
            ],
        }


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path)
    args = parser.parse_args()
    try:
        result = run()
    except (ValueError, AssertionError) as exc:
        parser.exit(1, f"Update workflow check failed: {exc}\n")
    if args.output:
        atomic_json(args.output, result)
    print(json.dumps(result, ensure_ascii=False, indent=2))
