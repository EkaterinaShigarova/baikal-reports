"""Explicit, snapshot-bound editorial additions; never grants expert status."""

import datetime
import hashlib
import json
import re
from pathlib import Path
from .core import ROOT, read_json, atomic_text
from .updates import digest, save_packet, archive_article, load_proposal
from .encyclopedia import catalog, topics
from .scientific_review import START, END, HUMAN


def draft_packet(root, pid):
    if not re.fullmatch(r"[0-9a-f]{64}", pid):
        raise ValueError("Invalid draft ID")
    d = read_json(root / "data/wiki_updates/llm_drafts" / f"{pid}.json", {})
    if (
        digest(d) != pid
        or d.get("status") != "draft"
        or d.get("published") is not False
        or not d.get("assessment")
    ):
        raise ValueError("An immutable update assessment draft is required")
    if Path(root).resolve().is_relative_to(ROOT.resolve()) and not d.get("run_id"):
        raise ValueError("Production draft requires a retained generation run")
    load_proposal(root, d["proposal_id"], d["topic"])
    from .assessment import validate

    if validate(d["result"], d["context"]):
        raise ValueError("Assessment validation failed")
    if d["result"]["selected_topic"] != d["topic"]:
        raise ValueError("Alternate target requires a separately reviewed context")
    return d


def prepare(draft_id, claim_indices, root=ROOT):
    root = Path(root)
    d = draft_packet(root, draft_id)
    target = next(
        t.get("article") for _, _, t in topics(catalog(root)) if t["id"] == d["topic"]
    )
    if not target:
        raise ValueError("New articles require a separate editorial workflow")
    old = d["context"]["existing_article"]
    if not old or old["path"] != target:
        raise ValueError("Missing preceding article snapshot")
    path = root / "wiki" / target
    if hashlib.sha256(path.read_bytes()).hexdigest() != old["sha256"]:
        raise ValueError("Article changed since generation")
    indices = sorted(set(claim_indices))
    claims = d["result"]["claims"]
    if not indices or any(
        type(i) is not int or not 0 <= i < len(claims) for i in indices
    ):
        raise ValueError("Select valid claim indices")
    selected = [claims[i] for i in indices]
    if any(c["novelty"] != "new_detail" for c in selected):
        raise ValueError(
            "Only explicitly reviewed new-detail candidates may be prepared"
        )
    from .llm import validate_claims

    if validate_claims({"claims": selected}, d["evidence"]):
        raise ValueError("Invalid change citations")
    for c in selected:
        if any(
            marker in value
            for value in [c["text"], *c["uncertainties"]]
            for marker in [HUMAN, START, END, "\x00"]
        ):
            raise ValueError("Reserved marker in model text")
        for e in c["evidence"]:
            source_page = root / "wiki/sources" / f"{e['source_id']}.md"
            if (
                not source_page.is_file()
                or f'id="page-{e["page"]}"' not in source_page.read_text()
            ):
                raise ValueError(
                    "Compile the cited source Wiki page before preparing an addition"
                )
    original = path.read_text()
    body, sep, notes = original.partition(HUMAN)
    body = re.sub(
        re.escape(START) + r".*?" + re.escape(END),
        START
        + "\nСтатус: **Требуется научная рецензия**. Текст дополнен; прежний объём сверки сохранён в реестре.\n"
        + END,
        body,
        count=1,
        flags=re.S,
    )
    addition = [
        "## Редакционное дополнение",
        "",
        "Редакционное принятие не является научной экспертизой специалистом.",
        "",
    ]
    for c in selected:
        addition += [c["text"], ""]
        for e in c["evidence"]:
            sid, page = e["source_id"], e["page"]
            addition.append(f"[{sid}:p{page}](../sources/{sid}.md#page-{page})")
        if c["uncertainties"]:
            addition += ["Ограничения: " + "; ".join(c["uncertainties"])]
        addition += [""]
    new = body.rstrip() + "\n\n" + "\n".join(addition).rstrip() + "\n\n"
    if sep:
        new += HUMAN + notes
    packet = {
        "version": 1,
        "draft_id": draft_id,
        "topic": d["topic"],
        "article": target,
        "base_sha256": old["sha256"],
        "new_sha256": hashlib.sha256(new.encode()).hexdigest(),
        "new_text": new,
        "selected_claim_indices": indices,
        "claims": selected,
        "status": "awaiting_editorial_approval",
        "expert_review": False,
    }
    pid, dest = save_packet(root, "changes", packet)
    return {"id": pid, "path": dest, "status": packet["status"], "published": False}


def apply(change_id, approval_path, root=ROOT):
    root = Path(root).resolve()
    if not re.fullmatch(r"[0-9a-f]{64}", change_id):
        raise ValueError("Invalid change ID")
    change = read_json(root / "data/wiki_updates/changes" / f"{change_id}.json", {})
    approval = read_json(approval_path, {})
    if (
        digest(change) != change_id
        or change.get("status") != "awaiting_editorial_approval"
    ):
        raise ValueError("Change integrity mismatch")
    if (
        approval.get("change_id") != change_id
        or approval.get("decision") != "approve"
        or approval.get("review_kind") != "editorial"
        or not approval.get("reviewed_by")
    ):
        raise ValueError("Explicit snapshot-bound editorial approval is required")
    if (
        approval.get("source_semantics_checked") is not True
        or approval.get("uncertainties_preserved") is not True
    ):
        raise ValueError("Editorial source/uncertainty checks are required")
    try:
        datetime.date.fromisoformat(approval["reviewed_at"])
    except (KeyError, TypeError, ValueError) as exc:
        raise ValueError("Approval date required") from exc
    if approval.get("test_only") and root.is_relative_to(ROOT.resolve()):
        raise ValueError("Test approval cannot be used in the actual repository")
    target = (root / "wiki" / change["article"]).resolve()
    if not target.is_relative_to((root / "wiki").resolve()):
        raise ValueError("Invalid target")
    ledger_path = root / "datasets/editorial_changes.json"
    ledger = read_json(ledger_path, [])
    current = hashlib.sha256(target.read_bytes()).hexdigest()
    if any(e["change_id"] == change_id for e in ledger):
        if current != change["new_sha256"]:
            raise ValueError("Previously applied article subsequently changed")
        return {"change_id": change_id, "status": "already_applied"}
    if current != change["base_sha256"]:
        raise ValueError("Article changed since editorial preparation")
    draft = draft_packet(root, change["draft_id"])
    # Recompute the entire prepared change: a forged text cannot reuse a valid draft.
    recomputed = prepare(change["draft_id"], change["selected_claim_indices"], root)
    if recomputed["id"] != change_id:
        raise ValueError("Change differs from validated draft")
    archived = archive_article(root, change["article"])
    provenance_path = root / "datasets/article_provenance.json"
    provenance = read_json(provenance_path, {})
    refs = provenance.setdefault(change["article"], [])
    for c in change["claims"]:
        for ref in c["evidence"]:
            sha = next(
                e["sha256"]
                for e in draft["evidence"]
                if e["source_id"] == ref["source_id"] and e["page"] == ref["page"]
            )
            entry = {"source_id": ref["source_id"], "sha256": sha, "page": ref["page"]}
            if entry not in refs:
                refs.append(entry)
    ledger.append(
        {
            "change_id": change_id,
            "draft_id": change["draft_id"],
            "article": change["article"],
            "before": archived,
            "after_sha256": change["new_sha256"],
            "approval": approval,
            "approval_sha256": hashlib.sha256(
                Path(approval_path).read_bytes()
            ).hexdigest(),
            "claims": change["claims"],
            "expert_review": False,
        }
    )
    log = root / "wiki/LOG.md"
    stamp = datetime.datetime.now(datetime.timezone.utc).isoformat()
    writes = {
        target: change["new_text"],
        provenance_path: json.dumps(provenance, ensure_ascii=False, indent=2) + "\n",
        ledger_path: json.dumps(ledger, ensure_ascii=False, indent=2) + "\n",
        log: (log.read_text() if log.exists() else "# Журнал Wiki\n")
        + f"\n## [{stamp}] editorial | {change['article']} / {change_id}; редакционное принятие, не экспертная рецензия.\n",
    }
    previous = {p: p.read_text() if p.exists() else None for p in writes}
    try:
        for p, content in writes.items():
            atomic_text(p, content)
    except Exception:
        for p, text in previous.items():
            if text is None:
                if p.exists():
                    p.unlink()
            else:
                atomic_text(p, text)
        raise
    return {
        "change_id": change_id,
        "status": "applied",
        "expert_review": False,
        "review_status": "needs_scientific_review",
        "article": change["article"],
    }
