"""Isolated synthetic transfer probe; no network, real corpus edits or LLM calls."""
import argparse
import json
import tempfile
from pathlib import Path
from unittest.mock import patch
import pymupdf
from baikal import acquire, core, extract
from baikal.core import atomic_json, sha256
from baikal.updates import propose
from baikal.cross_topic import compare


def audit():
    with tempfile.TemporaryDirectory(prefix="baikal-transfer-") as directory:
        root = Path(directory)
        data = root / "data"
        raw = data / "raw"
        raw.mkdir(parents=True)
        pdf_path = root / "synthetic-new-report.pdf"
        with pymupdf.open() as pdf:
            for i in range(2):
                p = pdf.new_page()
                for line in range(6):
                    p.insert_text((35, 45+line*22),
                        f"SYNTHETIC ONLY. Forest monitoring page {i+1}, line {line+1}. "
                        "Native text for an isolated workflow test, not environmental data.")
            pdf.save(pdf_path)
        source_hash = sha256(pdf_path)
        pdf_path.rename(raw / f"{source_hash}.pdf")
        source = {"id":"unknown-future-report-control", "sha256":source_hash,
                  "pages":2,"report_year":None,"publication_year":None,"test_only":True}
        topic_id = "forest-transfer-control"
        article = root / "wiki/syntheses/forest-control.md"
        article.parent.mkdir(parents=True)
        article.write_text("# Synthetic control only\n\nExisting text.\n\n<!-- HUMAN NOTES -->\nPreserve this annotation.\n")
        notes_hash = sha256(article)
        atomic_json(root/"config/sources.json", {"sources":[source]})
        atomic_json(root/"config/encyclopedia.json", {"sections":[{"subtopics":[{"topics":[{
            "id":topic_id,"article":"syntheses/forest-control.md","title":"Synthetic forest control"}]}]}]})
        rules = root/"config/update_rules.json"
        atomic_json(rules, {"patterns":{topic_id:"Forest monitoring"}})
        observations = root/"datasets/observations.json"
        atomic_json(observations, [])
        observation_hash = sha256(observations)
        with patch.object(core,"DATA",data), patch.object(extract,"DATA",data), patch.object(acquire,"DATA",data), patch.object(acquire,"open_url",side_effect=AssertionError("No network permitted")):
            cached_pdf = acquire.download(source)
            assert sha256(cached_pdf) == source_hash
            first = extract.process(source,workers=1,limit=1)
            checkpoint = data/"processed"/source_hash/"pages/0001.json"
            before = [sha256(checkpoint), checkpoint.stat().st_mtime_ns]
            resumed = extract.process(source,workers=1)
            checkpoint_preserved = before == [sha256(checkpoint), checkpoint.stat().st_mtime_ns]
        result = propose(source["id"],pages=[1,2],limit=2,root=root)
        repeat = propose(source["id"],pages=[1,2],limit=2,root=root)
        # A new or unrecognised topic needs configuration/discovery, not silent novelty.
        atomic_json(rules,{"patterns":{topic_id:"This term is absent from the fixture"}})
        unmatched = propose(source["id"],pages=[1,2],limit=2,root=root)
        typed = {"features":{"object":"synthetic forest", "indicator":"fire count", "value":"12",
            "statistic":"count","unit":"event","territory":"synthetic area","period":{"year":2020},
            "message_kind":"reported_observation","value_kind":"numeric","attribution":"source_statement",
            "comparison_scope":"synthetic_only"},"method":{"kind":"synthetic counting"},"value_qualifier":"exact"}
        generic_comparison = compare(typed,typed)["relation"]
        answer = {"test_only":True,"real_reports_downloaded":0,"real_corpus_pages_processed":0,
            "synthetic_pages":2,"first_extracted_pages":first["extracted_pages"],
            "resumed_extracted_pages":resumed["extracted_pages"],"cached_pages_on_resume":resumed["cached_pages"],
            "first_checkpoint_preserved":checkpoint_preserved,"synthetic_pdf_sha256":source_hash,
            "new_topic_config_only":topic_id,"proposals":len(result["proposals"]),
            "repeated_page_proposals":len(repeat["proposals"]),
            "unmatched_routes_reported":len(unmatched["notices"]),
            "generic_comparison":generic_comparison,"notes_preserved":sha256(article)==notes_hash,
            "reviewed_observations_preserved":sha256(observations)==observation_hash,
            "published":result["published"],"llm_called":result["llm_called"],
            "limits":["Tests cached local PDF extraction/resume and config-based lexical proposals, not a fresh Internet download.",
                "Synthetic text is not a state report; title/year/authenticity and scientific extraction were not validated.",
                "No expert judgment, arbitrary-topic model reasoning or fully automatic article creation is demonstrated."]}
        assert all([checkpoint_preserved, answer["notes_preserved"],answer["reviewed_observations_preserved"]])
        return answer


if __name__ == "__main__":
    p=argparse.ArgumentParser(description=__doc__)
    p.add_argument("--output",type=Path)
    args=p.parse_args()
    if args.output and args.output.exists():p.error("Existing report must not be overwritten")
    result=audit()
    if args.output:atomic_json(args.output,result)
    print(json.dumps(result,ensure_ascii=False,indent=2))
