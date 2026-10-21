"""Read-only local research dashboard and evidence API."""

from fastapi import FastAPI, HTTPException, Query
from fastapi.responses import FileResponse, PlainTextResponse
from fastapi.staticfiles import StaticFiles
from .core import ROOT, DATA, load_sources, page_dir, read_json, comparable
from .store import search, observations
from .wiki import WIKI

app = FastAPI(title="Baikal LLM Wiki", version="0.1.0")
app.mount("/static", StaticFiles(directory=ROOT / "web"), name="static")


def source(sid):
    s = next((s for s in load_sources()["sources"] if s["id"] == sid), None)
    if not s:
        raise HTTPException(404, "Источник не найден")
    return s


@app.get("/")
def home():
    return FileResponse(ROOT / "web/index.html")


@app.get("/api/overview")
def overview():
    sources = load_sources()["sources"]
    summaries = []
    for s in sources:
        s["local_available"] = bool(
            s.get("sha256") and (DATA / "raw" / f"{s['sha256']}.pdf").exists()
        )
    for s in sources:
        if s.get("sha256"):
            summary = read_json(page_dir(s) / "summary.json")
            if summary:
                summaries.append(summary)
    return {
        "sources": sources,
        "summaries": summaries,
        "observations": len(observations()),
        "verified_official": sum(
            s.get("authenticity") == "official_verified" for s in sources
        ),
        "pages": sum(s.get("extracted_pages", 0) for s in sources),
        "scope": "2003–2026: целевой диапазон; фактическое покрытие указано по источникам.",
    }


@app.get("/api/search")
def find(
    q: str = Query(min_length=1, max_length=300),
    limit: int = Query(default=30, ge=1, le=100),
):
    return search(q, limit)


@app.get("/api/observations")
def data(
    territory: str | None = None,
    year: int | None = None,
    year_from: int | None = None,
    year_to: int | None = None,
):
    if year_from is not None and year_to is not None and year_from > year_to:
        raise HTTPException(422, "Начальный год больше конечного")
    return [
        r
        for r in observations()
        if (not territory or r["territory"] == territory)
        and (not year or r["year"] == year)
        and (year_from is None or r["year"] >= year_from)
        and (year_to is None or r["year"] <= year_to)
        and r["review_status"] == "reviewed"
    ]


@app.get("/api/series")
def series(group: str):
    rows = [r for r in observations() if r["comparability_group"] == group]
    if not rows:
        raise HTTPException(404, "Ряд не найден")
    if not comparable(rows):
        raise HTTPException(
            422, "Сопоставимость не подтверждена: сравнение годов отключено."
        )
    return sorted(rows, key=lambda r: r["year"])


@app.get("/api/wiki")
def wiki_list():
    return [
        {
            "path": str(p.relative_to(WIKI)),
            "title": p.read_text().splitlines()[0].lstrip("# "),
        }
        for p in sorted(WIKI.rglob("*.md"))
    ]


@app.get("/api/wiki/{name:path}", response_class=PlainTextResponse)
def wiki_page(name: str):
    path = (WIKI / name).resolve()
    if (
        not path.is_relative_to(WIKI.resolve())
        or path.suffix != ".md"
        or not path.is_file()
    ):
        raise HTTPException(404, "Страница не найдена")
    return path.read_text()


@app.get("/api/documents/{sid}/pages/{n}")
def page(sid: str, n: int):
    s = source(sid)
    if n < 1 or n > s.get("pages", 0) or not s.get("sha256"):
        raise HTTPException(404, "Страница не найдена")
    result = read_json(page_dir(s) / "pages" / f"{n:04d}.json")
    if not result:
        raise HTTPException(404, "Страница ещё не обработана")
    audit = next(
        (a for a in read_json(page_dir(s) / "table_audit.json", []) if a["page"] == n),
        None,
    )
    return dict(result, source=s, table_audit=audit)


@app.get("/api/documents/{sid}/preview/{n}")
def preview(sid: str, n: int):
    s = source(sid)
    if not s.get("sha256") or n < 1 or n > s.get("pages", 0):
        raise HTTPException(404)
    path = page_dir(s) / "previews" / f"{n:04d}.png"
    if not path.exists():
        raise HTTPException(404, "Превью не создано")
    return FileResponse(path)


@app.get("/api/documents/{sid}/pdf")
def original(sid: str):
    s = source(sid)
    if not s.get("sha256"):
        raise HTTPException(404)
    path = DATA / "raw" / f"{s['sha256']}.pdf"
    if not path.exists():
        raise HTTPException(404, "PDF не скачан в эту среду")
    return FileResponse(
        path,
        media_type="application/pdf",
        filename=s["name"],
        content_disposition_type="inline",
    )


@app.get("/api/export/observations.csv")
def csv_export():
    return FileResponse(
        ROOT / "datasets/observations.csv",
        media_type="text/csv",
        filename="baikal-observations.csv",
    )


@app.get("/api/candidates")
def candidate_data(
    source_id: str | None = None,
    limit: int = Query(default=100, ge=1, le=500),
    offset: int = Query(default=0, ge=0),
):
    from .store import candidates

    return candidates(source_id, limit, offset)
