"""Traceable AI source checks; does not confer scientific expert approval."""

import hashlib
import re
from .core import ROOT, read_json

START = "<!-- REVIEW STATUS START -->"
END = "<!-- REVIEW STATUS END -->"
HUMAN = "<!-- HUMAN NOTES -->"


def registry(root=ROOT):
    return read_json(
        root / "datasets/scientific_review.json",
        {"articles": {}, "issues": [], "findings": [], "status_labels": {}},
    )


def reviewed_body(text):
    body = text.split(HUMAN, 1)[0]
    return re.sub(
        r"\n*" + re.escape(START) + r".*?" + re.escape(END) + r"\n*",
        "\n\n",
        body,
        count=1,
        flags=re.S,
    ).strip()


def body_digest(text):
    return hashlib.sha256(reviewed_body(text).encode()).hexdigest()


def effective_status(name, root=ROOT, data=None):
    data = data or registry(root)
    entry = data["articles"].get(name)
    if not entry:
        return "needs_scientific_review"
    status = entry["status"]
    if status not in ("ai_source_checked", "specialist_verified"):
        return status
    p = root / "wiki" / name
    sources = {
        s["id"]: s
        for s in read_json(root / "config/sources.json", {"sources": []})["sources"]
    }
    if not p.is_file() or entry.get("body_sha256") != body_digest(p.read_text()):
        return "needs_scientific_review"
    if any(
        sources.get(sid, {}).get("sha256") != sha
        for sid, sha in entry.get("source_revisions", {}).items()
    ):
        return "needs_scientific_review"
    return status


def label(name, root=ROOT):
    data = registry(root)
    return data.get("status_labels", {}).get(
        effective_status(name, root, data), "Требуется научная рецензия"
    )


def validate(root=ROOT, require_local=False):
    data = registry(root)
    claims = read_json(root / "datasets/scientific_review_claims.json", {}).get(
        "claims", []
    )
    errors = []
    warnings = []
    ids = set()
    local = 0
    sources = {
        s["id"]: s
        for s in read_json(root / "config/sources.json", {"sources": []})["sources"]
    }
    kinds = {
        "source_statement",
        "source_interpretation",
        "editorial_inference",
        "methodological",
        "calculation",
    }
    decisions = {
        "confirmed",
        "qualified",
        "corrected",
        "source_unresolved",
        "method_limit",
    }
    for c in claims:
        cid = c["id"]
        if cid in ids:
            errors.append(f"duplicate reviewed claim: {cid}")
        ids.add(cid)
        if c.get("claim_type") not in kinds or c.get("decision") not in decisions:
            errors.append(f"invalid review decision/type: {cid}")
        for field in [
            "article_excerpt",
            "observation_period",
            "territory",
            "reason",
            "evidence",
        ]:
            if not c.get(field):
                errors.append(f"{cid}: missing {field}")
        article = (root / "wiki" / c["article"]).resolve()
        if (
            not article.is_relative_to((root / "wiki").resolve())
            or not article.is_file()
        ):
            errors.append(f"{cid}: invalid article")
            continue
        if c["article_excerpt"] not in reviewed_body(article.read_text()):
            warnings.append(f"stale reviewed claim: {cid}")
        for r in c["evidence"]:
            s = sources.get(r["source_id"])
            if (
                not s
                or not isinstance(r["page"], int)
                or r["page"] < 1
                or not r.get("quote")
            ):
                errors.append(f"{cid}: invalid source/page/quote")
                continue
            if s.get("sha256") != r["sha256"]:
                warnings.append(f"{cid}: source revision changed")
            elif r["page"] > s.get("pages", 0) or r["report_year"] != s.get(
                "report_year"
            ):
                errors.append(f"{cid}: source page/reporting year mismatch")
            p = read_json(
                root
                / "data/processed"
                / r["sha256"]
                / "pages"
                / f"{r['page']:04d}.json"
            )
            if p:
                local += 1
                if p.get("status") != "ok" or r["quote"] not in p.get("text", ""):
                    errors.append(f"{cid}: quote absent from cited checkpoint")
            elif require_local:
                errors.append(f"{cid}: checkpoint unavailable")
    used = []
    for name, entry in data["articles"].items():
        if entry["status"] not in data.get("status_labels", {}):
            errors.append(f"{name}: unknown review status")
        if entry["status"] == "specialist_verified":
            h = entry.get("human_confirmation") or {}
            if entry.get("reviewer_type") != "human" or not all(
                h.get(k) for k in ["confirmed_by", "confirmed_at", "evidence"]
            ):
                errors.append(
                    f"{name}: specialist status requires actual documented human confirmation"
                )
        if entry["status"] == "ai_source_checked" and (
            not entry.get("claim_ids") or entry.get("reviewer_type") != "ai_agent"
        ):
            errors.append(f"{name}: AI check without claims/reviewer")
        expected = {c["id"] for c in claims if c["article"] == name}
        if set(entry.get("claim_ids", [])) != expected:
            errors.append(f"{name}: reviewed claim inventory mismatch")
        used.extend(entry.get("claim_ids", []))
        if effective_status(name, root, data) != entry["status"]:
            warnings.append(
                f"{name}: stale article/source review; effective status downgraded"
            )
    if set(used) != ids:
        errors.append("claims not assigned to reviewed articles")
    issue_ids = {i["id"] for i in data.get("issues", [])}
    for c in claims:
        if set(c.get("issue_ids", [])) - issue_ids:
            errors.append(f"{c['id']}: unknown issue")
    return {
        "errors": errors,
        "warnings": warnings,
        "articles_reviewed": sum(
            bool(e.get("claim_ids")) for e in data["articles"].values()
        ),
        "claims_reviewed": len(claims),
        "evidence_references": sum(len(c["evidence"]) for c in claims),
        "checked_local_references": local,
        "articles_pending": sum(
            not e.get("claim_ids") for e in data["articles"].values()
        ),
    }


def sync_review_pages(root=ROOT):
    """Refresh only managed status blocks; preserve article bodies and human notes."""
    data = registry(root)
    if not data["articles"]:
        return 0
    from .wiki import write_page

    changed = 0
    lines = [
        "# Проверка знаний и нерешённые вопросы",
        "",
        "## Источники и доказательства",
        "",
        "Проверка AI-агентом по первоисточнику — содержательная сверка выбранных утверждений, не научная экспертиза. Статус относится к основному телу статьи; ручные заметки после HUMAN NOTES не входят в эту проверку. Ни одна статья не получила статус проверки специалистом.",
        "",
        "[Подробный отчёт спринта](../../docs/SCIENTIFIC_REVIEW.md)",
    ]
    for name, entry in data["articles"].items():
        p = root / "wiki" / name
        if not p.is_file():
            continue
        text = p.read_text()
        status = effective_status(name, root, data)
        title = text.splitlines()[0].lstrip("# ")
        anchor = "article-" + p.stem
        status_label = data["status_labels"][status]
        banner = f"{START}\nСтатус: **{status_label}**. Требуется научная рецензия. [Объём проверки](../topics/knowledge-quality.md#{anchor}).\n{END}"
        body, sep, notes = text.partition(HUMAN)
        body = re.sub(
            re.escape(START) + r".*?" + re.escape(END), "", body, count=1, flags=re.S
        )
        heading, _, rest = body.partition("\n")
        new = heading + "\n\n" + banner + "\n\n" + rest.lstrip()
        if sep:
            new = new.rstrip() + "\n\n" + HUMAN + notes
        if new != text:
            p.write_text(new)
            changed += 1
        lines += [
            "",
            f'<a id="{anchor}"></a>',
            f"### [{title}](../{name})",
            "",
            f"Статус: {status_label}. Проверено смысловых блоков: {len(entry.get('claim_ids', []))}. {entry['coverage']}",
        ]
    lines += ["", "## Нерешённые вопросы", ""]
    for issue in data.get("issues", []):
        lines += [
            f'<a id="{issue["id"]}"></a>',
            f"### {issue['title']}",
            "",
            f"Состояние: {issue['status']}. {issue['conclusion']}",
        ]
        lines += ["- " + s for s in issue["source_versions"]]
        seen = set()
        for r in issue["evidence"]:
            pair = r["source_id"], r["page"]
            if pair in seen:
                continue
            seen.add(pair)
            sid, n = pair
            lines.append(f"[{sid}:p{n}](../sources/{sid}.md#page-{n})")
        lines += ["", issue["next_step"], ""]
    lines += [
        "## Ограничения",
        "",
        "Точная цитата подтверждает место происхождения, но не удостоверяет измерение. Числовой confidence в наборах данных описывает перенос, а не экспертную достоверность. Официальная подлинность зеркальных копий не повышается AI-проверкой.",
        "[Тематическая энциклопедия](../catalog.md)",
    ]
    changed += write_page(root / "wiki/topics/knowledge-quality.md", "\n".join(lines))
    return changed
