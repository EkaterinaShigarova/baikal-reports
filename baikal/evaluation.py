"""Prepare two real-evidence cases. Preparation is not model inference."""

import copy
import os
import shutil
from pathlib import Path
from .core import ROOT, read_json
from .updates import propose, load_proposal, save_packet
from .assessment import context, PROMPT


def resources():
    mem = {
        line.split(":")[0]: int(line.split()[1]) * 1024
        for line in Path("/proc/meminfo").read_text().splitlines()
        if line.startswith(("MemTotal:", "MemAvailable:"))
    }
    limit_path = Path("/sys/fs/cgroup/memory.max")
    limit = limit_path.read_text().strip() if limit_path.exists() else "max"
    available = mem["MemAvailable"]
    if limit != "max" and Path("/sys/fs/cgroup/memory.current").exists():
        available = min(
            available,
            max(0, int(limit) - int(Path("/sys/fs/cgroup/memory.current").read_text())),
        )
    return {
        "memory_limit_bytes": min(mem["MemTotal"], int(limit))
        if limit != "max"
        else mem["MemTotal"],
        "memory_available_bytes": available,
        "disk_free_bytes": shutil.disk_usage(ROOT).free,
        "cpu_affinity_count": len(os.sched_getaffinity(0)),
        "cpu_quota": Path("/sys/fs/cgroup/cpu.max").read_text().strip()
        if Path("/sys/fs/cgroup/cpu.max").exists()
        else None,
    }


def curate_v2(topic, evidence):
    """Reviewed case boundaries only; no inferred column reconstruction or OCR."""
    if topic == "baikal-sturgeon":
        ref = next(e for e in evidence if e["report_year"] == 2016)
        text = ref["quote"]
        begin = text.index("Для получения икры")
        split = text.index("В результате подращивания")
        end = text.index("государственного задания (рис. 1.4.6.3).") + len(
            "государственного задания (рис. 1.4.6.3)."
        )
        quotes = [text[begin:split].strip(), text[split:end].strip()]
        excluded = [
            {
                "source_id": e["source_id"],
                "page": e["page"],
                "reason": "Mixed sturgeon/grayling columns; short window lacks complete sturgeon claims",
            }
            for e in evidence
            if e is not ref
        ]
    else:
        ref = evidence[0]
        text = ref["quote"]
        begin = text.index("Максимальное содержание")
        end = text.index("в 2,7 раза.") + len("в 2,7 раза.")
        table_start = text.index("Содержание полициклических")
        table_end = text.index("Примечания.")
        quotes = [text[begin:end].strip(), text[table_start:table_end].strip()]
        excluded = []
    return [{**ref, "quote": q} for q in quotes], excluded


def prepare_cases(root=ROOT, assessment_protocol="v1"):
    protocol = read_json(root / "datasets/update_workflow_test.json")
    if not protocol:
        raise ValueError("Existing update workflow protocol required")
    for ref in protocol["source_pages"]:
        propose(ref["source_id"], pages=[ref["physical_page"]], root=root)
    state = read_json(root / "data/wiki_updates/state.json")
    cases = []
    for topic in ["baikal-sturgeon", "selenga-sediments"]:
        ids = [
            pid
            for pid in state["proposals"]
            if read_json(root / "data/wiki_updates/proposals" / f"{pid}.json").get(
                "topic"
            )
            == topic
        ]
        if not ids:
            raise ValueError(f"Missing real evidence proposal: {topic}")
        base_id = ids[-1]
        lexical = load_proposal(root, base_id, topic)
        selected = copy.deepcopy(lexical)
        diagnostics = {}
        if topic == "selenga-sediments":
            ref = selected["evidence"][0]
            page = read_json(
                root
                / "data/processed"
                / ref["sha256"]
                / "pages"
                / f"{ref['page']:04d}.json"
            )
            text = page["text"]
            stop = text.find("Состояние донных отложений на севере")
            if stop < 0 or stop > 6000:
                raise ValueError("Expected bounded table section unavailable")
            diagnostics = {
                "lexical_window_contains_2015_carcinogen_row": "49,2" in ref["quote"],
                "curated_window_contains_2015_carcinogen_row": "49,2" in text[:stop],
            }
            ref["quote"] = text[:stop]
            selected["preparation"] = (
                "Bounded source selection includes the whole cited table; original lexical proposal retained."
            )
        else:
            diagnostics = {
                "lexical_window_contains_other_fish": any(
                    "хариус" in e["quote"].lower() for e in selected["evidence"]
                ),
                "reference_new_detail": "1029 тыс. шт. со средней навеской 3,0 г: проверить как деталь выпуска, не выживаемость",
            }
        selected["reference_lexical_proposal_id"] = base_id
        if assessment_protocol == "v2":
            selected["evidence"], excluded = curate_v2(topic, selected["evidence"])
            selected["excluded_windows"] = excluded
            selected["preparation"] = (
                "v2 exact bounded fragments; original pages and proposals retained"
            )
        pid, _ = save_packet(root, "proposals", selected)
        supplied = context(topic, selected["evidence"], root)
        prompt, model_input = PROMPT, supplied
        if assessment_protocol == "v2":
            from .assessment_v2 import PROMPT as V2_PROMPT, wire_context

            prompt, model_input = V2_PROMPT, wire_context(supplied)
        prepared = {
            "version": 1,
            "topic": topic,
            "proposal_id": pid,
            "lexical_proposal_id": base_id,
            "system_prompt": prompt,
            "input": model_input,
            "diagnostics": diagnostics,
            "model_inference_executed": False,
            "reference_scope": "Evaluation expectations only, not new reviewed observations or specialist validation.",
        }
        eid, path = save_packet(root, "evaluation_inputs", prepared)
        cases.append(
            {
                "id": eid,
                "topic": topic,
                "proposal_id": pid,
                "path": path,
                "lexical_proposal_id": base_id,
                "diagnostics": diagnostics,
                "article_sha256": supplied["existing_article"]["sha256"],
                "source_pages": [
                    {k: e[k] for k in ["source_id", "sha256", "page", "report_year"]}
                    for e in selected["evidence"]
                ],
                "article_context_truncated": supplied["existing_article"]["truncated"],
            }
        )
    return {
        "status": "prepared_only",
        "model_inference_executed": False,
        "model_generated_updates": 0,
        "cases": cases,
        "lexical_baseline": {
            "proposals": protocol["proposal_count"],
            "topics": len({p["topic"] for p in protocol["proposals"]}),
        },
        "published_articles_changed": 0,
        "reviewed_observations_changed": 0,
        "assessment_protocol": assessment_protocol,
    }
