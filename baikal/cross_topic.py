"""Version-bound partial registries; comparison never proves global novelty."""

import re
from decimal import Decimal
from .core import read_json, sha256
from .updates import digest
from .claim_coverage import compare_pair

NUMBER_WORDS = {"одна": "1", "три": "3", "пять": "5", "семь": "7"}


def numeric_support(value, quote, word=None):
    numbers = {Decimal(n.replace(',', '.')) for n in
               re.findall(r"(?<![\w])\d+(?:[.,]\d+)?", quote)}
    number = Decimal(str(value).replace(',', '.'))
    return number in numbers or (
        word in NUMBER_WORDS and Decimal(NUMBER_WORDS[word]) == number
        and re.search(r"\b" + re.escape(word) + r"\b", quote) is not None
    )


def content_blocks(text):
    """Paragraph/table blocks, explicitly not atomic claims or sentence counts."""
    end = text.index("## Связанные статьи")
    body = text[:end]
    result = []
    for match in re.finditer(r"\S[^\n]*(?:\n(?!\n)[^\n]*)*", body):
        quote = match.group()
        if quote.startswith(("#", "<!-- REVIEW")):
            continue
        result.append({"id": f"B{len(result)+1:02d}", "start": match.start(),
                       "end": match.end(), "quote": quote,
                       "kind": "table" if quote.startswith("|") else "paragraph"})
    return result


def compare(fact, target, vocabulary=None):
    """Do not mistake changed/unknown survey methods for a measured conflict."""
    f, t = fact["features"], target["features"]
    for record in [fact, target]:
        period = record["features"].get("period")
        if not period or not any(isinstance(v, (str, int)) and v != "" for v in period.values()):
            return {"relation": "insufficient", "reason": "unknown_period"}
    # Preserve obvious dimensional differences even when a method is unknown.
    base = compare_pair(fact, target, vocabulary or {"aliases": {}})
    if base["relation"] == "incomparable":
        if fact.get("value_qualifier") != target.get("value_qualifier"):
            base["differences"].append("value_qualifier")
        if fact.get("method") != target.get("method"):
            base["differences"].append("method_description")
        return base
    if fact.get("value_qualifier") != target.get("value_qualifier"):
        return {"relation": "incomparable", "differences": ["value_qualifier"]}
    if fact.get("method") is None or target.get("method") is None:
        return {"relation": "insufficient", "reason": "method_not_documented"}
    if fact["method"] != target["method"]:
        return {"relation": "comparability_unconfirmed", "reason": "different_method_descriptions_not_proven_incompatibility"}
    return base


def presence(fact, registry, matches=None, absence_review=None):
    """An empty partial registry scan is not a whole-article absence certificate."""
    if matches:
        byid = {c["id"]: c for c in registry["claims"]}
        for cid in matches:
            if cid not in byid or compare(fact, byid[cid])["relation"] != "same_fact":
                raise ValueError("Article presence lacks an exact supported match")
        return "present_in_registered_passage"
    if absence_review is not None:
        if (absence_review.get("fact_sha256") != digest(fact)
            or absence_review.get("article_sha256") != registry["article"]["sha256"]
            or absence_review.get("reviewed_block_ids") != [b["id"] for b in registry["blocks"]]
            or absence_review.get("expert_review") is not False
            or not absence_review.get("reason")):
            raise ValueError("Absence needs a specific fact and the whole article version")
        return "reviewed_new_detail_candidate_only"
    return "unknown_requires_full_article_review"


def validate(bundle, root, require_local=False):
    cfg = {s["id"]: s for s in read_json(root / "config/sources.json")["sources"]}
    sources = {s["id"]: s for s in bundle["sources"]}
    if len(sources) != len(bundle["sources"]) or bundle["expert_review"] is not False:
        raise ValueError("Duplicate evidence or fabricated expert status")
    checked = 0
    for source in sources.values():
        original = cfg[source["source_id"]]
        if any(source[k] != original.get(k) for k in ["sha256", "report_year", "publication_year"]):
            raise ValueError("Source revision/dates changed")
        path = root / "data/processed" / source["sha256"] / f"pages/{source['page']:04d}.json"
        if require_local or path.exists():
            if not path.exists():
                raise FileNotFoundError(path)
            page = read_json(path)
            if source["quote"] not in page["text"]:
                raise ValueError("Exact cited excerpt missing")
            checked += 1
    claims = 0
    metrics = []
    for registry in bundle["articles"]:
        path = root / registry["article"]["path"]
        text = path.read_text()
        if sha256(path) != registry["article"]["sha256"] or content_blocks(text) != registry["blocks"]:
            raise ValueError("Article content or block inventory changed")
        if registry["semantic_completeness_established"] is not False:
            raise ValueError("Partial registry cannot claim semantic completeness")
        ids = set()
        for c in registry["claims"]:
            span = c["article_evidence"]
            if (c["id"] in ids or c["sha256"] != digest({k:v for k,v in c.items() if k!='sha256'})
                or text[span["start"]:span["end"]] != span["quote"]
                or not c["evidence_ids"] or not set(c["evidence_ids"]) <= sources.keys()
                or c["status"] != "source_statement_checked_non_expert"
                or c["expert_review"] is not False):
                raise ValueError("Unbound or unreviewed statement")
            if not any(b["start"] <= span["start"] < span["end"] <= b["end"] for b in registry["blocks"]):
                raise ValueError("Claim not in substantive article block")
            features = c["features"]
            support = c["source_support"]
            if support["evidence_id"] not in c["evidence_ids"] or support["quote"] not in sources[support["evidence_id"]]["quote"]:
                raise ValueError("Atomic support not contained in its own exact source")
            primary = sources[support["evidence_id"]]
            if (c["report_year"] != primary["report_year"]
                or c["publication_year"] != primary["publication_year"]
                or c["observation_year"] != features["period"].get("year")):
                raise ValueError("Year roles changed or were conflated")
            if features["value_kind"] == "numeric":
                if not numeric_support(features["value"], span["quote"], c.get("article_number_word")) or not numeric_support(features["value"], support["quote"], c.get("source_number_word")):
                    raise ValueError("Numeric value absent from article or source")
            ids.add(c["id"])
        for context in registry["nonregistered_context"]:
            if (text[context["start"]:context["end"]] != context["quote"]
                or context["matchable"] is not False or context["expert_review"] is not False):
                raise ValueError("Unverified article context became a matching authority")
        represented = {b["id"] for b in registry["blocks"] if any(
            b["start"] <= c["article_evidence"]["start"] < c["article_evidence"]["end"] <= b["end"]
            for c in registry["claims"])}
        uncovered = {b["id"] for b in registry["blocks"]} - represented
        if {b["block_id"] for b in registry["unrepresented"]} != uncovered:
            raise ValueError("Unreported article blocks")
        metrics.append({"article": registry["article"]["path"], "claims": len(ids),
                        "represented_blocks": len(represented), "blocks": len(registry["blocks"]),
                        "semantic_completeness_established": False})
        claims += len(ids)
    return {"claims": claims, "articles": metrics, "exact_local_excerpts_checked": checked,
            "expert_review": False}


def validate_examples(benchmark, bundle, registry_file_sha256):
    if benchmark["registry_file_sha256"] != registry_file_sha256 or benchmark["max_model_requests"] != 1:
        raise ValueError("Benchmark registry revision or request limit changed")
    sources = {s["id"]: s for s in bundle["sources"]}
    ids = set()
    for case in benchmark["examples"]:
        if case["id"] in ids or case["expert_review"] is not False:
            raise ValueError("Repeated example or false expertise")
        for field in ["fact", "target"]:
            record = case[field]
            if digest(record) != case[field + "_sha256"]:
                raise ValueError("Immutable example changed")
            support = record["source_support"]
            if (support["evidence_id"] not in record["evidence_ids"]
                or support["quote"] not in sources[support["evidence_id"]]["quote"]):
                raise ValueError("Example evidence is not its source excerpt")
            f = record["features"]
            if f["value_kind"] == "numeric" and not numeric_support(f["value"], support["quote"], record.get("source_number_word")):
                raise ValueError("Example number not in its source support")
        ids.add(case["id"])
    return {"examples": len(ids), "expert_review": False}
