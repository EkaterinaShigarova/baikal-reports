"""Persistent evidence wiki, incremental source compilation, query and lint."""

from __future__ import annotations
import datetime, hashlib, re
from pathlib import Path
from .core import (
    ROOT,
    atomic_json,
    atomic_text,
    load_sources,
    page_dir,
    read_json,
    validate_observation,
)

WIKI = ROOT / "wiki"
TOPICS = {
    "water-quality": (
        "Качество воды",
        r"качество вод|гидрохим|концентрац|загрязнен.{0,4}вод",
    ),
    "hydrology": (
        "Гидрология и водный баланс",
        r"уровень.{0,15}Байкал|водный баланс|водность|приток",
    ),
    "wastewater": (
        "Сточные воды и водопользование",
        r"сточн.{0,5}вод|водоотвед|водопотреб|водозабор",
    ),
    "biodiversity": (
        "Биоразнообразие и экосистемы",
        r"биоразнообраз|омул|нерп|эндемич|фитопланктон",
    ),
    "air": ("Атмосферный воздух", r"атмосферн.{0,8}воздух|выброс.{0,15}атмосфер"),
    "waste": (
        "Отходы и накопленный ущерб",
        r"отход.{0,15}производ|накопленн.{0,12}ущерб|БЦБК",
    ),
    "forests": ("Леса и пожары", r"лесн.{0,5}пожар|площад.{0,12}лес|лесовосстанов"),
    "protection": (
        "Охрана территории и управление",
        r"охран.{0,15}территор|экологическ.{0,8}надзор|государственн.{0,8}мониторинг",
    ),
}
ENTITIES = {
    "baikal": ("Озеро Байкал", r"Байкал"),
    "selenga": ("Река Селенга", r"Селенг"),
    "baikal-natural-area": (
        "Байкальская природная территория",
        r"Байкальск.{0,5}природн.{0,5}территор",
    ),
}
MARK = "<!-- HUMAN NOTES -->"


def write_page(path, content):
    path = Path(path)
    old = path.read_text() if path.exists() else ""
    notes = old.split(MARK, 1)[1] if MARK in old else "\n"
    new = (
        "\n".join(
            line.rstrip()
            for line in (content.rstrip() + "\n\n" + MARK + notes).splitlines()
        )
        + "\n"
    )
    if new != old:
        atomic_text(path, new)
        return True
    return False


def append_log(operation, message):
    path = WIKI / "LOG.md"
    old = path.read_text() if path.exists() else "# Журнал Wiki\n"
    date = datetime.datetime.now(datetime.timezone.utc).isoformat(timespec="seconds")
    atomic_text(path, old + f"\n## [{date}] {operation} | {message}\n")


def scoped_text(source, page):
    """Keep national-review evidence within its reviewed Baikal section."""
    ranges = source.get("wiki_page_ranges")
    if ranges and not any(a <= page["page"] <= b for a, b in ranges):
        return ""
    text = page["text"]
    stop = source.get("wiki_stop_before", {}).get(str(page["page"]))
    return text.split(stop, 1)[0] if stop else text


def build():
    sources = load_sources()["sources"]
    local_ids = {
        s["id"]
        for s in sources
        if s.get("sha256") and any((page_dir(s) / "pages").glob("*.json"))
    }
    previous = read_json(ROOT / "datasets/wiki_evidence.json", {})
    evidence = {
        k: [e for e in previous.get(k, []) if e["source_id"] not in local_ids]
        for k in TOPICS
    }
    entities = {k: [] for k in ENTITIES}
    changed = 0
    index = []
    for s in sources:
        if not s.get("sha256"):
            continue
        pages = [read_json(p) for p in sorted((page_dir(s) / "pages").glob("*.json"))]
        pages = [p for p in pages if p["status"] == "ok"]
        if not pages:
            continue
        title = s["name"]
        sid = s["id"]
        topics = set()
        ent = set()
        per_topic = {}
        summary = [
            f"# {title}",
            "",
            f"Год доклада: {s.get('report_year', 'не подтверждён')}. Подлинность: {s['authenticity']}.",
            f"[Документ у источника]({s['url']}) · SHA256 `{s['sha256']}`",
            "",
            "## Источники и доказательства",
            "",
            f"Извлечено {len(pages)} из {s.get('pages', '?')} страниц. Нумерация ниже физическая, от 1.",
            "",
        ]
        for p in pages:
            text = scoped_text(s, p)
            n = p["page"]
            first = " ".join(p["text"].split())[:230].rstrip()
            for key, (name, pattern) in TOPICS.items():
                m = re.search(pattern, text, re.I)
                if m:
                    topics.add(key)
                    if per_topic.get(key, 0) < 2:
                        start = max(0, text.rfind("\n", 0, m.start()))
                        quote = " ".join(text[start : m.end() + 260].split())
                        evidence[key].append(
                            {
                                "source_id": sid,
                                "page": n,
                                "quote": quote,
                                "year": s.get("report_year"),
                            }
                        )
                        per_topic[key] = per_topic.get(key, 0) + 1
            for key, (name, pattern) in ENTITIES.items():
                if key not in ent and re.search(pattern, text, re.I):
                    entities[key].append({"source_id": sid, "page": n})
                    ent.add(key)
            summary += [
                f'<a id="page-{n}"></a>',
                f"### Страница {n}",
                f"{first}",
                f"[{sid}:p{n}]({s['url']}#page={n}) · метод: {p['method']}; таблиц: {len(p['tables'])}.",
                "",
            ]
        summary += ["## Связанные темы", ""] + [
            f"- [{TOPICS[k][0]}](../topics/{k}.md)" for k in sorted(topics)
        ]
        summary += [
            "",
            "## Ограничения",
            "Текст — автоматическое извлечение. Таблицы, графики и OCR требуют проверки. "
            + (
                "Публикация скачана с официального сайта; это не проверка электронной подписи."
                if s["authenticity"] == "official_verified"
                else "Соответствие зеркала официальной публикации отдельно не подтверждено."
            ),
        ]
        changed += write_page(WIKI / "sources" / f"{sid}.md", "\n".join(summary))
        index.append((f"sources/{sid}.md", title))
    for key, (name, _) in TOPICS.items():
        if not evidence[key]:
            continue
        rows = evidence[key]
        lines = [
            f"# {name}",
            "",
            "Страница автоматически обновляется из сохранённых доказательств. Ниже выдержки из докладов, а не заключение о тенденциях.",
            "",
            "## Источники и доказательства",
            "",
        ]
        for e in rows:
            lines += [
                f"> {e['quote']}",
                f"— [{e['source_id']}:p{e['page']}](../sources/{e['source_id']}.md#page-{e['page']}); год: {e['year'] or 'не определён'}.",
                "",
            ]
        links = [k for k in TOPICS if k != key and evidence[k]]
        lines += ["## Связанные темы", ""] + [
            f"- [{TOPICS[k][0]}]({k}.md)" for k in links
        ]
        lines += [
            "",
            "## Ограничения",
            "Выдержки выбраны по тематическим терминам. Количественные сравнения требуют проверки единиц, периода, территории и методики. Различия формулировок не объявляются противоречием автоматически.",
        ]
        changed += write_page(WIKI / "topics" / f"{key}.md", "\n".join(lines))
        index.append((f"topics/{key}.md", name))
    for key, (name, _) in ENTITIES.items():
        if not entities[key]:
            continue
        lines = [f"# {name}", "", "## Источники и доказательства", ""]
        for e in entities[key]:
            lines.append(
                f"- Упоминание: [{e['source_id']}:p{e['page']}](../sources/{e['source_id']}.md#page-{e['page']})."
            )
        lines += [
            "",
            "## Ограничения",
            "Упоминание сущности не подтверждает границы территории измерения.",
            "",
            "[Тематический индекс](../index.md)",
        ]
        changed += write_page(WIKI / "entities" / f"{key}.md", "\n".join(lines))
        index.append((f"entities/{key}.md", name))
    # Include human-authored synthesis and saved queries in the index too.
    known = {p for p, _ in index}
    for folder in ("sources", "topics", "entities", "syntheses", "queries"):
        for path in sorted((WIKI / folder).glob("*.md")):
            rel = str(path.relative_to(WIKI))
            if rel not in known:
                index.append((rel, path.read_text().splitlines()[0].lstrip("# ")))
                known.add(rel)
    changed += write_page(
        WIKI / "index.md",
        "# Baikal LLM Wiki\n\nСохранённая база знаний с проверяемыми ссылками.\n\n"
        + "\n".join(f"- [{title}]({path})" for path, title in index)
        + "\n\n[Журнал](LOG.md)\n\n## Ограничения\nПокрытие и достоверность указаны в [реестре источников](../SOURCES.md).",
    )
    if changed:
        append_log(
            "ingest",
            f"Обновлено страниц: {changed}; источников с извлечёнными страницами: {sum(bool(s.get('extracted_pages')) for s in sources)}",
        )
    atomic_json(ROOT / "datasets/wiki_evidence.json", evidence)
    return {"changed_pages": changed, "indexed_pages": len(index)}


def query(question, save=False):
    tokens = re.findall(r"\w{3,}", question.lower())
    hits = []
    for p in WIKI.rglob("*.md"):
        if p.name == "LOG.md":
            continue
        t = p.read_text()
        score = sum(t.lower().count(x) for x in tokens)
        if score:
            hits.append((score, p, t))
    hits = sorted(hits, key=lambda h: (-h[0], str(h[1])))[:6]
    answer = "# Поиск в сохранённой Wiki\n\nЗапрос: " + question + "\n\n"
    answer += "Результаты поиска по статьям; это не новое экологическое заключение.\n\n"
    for _, p, t in hits:
        answer += f"- [{t.splitlines()[0].lstrip('# ')}]({p.relative_to(WIKI)})\n"
    if not hits:
        answer += (
            "Соответствий нет; недостаток сведений не означает отсутствие явления.\n"
        )
    if save:
        name = hashlib.sha256(question.encode()).hexdigest()[:12]
        stored = answer
        for _, p, _ in hits:
            stored = stored.replace(
                f"]({p.relative_to(WIKI)})", f"](../{p.relative_to(WIKI)})"
            )
        write_page(WIKI / "queries" / f"{name}.md", stored)
        append_log("query", question[:150])
        build()
    return answer


def lint():
    errors = []
    warnings = []
    cat = {s["id"]: s for s in load_sources()["sources"]}
    provenance = read_json(ROOT / "datasets/article_provenance.json", {})
    for name, refs in provenance.items():
        if not (WIKI / name).is_file():
            errors.append(f"article provenance without page: {name}")
        for ref in refs:
            current = cat.get(ref["source_id"])
            if not current or current.get("sha256") != ref.get("sha256"):
                warnings.append(
                    f"stale synthesis: {name}: source revision {ref['source_id']}; review before reuse"
                )
    for path in (WIKI / "syntheses").glob("*.md"):
        if str(path.relative_to(WIKI)) not in provenance:
            warnings.append(f"synthesis missing revision provenance: {path.name}")
    links = {}
    allpages = set(WIKI.rglob("*.md"))
    for path in allpages:
        text = path.read_text()
        links[path] = []
        for target in re.findall(r"\]\(([^)]+)\)", text):
            if re.match(r"https?://", target):
                continue
            file, _, anchor = target.partition("#")
            dest = (path.parent / file).resolve()
            if not dest.exists():
                errors.append(f"{path.relative_to(ROOT)}: missing link {target}")
            elif anchor and f'id="{anchor}"' not in dest.read_text():
                errors.append(f"{path.name}: missing anchor {target}")
            links[path].append(dest)
        for sid, n in re.findall(r"\[((?:drive|official|local)-[\w-]+):p(\d+)\]", text):
            if sid not in cat or int(n) > cat[sid].get("pages", 0):
                errors.append(f"{path.name}: invalid source/page {sid}:{n}")
    inbound = {dest for out in links.values() for dest in out}
    for path in allpages:
        if path.name not in ("index.md", "LOG.md") and path.resolve() not in inbound:
            warnings.append(f"orphan page: {path.relative_to(WIKI)}")
    rows = read_json(ROOT / "datasets/observations.json", [])
    identities = {}
    for row in rows:
        errors.extend(f"{row.get('id')}: {e}" for e in validate_observation(row))
        s = cat.get(row.get("source_id"))
        if (
            not s
            or row.get("sha256") != s.get("sha256")
            or row.get("page", 0) > s.get("pages", 0)
        ):
            errors.append(f"{row.get('id')}: source provenance mismatch")
        key = tuple(
            row.get(k)
            for k in ["year", "indicator", "unit", "territory", "period", "method"]
        )
        identities.setdefault(key, set()).add(str(row.get("value")))
    for key, vals in identities.items():
        if len(vals) > 1:
            warnings.append(f"potential contradiction: {key}: {sorted(vals)}")
    for y in range(2003, 2027):
        if not any(s.get("report_year") == y for s in cat.values()):
            warnings.append(f"year {y}: no title-verified processed source")
    return {
        "errors": errors,
        "warnings": warnings,
        "pages": len(allpages),
        "observations": len(rows),
    }
