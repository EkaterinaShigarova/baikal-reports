"""Conservative, version-bound annotations preserve the experimental snapshot."""

from .updates import digest


def audit_statuses(registry, audit):
    if (
        audit["registry_sha256"] != digest(registry)
        or audit["article_sha256"] != registry["article"]["sha256"]
        or audit["expert_review"] is not False
    ):
        raise ValueError(
            "Audit refers to a different article/registry or claims expertise"
        )
    cards = {c["card_id"]: c for c in registry["cards"]}
    effective = {cid: c["review_status"] for cid, c in cards.items()}
    seen = set()
    for note in audit["overrides"]:
        card = cards.get(note["card_id"])
        if (
            not card
            or note["card_id"] in seen
            or digest(card) != note["card_sha256"]
            or card["review_status"] != note["original_status"]
        ):
            raise ValueError("Unbound/repeated audit annotation")
        if (
            card["matchable"]
            or note["promotion"] is not False
            or note["effective_status"]
            != "methodological_context_not_direct_source_fact"
            or not note["reason"]
        ):
            raise ValueError(
                "Audit cannot promote a claim or silently change matching authority"
            )
        effective[note["card_id"]] = note["effective_status"]
        seen.add(note["card_id"])
    count = sum(status == "source_checked_non_expert" for status in effective.values())
    if count != audit["effective_source_checked_cards"]:
        raise ValueError("Misreported effective source-check count")
    return {
        "effective_statuses": effective,
        "effective_source_checked_cards": count,
        "expert_review": False,
    }
