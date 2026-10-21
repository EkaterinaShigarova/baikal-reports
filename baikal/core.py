"""Shared file and evidence contracts; no credentials or service dependencies."""

from __future__ import annotations
import hashlib, json, os, re, tempfile
from pathlib import Path
from decimal import Decimal, InvalidOperation

ROOT = Path(__file__).resolve().parent.parent
DATA = ROOT / "data"
MANIFEST = ROOT / "config/sources.json"
EXTRACTOR_VERSION = "1.0"


def read_json(path, default=None):
    p = Path(path)
    return json.loads(p.read_text(encoding="utf-8")) if p.exists() else default


def atomic_json(path, value):
    atomic_text(path, json.dumps(value, ensure_ascii=False, indent=2) + "\n")


def atomic_text(path, text):
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, name = tempfile.mkstemp(dir=path.parent, prefix="." + path.name)
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as f:
            f.write(text)
        os.replace(name, path)
    finally:
        if os.path.exists(name):
            os.unlink(name)


def sha256(path):
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for b in iter(lambda: f.read(1024 * 1024), b""):
            h.update(b)
    return h.hexdigest()


def parse_number(raw):
    """Accept a single exact numeric token, never collapse ranges or qualifiers."""
    value = re.sub(r"[\s\u00a0\u202f]", "", raw).replace(",", ".")
    if not re.fullmatch(r"[+-]?\d+(?:\.\d+)?", value):
        return None
    try:
        return str(Decimal(value))
    except InvalidOperation:
        return None


def validate_observation(row):
    required = [
        "id",
        "year",
        "indicator",
        "value",
        "unit",
        "territory",
        "source_id",
        "sha256",
        "page",
        "quote",
        "method",
        "confidence",
        "review_status",
        "period",
        "comparability_group",
    ]
    errors = [f"missing {k}" for k in required if row.get(k) in (None, "")]
    if not isinstance(row.get("page"), int) or row.get("page", 0) < 1:
        errors.append("invalid page")
    if not isinstance(row.get("year"), int) or not 2003 <= row.get("year", 0) <= 2026:
        errors.append("invalid year")
    if parse_number(str(row.get("value", ""))) is None:
        errors.append("non-scalar value")
    if not re.fullmatch("[a-f0-9]{64}", row.get("sha256", "")):
        errors.append("invalid hash")
    if row.get("review_status") not in ("candidate", "reviewed", "rejected"):
        errors.append("invalid review status")
    if (
        not isinstance(row.get("confidence"), (int, float))
        or not 0 <= row.get("confidence", -1) <= 1
    ):
        errors.append("invalid confidence")
    return errors


def comparable(rows):
    if not rows or any(
        r.get("review_status") != "reviewed" or not r.get("comparability_approved")
        for r in rows
    ):
        return False
    fields = [
        "indicator",
        "unit",
        "territory",
        "period",
        "method",
        "comparability_group",
    ]
    if any(not r.get(k) for r in rows for k in fields):
        return False
    return len({tuple(r[k] for k in fields) for r in rows}) == 1


def load_sources():
    return read_json(MANIFEST, {"sources": [], "discovery": []})


def page_dir(source):
    return DATA / "processed" / source["sha256"]
