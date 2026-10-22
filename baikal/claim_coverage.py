"""Article coverage and pairwise relations are separate, conservative questions."""

import copy
import re
from decimal import Decimal, InvalidOperation

from .core import read_json, sha256
from .updates import digest

DIMENSIONS = (
    "object",
    "indicator",
    "statistic",
    "unit",
    "territory",
    "period",
    "message_kind",
    "attribution",
    "comparison_scope",
)


def article_units(text):
    """Sentence inventory, not a claim that each sentence is atomic or true."""
    body = text.split("## Связанные статьи", 1)[0]
    body = re.sub(
        r"<!-- REVIEW STATUS START -->.*?<!-- REVIEW STATUS END -->",
        "",
        body,
        flags=re.S,
    )
    units = []
    cursor = 0
    for paragraph in body.split("\n\n"):
        if not paragraph.strip() or paragraph.lstrip().startswith("#"):
            continue
        clean = re.sub(r"\s*\[[^\]]+\]\([^)]+\)", "", paragraph).strip()
        for sentence in re.split(r"(?<=[.!?])\s+(?=[А-ЯЁ])", clean):
            start = text.index(sentence, cursor)
            cursor = start + len(sentence)
            units.append(
                {
                    "id": f"U{len(units) + 1:02d}",
                    "quote": sentence,
                    "start": start,
                    "end": start + len(sentence),
                }
            )
    return units


def validate_registry(registry, root, require_local=False):
    article = root / registry["article"]["path"]
    text = article.read_text()
    if (
        sha256(article) != registry["article"]["sha256"]
        or article_units(text) != registry["inventory"]
    ):
        raise ValueError("Article version or sentence inventory changed")
    if text.split("<!-- HUMAN NOTES -->", 1)[-1].strip():
        raise ValueError("Nonempty manual notes need a separate coverage review")
    old = read_json(root / registry["previous_registry"]["path"])
    if (
        sha256(root / registry["previous_registry"]["path"])
        != registry["previous_registry"]["sha256"]
    ):
        raise ValueError("Previous registry changed")
    if [c["legacy_record"] for c in registry["cards"] if "legacy_record" in c] != old[
        "claims"
    ]:
        raise ValueError("Original seven claims were altered")
    cfg = {s["id"]: s for s in read_json(root / "config/sources.json")["sources"]}
    sources = {s["id"]: s for s in registry["sources"]}
    for source in sources.values():
        if any(
            source[k] != cfg[source["source_id"]].get(k)
            for k in ["sha256", "report_year", "publication_year"]
        ):
            raise ValueError("Source revision or date changed")
        path = (
            root
            / "data/processed"
            / source["sha256"]
            / f"pages/{source['page']:04d}.json"
        )
        if require_local or path.exists():
            if source["quote"] not in read_json(path)["text"]:
                raise ValueError("Exact source excerpt absent")
    unit_ids = {u["id"] for u in registry["inventory"]}
    cards = {c["card_id"]: c for c in registry["cards"]}
    if len(cards) != len(registry["cards"]):
        raise ValueError("Repeated card")
    for c in cards.values():
        if (
            c["card_id"]
            != "V" + digest({k: v for k, v in c.items() if k != "card_id"})[:16]
        ):
            raise ValueError("Immutable card changed")
        span = c["article_evidence"]
        if (
            text[span["start"] : span["end"]] != span["quote"]
            or not set(c["unit_ids"]) <= unit_ids
        ):
            raise ValueError("Invalid article span/unit")
        if not c["source_evidence_ids"] or not set(c["source_evidence_ids"]) <= set(
            sources
        ):
            raise ValueError("Missing contextual source")
        if c["expert_review"] is not False or (
            c["matchable"] and c["review_status"] != "source_checked_non_expert"
        ):
            raise ValueError("Unverified material cannot be a matching authority")
    covered = {u for c in cards.values() for u in c["unit_ids"]}
    deferred = {d["unit_id"] for d in registry["deferred"]}
    if (
        covered & deferred
        or covered | deferred != unit_ids
        or registry["absence_authorized"] is not False
    ):
        raise ValueError("Coverage gap, overlapping deferral or unsafe absence claim")
    return {
        "sentence_units": len(unit_ids),
        "represented_units": len(covered),
        "deferred_units": len(deferred),
        "cards": len(cards),
        "source_checked_cards": sum(
            c["review_status"] == "source_checked_non_expert" for c in cards.values()
        ),
        "coverage_percent": round(100 * len(covered) / len(unit_ids), 2),
        "semantic_completeness_established": False,
    }


def canon(value, dimension, vocabulary):
    if not isinstance(value, str):
        return value
    return vocabulary["aliases"].get(dimension, {}).get(value, value)


def compare_pair(fact, target, vocabulary):
    """Question A only. No branch concludes global article absence."""
    f, t = fact["features"], target["features"]
    if f["message_kind"] != t["message_kind"]:
        return {"relation": "incomparable", "differences": ["message_kind"]}
    if any(
        f[k] is None or t[k] is None
        for k in ["object", "indicator", "statistic", "territory", "period"]
    ):
        return {"relation": "insufficient", "reason": "missing_comparison_dimension"}
    differences = [
        d
        for d in DIMENSIONS
        if canon(f[d], d, vocabulary) != canon(t[d], d, vocabulary)
    ]
    if differences:
        return {"relation": "incomparable", "differences": differences}
    if any(
        f[k] is None or t[k] is None
        for k in ["object", "indicator", "statistic", "territory", "period"]
    ):
        return {"relation": "insufficient", "reason": "missing_comparison_dimension"}
    if f["value_kind"] != t["value_kind"]:
        return {"relation": "incomparable", "differences": ["value_kind"]}
    if f["value_kind"] == "numeric":
        if f["value"] is None or t["value"] is None or f["unit"] is None:
            return {
                "relation": "insufficient",
                "reason": "missing_value_or_unit_not_zero",
            }
        try:
            a, b = (
                Decimal(f["value"].replace(",", ".")),
                Decimal(t["value"].replace(",", ".")),
            )
            if not a.is_finite() or not b.is_finite():
                raise InvalidOperation
        except InvalidOperation as exc:
            raise ValueError("Unsupported scalar value") from exc
        if a == b:
            return {"relation": "same_fact", "basis": "exact_typed_value"}
        check = fact.get("arithmetic_check")
        if check and target.get("arithmetic_group") == check["group"]:
            a0, a1 = map(Decimal, check["operands"])
            if a0 - a1 != a or check["source_binding"] != target.get("source_binding"):
                raise ValueError("Unbound or incorrect arithmetic check")
            return {
                "relation": "documented_arithmetic_discrepancy",
                "repair_source": False,
            }
        return {"relation": "potential_conflict_requires_review"}
    if f["value"] == t["value"]:
        return {"relation": "same_fact", "basis": "exact_qualitative_statement"}
    return {"relation": "semantic_required"}


def article_presence(
    fact,
    matched_cards,
    registry,
    absence_review=None,
    equivalence_reviews=None,
    vocabulary=None,
):
    """Question B. Partial/inventoried coverage never grants automatic novelty."""
    byid = {c["card_id"]: c for c in registry["cards"]}
    if matched_cards:
        if any(cid not in byid or not byid[cid]["matchable"] for cid in matched_cards):
            raise ValueError("Non-authoritative or unknown article match")
        for cid in matched_cards:
            card = byid[cid]
            result = compare_pair(fact, card, vocabulary or {"aliases": {}})
            if result["relation"] != "same_fact":
                review = (equivalence_reviews or {}).get(cid, {})
                if (
                    review.get("fact_sha256") != digest(fact)
                    or review.get("card_sha256") != digest(card)
                    or review.get("relation") != "semantic_equivalent"
                    or review.get("expert_review") is not False
                    or not review.get("reason")
                ):
                    raise ValueError(
                        "Article match lacks a bound semantic equivalence review"
                    )
        return {
            "status": "present_in_registered_passage",
            "card_ids": matched_cards,
            "automatic_publication": False,
        }
    if absence_review is not None:
        if (
            absence_review.get("fact_sha256") != digest(fact)
            or absence_review.get("article_sha256") != registry["article"]["sha256"]
            or absence_review.get("status") != "reviewed_absent_for_this_fact"
            or absence_review.get("expert_review") is not False
            or not absence_review.get("reason")
        ):
            raise ValueError("Missing fact/version-bound full-article review")
        return {
            "status": "reviewed_absent_for_this_fact",
            "candidate_only": True,
            "automatic_publication": False,
        }
    return {
        "status": "unknown_requires_full_article_review",
        "coverage_percent_does_not_prove_absence": True,
        "automatic_publication": False,
    }


def semantic_input(case):
    return {
        "fact_id": case["fact"]["fact_id"],
        "fact": copy.deepcopy(case["fact"]),
        "target_id": case["target"]["target_id"],
        "target": copy.deepcopy(case["target"]),
        "scope": "Relation to this one statement only. No conclusion about absence in the whole article.",
    }


def scan_registry(fact, registry, vocabulary):
    results = [
        {"card_id": c["card_id"], **compare_pair(fact, c, vocabulary)}
        for c in registry["cards"]
        if c["matchable"]
    ]
    return {
        "matches": [r["card_id"] for r in results if r["relation"] == "same_fact"],
        "semantic_targets": [
            r["card_id"] for r in results if r["relation"] == "semantic_required"
        ],
        "absence_proven": False,
    }


def validate_cases(benchmark, registry, vocabulary, root, require_local=False):
    if (
        benchmark["registry_sha256"] != digest(registry)
        or benchmark["article_sha256"] != registry["article"]["sha256"]
    ):
        raise ValueError("New benchmark version mismatch")
    refs = {s["id"]: s for s in benchmark["sources"]}
    cfg = {s["id"]: s for s in read_json(root / "config/sources.json")["sources"]}
    for ref in refs.values():
        if any(
            ref[k] != cfg[ref["source_id"]].get(k)
            for k in ["sha256", "report_year", "publication_year"]
        ):
            raise ValueError("Benchmark source version changed")
        path = root / "data/processed" / ref["sha256"] / f"pages/{ref['page']:04d}.json"
        if require_local or path.exists():
            if ref["quote"] not in read_json(path)["text"]:
                raise ValueError("Benchmark source quote absent")
    for case in benchmark["examples"]:
        for key in ["fact", "target"]:
            r = case[key]
            if digest(r) != case[key + "_sha256"]:
                raise ValueError("Immutable independent test input changed")
            if "source_binding" not in r:
                continue
            ref = refs[r["source_evidence_id"]]
            if r["source_binding"] != digest(ref):
                raise ValueError("Wrong supporting document")
            cell = r.get("source_cell")
            if cell:
                raw = cell["raw_value"]
                path = (
                    root
                    / "data/processed"
                    / ref["sha256"]
                    / f"pages/{ref['page']:04d}.json"
                )
                if require_local or path.exists():
                    rows = read_json(path)["tables"][cell["table_index"]]["rows"]
                    if (
                        rows[cell["row_index"]] != cell["row"]
                        or rows[0] != cell["header"]
                        or rows[cell["row_index"]][cell["column_index"]] != raw
                    ):
                        raise ValueError("Wrong table cell/header/column")
                expected = (
                    raw.splitlines()[-1].strip()
                    if r["features"]["statistic"] == "mean"
                    else re.split(r"[–-]", raw.splitlines()[0])[-1].strip()
                )
                if expected != r["features"]["value"]:
                    raise ValueError("Mean/max value crosses table statistic")
                if (
                    r["features"]["period"].get("month") != 9
                    or "Сентябрь"
                    not in cell["header"][cell["column_index"]].capitalize()
                ):
                    raise ValueError("Wrong observation season")
        if case["scope"] == "article":
            card = next(
                (
                    c
                    for c in registry["cards"]
                    if c["card_id"] == case["target"]["target_id"]
                ),
                None,
            )
            if (
                not card
                or not card["matchable"]
                or case["target"]["features"] != card["features"]
                or case["target"]["article_quote"] != card["article_evidence"]["quote"]
            ):
                raise ValueError("Unbound target article claim")
    return {
        "cases": len(benchmark["examples"]),
        "semantic_cases": [
            c["case_id"]
            for c in benchmark["examples"]
            if compare_pair(c["fact"], c["target"], vocabulary)["relation"]
            == "semantic_required"
        ],
    }
