"""Read-only HTTPS discovery, immutable downloads, and explicit provenance."""

from __future__ import annotations
import datetime, html, json, re, shutil, urllib.request, urllib.parse
from html.parser import HTMLParser
from .core import DATA, MANIFEST, ROOT, atomic_json, atomic_text, load_sources, sha256

FOLDER_ID = "1WokrBQQKYGPQstIU7Xg8x8EX_zJKpL6M"
OFFICIAL_SEEDS = [
    "https://www.mnr.gov.ru/docs/gosudarstvennye_doklady/",
    "https://www.mnr.gov.ru/activity/directions/okhrana_ozera_baykal/",
    "https://baikalake.ru/security/info/reports/",
    "https://voda.gov.ru/",
    "https://www.meteorf.gov.ru/",
]
OFFICIAL_HOSTS = {
    "www.mnr.gov.ru",
    "mnr.gov.ru",
    "baikalake.ru",
    "www.baikalake.ru",
    "voda.gov.ru",
    "www.meteorf.gov.ru",
}


def open_url(url, timeout=90):
    if urllib.parse.urlsplit(url).scheme != "https":
        raise ValueError("Source downloads require HTTPS")
    req = urllib.request.Request(
        url, headers={"User-Agent": "BaikalResearch/0.1 (source-access-test)"}
    )
    return urllib.request.urlopen(req, timeout=timeout)


def parse_drive_listing(text):
    m = re.search(r"window\['_DRIVE_ivd'\] = '((?:[^'\\]|\\.)*)'", text)
    if not m:
        raise ValueError(
            "Drive listing format unavailable; authentication or markup change possible"
        )
    decoded = re.sub(
        r"\\x([0-9a-fA-F]{2})", lambda m: chr(int(m[1], 16)), m[1]
    ).replace("\\/", "/")
    rows = json.loads(decoded)[0]
    return [
        {
            "drive_id": r[0],
            "name": r[2],
            "mime": r[3],
            "expected_bytes": r[13],
            "remote_modified_ms": r[9],
        }
        for r in rows
        if r[3] == "application/pdf"
    ]


def discover_drive(folder_id=FOLDER_ID):
    url = f"https://drive.google.com/drive/folders/{folder_id}"
    with open_url(url) as r:
        files = parse_drive_listing(r.read().decode("utf-8"))
    catalog = load_sources()
    existing = {s["id"]: s for s in catalog["sources"]}
    for f in files:
        sid = "drive-" + f["drive_id"]
        year_match = re.findall(r"20(?:0\d|1\d|2[0-6])", f["name"])
        existing.setdefault(sid, {"id": sid, "status": "discovered"})
        s = existing[sid]
        previous_size = s.get("expected_bytes")
        if (previous_size and previous_size != f["expected_bytes"]) or (
            s.get("remote_modified_ms")
            and s["remote_modified_ms"] != f["remote_modified_ms"]
        ):
            s["remote_changed"] = True
        s.update(f)
        s.update(
            {
                "url": f"https://drive.google.com/file/d/{f['drive_id']}/view",
                "download_url": f"https://drive.usercontent.google.com/download?id={f['drive_id']}&export=download",
                "year_hint": int(year_match[0]) if year_match else None,
                "authenticity": s.get("authenticity", "mirror_unverified"),
                "official_url": s.get("official_url"),
                "agency": s.get("agency", "Требует проверки титула"),
                "last_seen": datetime.datetime.now(datetime.timezone.utc).isoformat(),
            }
        )
    catalog["sources"] = list(existing.values())
    atomic_json(MANIFEST, catalog)
    write_sources()
    return files


class Links(HTMLParser):
    def __init__(self):
        super().__init__()
        self.links = []

    def handle_starttag(self, tag, attrs):
        if tag == "a":
            href = dict(attrs).get("href")
            if href:
                self.links.append(href)


def discover_official(max_pages=12):
    catalog = load_sources()
    queue = list(OFFICIAL_SEEDS)
    visited = set()
    results = []
    while queue and len(visited) < max_pages:
        url = queue.pop(0)
        if url in visited:
            continue
        visited.add(url)
        try:
            with open_url(url, 25) as r:
                body = r.read(4 * 1024 * 1024).decode("utf-8", "replace")
                result = {
                    "url": url,
                    "status": r.status,
                    "checked_at": datetime.datetime.now(
                        datetime.timezone.utc
                    ).isoformat(),
                }
            parser = Links()
            parser.feed(body)
            found = []
            for href in parser.links:
                link = urllib.parse.urljoin(url, html.unescape(href))
                host = urllib.parse.urlsplit(link).hostname
                if host not in OFFICIAL_HOSTS or not link.startswith("https://"):
                    continue
                if re.search(r"\.pdf(?:\?|$)", link, re.I):
                    found.append(link)
                elif (
                    re.search(r"baikal|baykal|байкал|doklad|report", link, re.I)
                    and link not in visited
                ):
                    queue.append(link)
            result["pdf_links"] = sorted(set(found))
            results.append(result)
        except Exception as exc:
            results.append(
                {
                    "url": url,
                    "status": "blocked_or_failed",
                    "error": str(exc),
                    "checked_at": datetime.datetime.now(
                        datetime.timezone.utc
                    ).isoformat(),
                }
            )
    catalog["discovery"] = results
    atomic_json(MANIFEST, catalog)
    write_sources()
    return results


def confirmation_url(text, file_id):
    """Accept only Google's download form for the same requested public file."""

    class Form(HTMLParser):
        def __init__(self):
            super().__init__()
            self.active = False
            self.action = None
            self.fields = {}

        def handle_starttag(self, tag, attrs):
            attrs = dict(attrs)
            if tag == "form" and attrs.get("id") == "download-form":
                self.active = True
                self.action = attrs.get("action")
            if self.active and tag == "input" and attrs.get("name"):
                self.fields[attrs["name"]] = attrs.get("value", "")

        def handle_endtag(self, tag):
            if tag == "form":
                self.active = False

    form = Form()
    form.feed(text)
    if (
        form.action != "https://drive.usercontent.google.com/download"
        or form.fields.get("id") != file_id
        or form.fields.get("confirm") != "t"
    ):
        raise ValueError("Not a valid same-file Google download confirmation")
    return form.action + "?" + urllib.parse.urlencode(form.fields)


def download(source, refresh=False):
    current = source.get("sha256")
    old = DATA / "raw" / f"{current}.pdf"
    if current and old.exists() and not (refresh or source.get("remote_changed")):
        if sha256(old) != current:
            raise ValueError("Immutable original checksum mismatch")
        return old
    target = DATA / "downloads" / f"{source['id']}.partial"
    target.parent.mkdir(parents=True, exist_ok=True)
    try:
        with open_url(source["download_url"], 180) as response, target.open("wb") as f:
            source["download_final_url"] = response.geturl()
            shutil.copyfileobj(response, f, 1024 * 1024)
        with target.open("rb") as check:
            signature = check.read(5)
        if signature != b"%PDF-" and source.get("drive_id"):
            with target.open("rb") as check:
                form = check.read(1024 * 1024).decode("utf-8", "replace")
            url = confirmation_url(form, source["drive_id"])
            with open_url(url, 300) as response, target.open("wb") as f:
                # Do not persist the ephemeral confirmation token.
                shutil.copyfileobj(response, f, 1024 * 1024)
            with target.open("rb") as check:
                signature = check.read(5)
        if signature != b"%PDF-":
            raise ValueError(
                "Response is not PDF (Google confirmation, quota or authentication may be required)"
            )
        expected = source.get("expected_bytes")
        if expected and target.stat().st_size != expected:
            raise ValueError("Downloaded size differs from listed metadata")
        import pymupdf

        with pymupdf.open(target) as doc:
            if doc.needs_pass:
                raise ValueError("Encrypted PDF needs a password")
            count = len(doc)
        digest = sha256(target)
        final = DATA / "raw" / f"{digest}.pdf"
        final.parent.mkdir(parents=True, exist_ok=True)
        if final.exists():
            if sha256(final) != digest:
                raise ValueError("Existing original is corrupted")
            target.unlink()
        else:
            target.replace(final)
            final.chmod(0o444)
        if current and current != digest:
            source.setdefault("previous_hashes", []).append(current)
        source.update(
            {
                "sha256": digest,
                "bytes": final.stat().st_size,
                "pages": count,
                "status": "downloaded",
                "remote_changed": False,
            }
        )
        return final
    finally:
        if target.exists():
            target.unlink()


def write_sources():
    cat = load_sources()
    lines = [
        "# Источники Baikal LLM Wiki",
        "",
        "Google Drive — зеркало передачи. Подлинность официальной публикации указывается отдельно. Год из имени — подсказка, не подтверждение.",
        "",
        "| Документ | Год по титулу / подсказка | Тип | Ведомство | Официальный URL | Доступ / обработка | Подлинность |",
        "| --- | --- | --- | --- | --- | --- | --- |",
    ]
    for s in cat["sources"]:
        year = s.get("report_year") or f"{s.get('year_hint', '?')} (имя)"
        lines.append(
            f"| [{s['name']}]({s['url']}) | {year} | {'Общероссийский обзор с разделом о Байкале' if s.get('document_type') == 'supplementary_national_review' else 'Госдоклад о Байкале'} | {s.get('agency', '?')} | {s.get('official_url') or 'Не подтверждён'} | {s['status']} ({s.get('extracted_pages', 0)}/{s.get('pages', '?')} стр.) | {s.get('authenticity', 'unknown')} |"
        )
    lines += [
        "",
        "## Покрытие 2003–2026",
        "",
        "| Год | Наличие в коллекции |",
        "| --- | --- |",
    ]
    for y in range(2003, 2027):
        items = [
            s for s in cat["sources"] if s.get("report_year", s.get("year_hint")) == y
        ]
        lines.append(
            f"| {y} | {sum(s.get('document_type') != 'supplementary_national_review' for s in items)} госдоклад(ов) о Байкале; {sum(s.get('document_type') == 'supplementary_national_review' for s in items)} дополнительных обзоров"
            + (
                " (год требует проверки)"
                if items and any(not s.get("report_year") for s in items)
                else ""
            )
            + " |"
            if items
            else f"| {y} | Публикация не проверена; отсутствие не установлено |"
        )
    lines += ["", "## Проверка официальных каталогов", ""]
    for d in cat.get("discovery", []):
        lines.append(
            f"- {d['url']}: {d['status']}; {d.get('error', 'HTTP ответ получен')}"
        )
        for u in d.get("pdf_links", []):
            lines.append(f"  - Кандидат PDF (содержание ещё не проверено): {u}")
    lines += [
        "",
        "Статус mirror_unverified означает: титул может указывать ведомство, но соответствие официальной публикации независимо не подтверждено. Доклады за непроверенные годы не выдумываются.",
        "",
    ]
    atomic_text(ROOT / "SOURCES.md", "\n".join(lines))


def add_official(url, name, year=None):
    """Register an official-domain PDF candidate; verify its contents during ingest."""
    import hashlib

    host = urllib.parse.urlsplit(url).hostname
    if not url.startswith("https://") or host not in OFFICIAL_HOSTS:
        raise ValueError("Expected HTTPS URL on a configured official host")
    sid = "official-" + hashlib.sha256(url.encode()).hexdigest()[:20]
    cat = load_sources()
    if not any(s["id"] == sid for s in cat["sources"]):
        cat["sources"].append(
            {
                "id": sid,
                "name": name,
                "url": url,
                "download_url": url,
                "official_url": url,
                "year_hint": year,
                "agency": host,
                "authenticity": "official_domain_candidate",
                "status": "discovered",
            }
        )
        atomic_json(MANIFEST, cat)
        write_sources()
    return sid
