"""Bounded review proposals from cached pages. Never publishes or approves data."""

import datetime
import hashlib
import json
import re
from pathlib import Path
from .core import ROOT, read_json, atomic_json
from .encyclopedia import catalog, topics
from .wiki import scoped_text


def digest(value):
    return hashlib.sha256(
        json.dumps(value, ensure_ascii=False, sort_keys=True).encode()
    ).hexdigest()


def archive_article(root, name):
    """Retain the entire preceding version, including human notes, by its hash."""
    path = (root / "wiki" / name).resolve()
    if not path.is_relative_to((root / "wiki").resolve()) or not path.is_file():
        raise ValueError("Invalid article to archive")
    content = path.read_bytes()
    sha = hashlib.sha256(content).hexdigest()
    dest = root / "data/wiki_updates/versions" / f"{sha}.md"
    dest.parent.mkdir(parents=True, exist_ok=True)
    if dest.exists():
        if dest.read_bytes() != content:
            raise ValueError("Archived article integrity mismatch")
    else:
        dest.write_bytes(content)
    return {"article": name, "sha256": sha, "path": str(dest.relative_to(root))}


def save_packet(root, folder, packet):
    """Content-addressed, append-only packets; identical runs reuse a packet."""
    pid = digest(packet)
    dest = root / "data/wiki_updates" / folder / f"{pid}.json"
    if dest.exists():
        if read_json(dest) != packet:
            raise ValueError("Immutable update packet mismatch")
    else:
        atomic_json(dest, packet)
        log = root / "data/wiki_updates/events.jsonl"
        with log.open("a") as f:
            f.write(
                json.dumps(
                    {
                        "at": datetime.datetime.now(datetime.timezone.utc).isoformat(),
                        "operation": folder,
                        "id": pid,
                    }
                )
                + "\n"
            )
    return pid, str(dest.relative_to(root))


def substantive(text):
    return len(text.strip()) >= 250 and not re.search(
        r"^\s*(?:содержание|оглавление|список\s+сокращений|условные\s+обозначения)\s*$",
        text[:1400],
        re.I | re.M,
    )


def propose(source_id, pages=None, limit=20, root=ROOT):
    """Inspect at most limit changed checkpoints for one source; no PDF/LLM I/O."""
    root = Path(root)
    if not 1 <= limit <= 100:
        raise ValueError("Proposal limit must be between 1 and 100 changed pages")
    sources = read_json(root / "config/sources.json", {"sources": []})["sources"]
    current_hashes = {s["id"]: s.get("sha256") for s in sources}
    source = next((s for s in sources if s["id"] == source_id), None)
    if not source or not re.fullmatch(r"[0-9a-f]{64}", source.get("sha256", "")):
        raise ValueError("A processed source with SHA256 is required")
    rules = read_json(root / "config/update_rules.json", {"patterns": {}})
    if not rules["patterns"]:
        raise ValueError("No update routing rules configured")
    cfg = {t["id"]: t for _, _, t in topics(catalog(root))}
    compiled = {k: re.compile(v, re.I | re.S) for k, v in rules["patterns"].items()}
    state_path = root / "data/wiki_updates/state.json"
    state = read_json(state_path, {"processed_pages": {}, "proposals": []})
    review = read_json(
        root / "datasets/scientific_review.json", {"articles": {}, "issues": []}
    )
    claims = read_json(root / "datasets/scientific_review_claims.json", {}).get(
        "claims", []
    )
    known = {}
    for c in claims:
        for r in c["evidence"]:
            known.setdefault((r["source_id"], r["sha256"], r["page"]), []).append(
                r["quote"]
            )
    selected = set(pages) if pages is not None else None
    if selected is not None and any(
        not isinstance(n, int) or not 1 <= n <= source["pages"] for n in selected
    ):
        raise ValueError("Invalid physical page")
    directory = root / "data/processed" / source["sha256"] / "pages"
    files = (
        sorted(directory.glob("*.json"))
        if selected is None
        else [directory / f"{n:04d}.json" for n in sorted(selected)]
    )
    if not files:
        raise ValueError("No local page checkpoints")
    grouped = {}
    inspected = skipped = unchanged = 0
    failures = []
    for path in files:
        n = int(path.stem)
        if not path.is_file():
            failures.append(f"page {n}: checkpoint unavailable")
            continue
        key = f"{source_id}:{source['sha256']}:{n}"
        scope = {
            k: source.get(k)
            for k in [
                "report_year",
                "authenticity",
                "wiki_page_ranges",
                "wiki_stop_before",
            ]
        }
        stamp = digest([rules, scope, path.stat().st_mtime_ns, path.stat().st_size])
        if state["processed_pages"].get(key) == stamp:
            unchanged += 1
            continue
        if inspected >= limit:
            break
        inspected += 1
        page = read_json(path)
        if page.get("status") != "ok" or page.get("page") != n:
            failures.append(f"page {n}: failed or mismatched checkpoint")
            continue
        text = scoped_text(source, page)
        if not substantive(text):
            skipped += 1
        else:
            matched = False
            for topic, pattern in compiled.items():
                if topic not in cfg:
                    continue
                m = pattern.search(text)
                if not m:
                    continue
                matched = True
                quote = text[max(0, m.start() - 350) : m.end() + 1000]
                ref = {
                    "source_id": source_id,
                    "sha256": source["sha256"],
                    "page": n,
                    "report_year": source.get("report_year"),
                    "observation_period": None,
                    "quote": quote,
                    "quote_basis": "exact_cached_page_text",
                    "source_authenticity": source.get("authenticity", "unverified"),
                    "novelty": "already_quoted_fragment"
                    if any(
                        quote in q
                        for q in known.get((source_id, source["sha256"], n), [])
                    )
                    else "previously_unindexed_fragment",
                }
                grouped.setdefault(topic, []).append(ref)
            if not matched:
                failures.append(
                    f"page {n}: no lexical route; manual topic discovery needed"
                )
        state["processed_pages"][key] = stamp
    result = []
    for topic, evidence in grouped.items():
        target = cfg[topic].get("article")
        old = []
        for pid in state["proposals"]:
            previous = read_json(
                root / "data/wiki_updates/proposals" / f"{pid}.json", {}
            )
            if previous.get("topic") == topic:
                old.extend(previous["evidence"])
        unique = {}
        for ref in old + evidence:
            if current_hashes.get(ref["source_id"]) == ref["sha256"]:
                unique[digest(ref)] = ref
        refs = list(unique.values())[-12:]
        issue_ids = []
        article_claims = set(review["articles"].get(target, {}).get("claim_ids", []))
        for issue in review.get("issues", []):
            if article_claims.intersection(issue["claim_ids"]):
                issue_ids.append(issue["id"])
        revisions = review["articles"].get(target, {}).get("source_revisions", {})
        packet = {
            "version": 1,
            "topic": topic,
            "title": cfg[topic]["title"],
            "kind": "article_update" if target else "new_article_candidate",
            "status": "pending_semantic_review",
            "published": False,
            "affected_article": target,
            "evidence": refs,
            "preceding_versions": [archive_article(root, target)] if target else [],
            "known_issue_ids": issue_ids,
            "source_revision_changed": bool(
                source_id in revisions and revisions[source_id] != source["sha256"]
            ),
            "distinct_documents": len({r["sha256"] for r in refs}),
            "new_article_minimum_packet_met": not target
            and len({r["sha256"] for r in refs}) >= 2,
            "limitations": [
                "Lexical routing and fragment novelty are not semantic discovery.",
                "Known issues/source revisions are review signals, not detected scientific contradictions.",
                "Observation period, methodological comparability and semantic support require review.",
                "Two documents do not establish independent evidence or publication readiness.",
            ],
        }
        pid, path = save_packet(root, "proposals", packet)
        if pid not in state["proposals"]:
            state["proposals"].append(pid)
        result.append({"id": pid, "topic": topic, "path": path, "kind": packet["kind"]})
    atomic_json(state_path, state)
    return {
        "source_id": source_id,
        "inspected_changed_pages": inspected,
        "unchanged_pages": unchanged,
        "skipped_noncontent_pages": skipped,
        "notices": failures,
        "proposals": result,
        "published": False,
        "llm_called": False,
    }


def load_proposal(root, pid, topic):
    if not re.fullmatch(r"[0-9a-f]{64}", pid):
        raise ValueError("Invalid proposal ID")
    packet = read_json(root / "data/wiki_updates/proposals" / f"{pid}.json", {})
    if not packet or packet.get("topic") != topic or digest(packet) != pid:
        raise ValueError("Proposal topic or integrity mismatch")
    sources = {
        s["id"]: s
        for s in read_json(root / "config/sources.json", {"sources": []})["sources"]
    }
    for ref in packet["evidence"]:
        if sources.get(ref["source_id"], {}).get("sha256") != ref["sha256"]:
            raise ValueError("Stale proposal source revision; propose again")
        if (
            "report_year" in ref
            and sources[ref["source_id"]].get("report_year") != ref["report_year"]
        ):
            raise ValueError("Stale proposal reporting year; propose again")
        page = read_json(
            root
            / "data/processed"
            / ref["sha256"]
            / "pages"
            / f"{ref['page']:04d}.json",
            {},
        )
        if page.get("status") != "ok" or ref["quote"] not in page.get("text", ""):
            raise ValueError("Proposal quote no longer matches checkpoint")
    return packet
