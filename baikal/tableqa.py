"""Geometry-aware table audits without repeating extraction or OCR."""

from collections import Counter
import re
import pymupdf
from .core import DATA, ROOT, load_sources, page_dir, read_json, atomic_json

VERSION = "1.0"


def numbers(text):
    return Counter(
        x.replace(",", ".") for x in re.findall(r"(?<!\w)[+-]?\d+(?:[.,]\d+)?", text)
    )


def assess_tables(page, extracted):
    tables = extracted.get("tables", [])
    issues = []
    results = []
    for i, table in enumerate(tables):
        rows = table.get("rows", [])
        width = max(map(len, rows), default=0)
        flags = []
        if any(len(r) != width for r in rows):
            flags.append("ragged_rows")
        bbox = table.get("bbox")
        missing = []
        outside = []
        if bbox:
            # Full page width reveals values in a column excluded from the detector bbox.
            band = pymupdf.Rect(0, bbox[1], page.rect.width, bbox[3])
            words = page.get_text("words", clip=band)
            overlapping = [
                t
                for t in tables
                if t.get("bbox")
                and min(t["bbox"][3], bbox[3]) > max(t["bbox"][1], bbox[1])
            ]
            raw = " ".join(w[4] for w in words)
            cells = " ".join(
                str(c or "") for t in overlapping for r in t["rows"] for c in r
            )
            missing = list((numbers(raw) - numbers(cells)).elements())
            for w in words:
                cx = (w[0] + w[2]) / 2
                cy = (w[1] + w[3]) / 2
                if not any(
                    t["bbox"][0] - 2 <= cx <= t["bbox"][2] + 2
                    and t["bbox"][1] - 2 <= cy <= t["bbox"][3] + 2
                    for t in overlapping
                ):
                    outside.append(w[4])
            if missing:
                flags.append("unaccounted_numeric_tokens")
            if len(outside) >= 2:
                flags.append("text_outside_detected_columns")
        else:
            flags.append("missing_geometry")
        if extracted.get("method") == "ocr":
            flags.append("ocr_table_requires_review")
        results.append(
            {
                "table_index": i,
                "rows": len(rows),
                "columns": width,
                "flags": flags,
                "missing_numeric_tokens": missing[:40],
                "outside_words": outside[:40],
                "status": "possible_loss" if flags else "no_loss_detected",
                "note": "Heuristic audit, not a completeness guarantee. Check original page before using values.",
            }
        )
    if not tables and re.search(r"Таблица\s+\d", extracted.get("text", ""), re.I):
        issues.append("table_caption_without_grid")
    return {
        "version": VERSION,
        "page": extracted["page"],
        "tables": results,
        "page_flags": issues,
        "requires_review": bool(issues or any(r["flags"] for r in results)),
    }


def audit(source_id=None):
    summary = []
    for s in load_sources()["sources"]:
        if source_id and s["id"] != source_id:
            continue
        if not s.get("sha256"):
            continue
        dest = page_dir(s)
        report = []
        pdf = DATA / "raw" / f"{s['sha256']}.pdf"
        if not pdf.exists():
            continue
        with pymupdf.open(pdf) as doc:
            for path in sorted((dest / "pages").glob("*.json")):
                p = read_json(path)
                if p["status"] != "ok":
                    continue
                if not p.get("tables") and not re.search(
                    r"Таблица\s+\d", p.get("text", ""), re.I
                ):
                    continue
                result = assess_tables(doc[p["page"] - 1], p)
                report.append(result)
        atomic_json(dest / "table_audit.json", report)
        summary.append(
            {
                "source_id": s["id"],
                "sha256": s["sha256"],
                "year": s.get("report_year"),
                "pages_audited": len(report),
                "pages_flagged": sum(r["requires_review"] for r in report),
                "tables_audited": sum(len(r["tables"]) for r in report),
                "tables_flagged": sum(
                    bool(t["flags"]) for r in report for t in r["tables"]
                ),
                "status": "heuristic_review_required",
            }
        )
    if source_id:
        existing = read_json(ROOT / "datasets/table_quality_summary.json", [])
        summary = [x for x in existing if x["source_id"] != source_id] + summary
    atomic_json(ROOT / "datasets/table_quality_summary.json", summary)
    return summary
