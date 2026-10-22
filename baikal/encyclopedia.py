"""Small editorial catalog over the existing persistent Wiki; no PDF processing."""

import re
from .core import ROOT, read_json


def catalog(root=ROOT):
    return read_json(
        root / "config/encyclopedia.json", {"sections": [], "evidence_levels": {}}
    )


def topics(config):
    for section in config["sections"]:
        for sub in section["subtopics"]:
            for topic in sub["topics"]:
                yield section, sub, topic


def render_catalog(root=ROOT):
    config = catalog(root)
    if not config["sections"]:
        return None
    source_years = {
        s["id"]: s.get("report_year")
        for s in read_json(root / "config/sources.json", {"sources": []})["sources"]
    }
    lines = [
        "# Тематическая энциклопедия Байкала",
        "",
        "Разделы → подтемы → аналитические статьи. Каталог объединяет содержательные статьи и показывает очередь тем, для которых статья ещё не подготовлена.",
        "",
        "## Источники и доказательства",
        "",
        "Обеспеченность означает пригодность выбранных страниц для текста, а не подтверждение подлинности зеркала или сопоставимости числовых рядов. Указаны физические страницы PDF и годы докладов; год отдельного наблюдения может отличаться. Статусы официальной публикации приведены в реестре источников.",
        "",
    ]
    labels = config.get("evidence_labels", {})
    for name, meaning in config["evidence_levels"].items():
        lines.append(f"- {labels.get(name, name)}: {meaning}")
    for section in config["sections"]:
        lines += ["", f'<a id="{section["id"]}"></a>', f"## {section['title']}", ""]
        for sub in section["subtopics"]:
            lines += [f'<a id="{sub["id"]}"></a>', f"### {sub['title']}", ""]
            for topic in sub["topics"]:
                label = (
                    f"[{topic['title']}]({topic['article']})"
                    if topic.get("article")
                    else topic["title"] + " — в очереди, статья не создана"
                )
                lines += [
                    f"- {label}",
                    "",
                    f"Обеспеченность: {labels.get(topic['evidence_level'], topic['evidence_level'])}. {topic['gap']}",
                    "",
                ]
                for ref in topic["sources"]:
                    sid, n = ref["source_id"], ref["page"]
                    lines.append(
                        f"[{sid}:p{n}](sources/{sid}.md#page-{n}) · доклад {source_years.get(sid, 'год не подтверждён')}."
                    )
                lines.append("")
    lines += [
        "## Ограничения",
        "",
        "Пробелы в каталогах не означают отсутствие явления или доклада. Найденная страница и опубликованная аналитическая статья имеют разные статусы. Научная экспертиза причинных объяснений и массовая валидация таблиц остаются отдельными задачами.",
        "",
        "[Все страницы Wiki](index.md)",
    ]
    return "\n".join(lines)


def validate_catalog(config, root, sources):
    errors = []
    ids = set()
    paths = set()
    w = (root / "wiki").resolve()
    for section, sub, topic in topics(config):
        if topic["id"] in ids:
            errors.append(f"duplicate encyclopedia topic: {topic['id']}")
        ids.add(topic["id"])
        if topic.get("evidence_level") not in config["evidence_levels"]:
            errors.append(f"unknown evidence level: {topic['id']}")
        article = topic.get("article")
        if topic.get("status") == "published" and not article:
            errors.append(f"published topic without article: {topic['id']}")
        if article:
            p = (w / article).resolve()
            if not p.is_relative_to(w) or not p.is_file() or p.suffix != ".md":
                errors.append(f"invalid encyclopedia article: {article}")
            if article in paths:
                errors.append(f"duplicate encyclopedia article: {article}")
            paths.add(article)
        if not topic.get("sources"):
            errors.append(f"topic without evidence: {topic['id']}")
        for ref in topic.get("sources", []):
            source = sources.get(ref["source_id"])
            if (
                not source
                or source.get("sha256") != ref.get("sha256")
                or not 1 <= ref["page"] <= source.get("pages", 0)
            ):
                errors.append(
                    f"encyclopedia source revision/page mismatch: {topic['id']}"
                )
    for section in config["sections"]:
        for node in [section] + section["subtopics"]:
            if node["id"] in ids:
                errors.append(f"duplicate encyclopedia anchor: {node['id']}")
            ids.add(node["id"])
    return errors


def search_articles(query, limit=30, root=ROOT):
    tokens = re.findall(r"\w+", query.casefold().replace("ё", "е"))[:15]
    if not tokens:
        return []
    results = []
    w = (root / "wiki").resolve()
    for section, sub, topic in topics(catalog(root)):
        if topic["status"] != "published" or not topic.get("article"):
            continue
        p = (w / topic["article"]).resolve()
        if not p.is_relative_to(w) or not p.is_file():
            continue
        raw = p.read_text()
        body = raw.casefold().replace("ё", "е")
        title = topic["title"].casefold().replace("ё", "е")
        if not all(t in body or t in title for t in tokens):
            continue
        score = sum(10 * (t in title) + min(body.count(t), 5) for t in tokens)
        offset = min((body.find(t) for t in tokens if t in body), default=0)
        snippet = " ".join(raw[max(0, offset - 80) : offset + 240].split())
        results.append(
            {
                "path": topic["article"],
                "title": topic["title"],
                "section": section["title"],
                "subtopic": sub["title"],
                "snippet": snippet,
                "score": score,
            }
        )
    return sorted(results, key=lambda r: (-r["score"], r["title"]))[:limit]


def check_evidence(root=ROOT, require_local=False):
    evidence = read_json(root / "datasets/encyclopedia_evidence.json", {}).get(
        "articles", {}
    )
    sources = {
        s["id"]: s
        for s in read_json(root / "config/sources.json", {"sources": []})["sources"]
    }
    errors = []
    local = 0
    refs = set()
    citations = 0
    years = set()
    for name, records in evidence.items():
        article = (root / "wiki" / name).resolve()
        if (
            not article.is_relative_to((root / "wiki").resolve())
            or not article.is_file()
        ):
            errors.append(f"{name}: missing or invalid article")
            continue
        text = article.read_text()
        cited = re.findall(r"\[((?:drive|official)-[\w-]+):p(\d+)\]", text)
        citations += len(cited)
        recorded = {(r["source_id"], str(r["page"])) for r in records}
        if set(cited) != recorded:
            errors.append(f"{name}: citation/evidence mismatch")
        for r in records:
            sid, n = r["source_id"], r["page"]
            refs.add((sid, n))
            years.add(r["report_year"])
            source = sources.get(sid, {})
            if source.get("sha256") != r["sha256"] or not 1 <= n <= source.get(
                "pages", 0
            ):
                errors.append(f"{name}: source hash/page mismatch")
                continue
            if source.get("report_year") != r["report_year"]:
                errors.append(f"{name}: reporting year mismatch")
            if not r.get("statement") or not r.get("quote"):
                errors.append(f"{name}: missing statement/quote")
            if len(re.findall(r"\.{5,}", r["quote"])) >= 5:
                errors.append(f"{name}: possible contents page")
            path = (
                root / "data/processed" / source["sha256"] / "pages" / f"{n:04d}.json"
            )
            page = read_json(path)
            if page:
                local += 1
                if page.get("status") != "ok" or r["quote"] not in page.get("text", ""):
                    errors.append(f"{name}: quote absent from cached page {sid}:{n}")
            elif require_local:
                errors.append(f"{name}: cached page unavailable {sid}:{n}")
    return {
        "articles": len(evidence),
        "article_page_references": sum(map(len, evidence.values())),
        "unique_source_pages": len(refs),
        "documents": len({s for s, n in refs}),
        "years": sorted(years),
        "citation_occurrences": citations,
        "checked_local_references": local,
        "errors": errors,
    }
