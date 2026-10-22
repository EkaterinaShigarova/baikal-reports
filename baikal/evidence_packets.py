"""Scope-aware packets from reviewed source cards, not heading-derived facts.

Retrieval produces unreviewed leads. Typed cards are separately source-checked;
the module checks provenance/structure, never grants scientific expertise.
"""

import re
import sqlite3
from decimal import Decimal, InvalidOperation
from pathlib import Path
from .core import ROOT, read_json, sha256
from .updates import digest, save_packet
from .encyclopedia import catalog, topics


def number(value):
    try:
        result = Decimal(str(value))
        if not result.is_finite():
            raise ValueError("Non-finite source value")
        return result
    except InvalidOperation as exc:
        raise ValueError("Invalid scalar/range numeric representation") from exc


def descendant(child, parent, tree):
    seen = set()
    while child is not None:
        if child == parent:
            return True
        if child in seen or child not in tree:
            raise ValueError("Unknown/cyclic controlled term")
        seen.add(child)
        child = tree[child].get("parent")
    return False


def retrieve(terms, root=ROOT, limit=12):
    """Use existing FTS read-only. A hit is not a reviewed fact or topic decision."""
    if not terms or not 1 <= limit <= 30:
        raise ValueError("Bounded search terms required")
    path = root / "data/baikal.sqlite"
    if not path.exists():
        return {"status": "index_unavailable", "hits": []}
    words = re.findall(r"[\w]+", " ".join(terms))[:12]
    if not words:
        raise ValueError("Empty search")
    query = " OR ".join('"' + w + '"' for w in words)
    db = sqlite3.connect(f"file:{path}?mode=ro", uri=True)
    try:
        rows = db.execute("SELECT source_id,page,snippet(page_search,2,'[',']',' … ',24) "
                          "FROM page_search WHERE page_search MATCH ? ORDER BY rank LIMIT ?",
                          (query, limit)).fetchall()
    finally:
        db.close()
    return {"status": "unreviewed_search_hits", "query": query,
            "hits": [{"source_id": r[0], "page": r[1], "snippet": r[2]} for r in rows]}


def registry(root=ROOT):
    return read_json(root / "datasets/evidence_packet_registry.json")


def validate(data, root=ROOT, require_local=False):
    sources = {s["id"]: s for s in read_json(root / "config/sources.json")["sources"]}
    refs = {e["id"]: e for e in data["evidence"]}
    if len(refs) != len(data["evidence"]):
        raise ValueError("Duplicate evidence ID")
    local = 0
    for e in refs.values():
        source = sources[e["source_id"]]
        if any(e[k] != source.get(k) for k in ("sha256", "report_year", "publication_year")):
            raise ValueError("Source revision/year role changed")
        if not e["quote"].strip() or e["kind"] != "substantive_excerpt":
            raise ValueError("Heading alone is not evidence")
        p = root / "data/processed" / e["sha256"] / "pages" / f"{e['page']:04d}.json"
        if p.exists():
            text = read_json(p)["text"]
            if (sha256(p) != e["checkpoint_sha256"]
                    or text[e["start"]:e["end"]] != e["quote"]):
                raise ValueError("Exact section-bounded excerpt changed")
            local += 1
        elif require_local:
            raise ValueError("Local checkpoint unavailable")
        target = root / "wiki/sources" / f"{e['source_id']}.md"
        if f'id="page-{e["page"]}"' not in target.read_text():
            raise ValueError("Missing exact physical-page anchor")
    for key in ("objects", "territories"):
        for term in data[key]:
            # Walk to the root even when the requested node equals its ancestor.
            node, seen = term, set()
            while node is not None:
                if node in seen or node not in data[key]:
                    raise ValueError("Broken controlled vocabulary")
                seen.add(node); node = data[key][node].get("parent")
    ids = set()
    for fact in data["facts"]:
        if fact["id"] in ids or fact["status"] != "source_checked_nonexpert":
            raise ValueError("Duplicate/unreviewed fact")
        ids.add(fact["id"])
        if fact["object"] not in data["objects"] or fact["territory"] not in data["territories"]:
            raise ValueError("Unregistered scope")
        support = fact["support"]
        if support["quote"] not in refs[support["evidence_id"]]["quote"]:
            raise ValueError("Own fact support absent")
        for anchor in fact["anchors"]:
            if anchor["quote"] not in refs[anchor["evidence_id"]]["quote"]:
                raise ValueError("Year/territory/indicator anchor absent")
        value = fact["value"]
        if value is not None:
            lexeme = fact["value_lexeme"]
            if not lexeme or lexeme not in support["quote"]:
                raise ValueError("Value borrowed from another fragment")
            compact = re.sub(r"\s+", "", lexeme).replace(",", ".")
            if isinstance(value, list):
                parts = compact.replace("–", "|").split("|")
                if len(parts) != 2 or [number(x) for x in parts] != [number(x) for x in value]:
                    raise ValueError("Unsupported numeric range")
            elif number(compact) != number(value):
                raise ValueError("Value/lexeme mismatch")
            if fact["unit_lexeme"] not in support["quote"] or not fact["unit"]:
                raise ValueError("Own unit support absent")
            if fact["unit_lexeme"] not in data["units"].get(fact["unit"], {}).get("lexemes", []):
                raise ValueError("Unregistered unit interpretation")
        period = fact["observation_period"]
        if period:
            year_quotes = " ".join(a["quote"] for a in fact["anchors"] if a["role"] == "period")
            # Spaced OCR year may be normalized, never borrowed from report metadata.
            compact = re.sub(r"\s", "", year_quotes)
            for year in period["years"]:
                if str(year) not in compact:
                    raise ValueError("Observation year lacks its own source anchor")
            if period.get("month") == 1 and not re.search(r"январ", year_quotes, re.I):
                raise ValueError("Month lacks source anchor")
        if fact["expert_review"] or fact["comparability_approved"]:
            raise ValueError("Automatic source cards cannot grant expert/trend approval")
        for bound in fact.get("article_support", []):
            path = root / "wiki" / bound["path"]
            if sha256(path) != bound["sha256"] or bound["quote"] not in path.read_text():
                raise ValueError("Version-bound existing article support changed")
    published = {t["id"]: t for _, _, t in topics(catalog(root)) if t.get("article")}
    if {a["id"] for a in data["articles"]} != set(published):
        raise ValueError("Scope inventory must retain all published articles")
    for a in data["articles"]:
        p = root / "wiki" / published[a["id"]]["article"]
        if sha256(p) != a["sha256"] or a["scope_quote"] not in p.read_text():
            raise ValueError("Article scope version changed")
    return {"facts": len(ids), "excerpts": len(refs), "local_excerpts": local,
            "article_scope_inventory": len(published), "expert_review": False}


def make_packet(request, data, root=ROOT):
    """Structural matching uses object hierarchy + indicator + territory, not title."""
    if request["object"] not in data["objects"] or request["territory"] not in data["territories"]:
        raise ValueError("Unknown requested scope")
    byid = {f["id"]: f for f in data["facts"]}
    chosen = [byid[i] for i in request["fact_ids"]]
    eligible, excluded = [], []
    for f in chosen:
        reasons = []
        if not descendant(f["object"], request["object"], data["objects"]):
            reasons.append("different_object")
        if not descendant(f["territory"], request["territory"], data["territories"]):
            reasons.append("different_territorial_scope")
        if request["indicators"] and f["indicator"] not in request["indicators"]:
            reasons.append("different_indicator")
        for key in ("statistic", "unit", "message_kind", "observation_period"):
            if request.get(key) is not None and f[key] != request[key]:
                reasons.append("different_or_unknown_" + key)
        (excluded if reasons else eligible).append({"fact_id": f["id"], "reasons": reasons})
    facts = [byid[x["fact_id"]] for x in eligible]
    eids = {f["support"]["evidence_id"] for f in chosen}
    eids.update(a["evidence_id"] for f in chosen for a in f["anchors"])
    eids.update(eid for issue in request.get("contradictions", []) for eid in issue["evidence_ids"])
    evidence = [e for e in data["evidence"] if e["id"] in eids]
    if {e["id"] for e in evidence} != eids:
        raise ValueError("Unbound contradiction/context evidence")
    refs = {e["id"]: e for e in evidence}
    documents = {refs[f["support"]["evidence_id"]]["sha256"] for f in facts}
    related = []
    for a in data["articles"]:
        relation = "unmapped"
        if request["object"] in a["objects"]:
            relation = "same_subject"
        elif any(descendant(request["object"], o, data["objects"]) for o in a["objects"]):
            relation = "subtopic_of_article"
        elif any(descendant(o, request["object"], data["objects"]) for o in a["objects"]):
            relation = "article_is_narrower"
        elif request["object"] in a.get("related_objects", []):
            relation = "related_distinct_subject"
        if relation == "unmapped":
            continue
        overlaps = [f["id"] for f in facts if any(b["path"] == a["path"] for b in f.get("article_support", []))]
        related.append({**a, "scope_relation": relation,
                        "fact_overlap": overlaps, "unmatched_facts_are_not_proven_new": True})
    order = {"same_subject": 0, "subtopic_of_article": 1, "article_is_narrower": 2, "related_distinct_subject": 3}
    related.sort(key=lambda a: (order[a["scope_relation"]], a["id"]))
    same = [a for a in related if a["scope_relation"] == "same_subject"]
    parent = [a for a in related if a["scope_relation"] == "subtopic_of_article"]
    enough = len(documents) >= request.get("min_documents", 2) and len(facts) >= request.get("min_facts", 2)
    if not facts:
        action, target = "insufficient", None
    elif same:
        action, target = "extend_existing", same[0]["id"]
    elif parent:
        action, target = "subtopic", parent[0]["id"]
    elif enough:
        action, target = "new_article_candidate", None
    else:
        action, target = "insufficient", None
    return {"id": request["id"], "title": request["title"], "scope": request,
            "facts": facts, "excluded": excluded, "evidence": evidence, "related_articles": related,
            "limitations": request["limitations"], "contradictions": request.get("contradictions", []),
            "readiness": {"eligible_facts": len(facts), "distinct_source_documents": len(documents),
                          "enough_for_scoped_overview": enough, "trend_approved": False,
                          "missing_evidence": request["missing_evidence"]},
            "structural_decision": {"action": action, "target_id": target,
                                    "fact_novelty": "not_established", "whole_article_absence": "not_established"},
            "published": False, "expert_review": False, "editorial_decision": None}


def retain_packet(packet, root=ROOT):
    return save_packet(root, "evidence_packets", packet)


def compact_context(packet):
    """No expected label or structural answer; small relevant facts + article scope."""
    used = {f["support"]["evidence_id"] for f in packet["facts"]}
    # Outside-scope facts remain identified as context, never converted to target facts.
    return {"topic_id": packet["id"], "title": packet["title"],
            "requested_object": packet["scope"]["object"], "requested_territory": packet["scope"]["territory"],
            "facts": [{**{k: f[k] for k in ("id", "statement", "object", "indicator", "value", "unit",
                                        "statistic", "territory", "observation_period", "message_kind", "support")},
                       "period_anchors": [a for a in f["anchors"] if a["role"] == "period"
                                          and a["quote"] not in f["support"]["quote"]]}
                      for f in packet["facts"]],
            "excluded_fact_reasons": packet["excluded"],
            "sources": [{k: e[k] for k in ("id", "source_id", "sha256", "page", "report_year", "publication_year")}
                        for e in packet["evidence"] if e["id"] in used],
            "articles": [{k: a[k] for k in ("id", "title", "objects", "scope_quote", "fact_overlap")}
                         for a in packet["related_articles"][:2]],
            "limitations": packet["limitations"], "missing_evidence": packet["readiness"]["missing_evidence"],
            "unresolved_issues": packet["contradictions"],
            "issue_excerpts": [{"id": e["id"], "quote": e["quote"]} for e in packet["evidence"]
                               if any(e["id"] in i["evidence_ids"] for i in packet["contradictions"])],
            "no_global_absence_proof": True}
