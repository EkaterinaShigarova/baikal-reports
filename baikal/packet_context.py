"""Post-experiment projection: include exact matched article claims, not only IDs.

Prepared after the three real calls. No additional model run is implied.
"""
from .evidence_packets import compact_context
from .updates import digest


def with_article_claims(packet):
    wire = compact_context(packet)
    previous = digest(wire)
    claims = []
    for fact in packet["facts"]:
        for bound in fact.get("article_support", []):
            claims.append({"fact_id": fact["id"], "article_path": bound["path"],
                           "article_sha256": bound["sha256"], "exact_article_quote": bound["quote"],
                           "scope": "matched_claim_only_not_complete_article_coverage"})
    return {**wire, "context_version": 2, "parent_context_sha256": previous,
            "matched_article_claims": claims}
