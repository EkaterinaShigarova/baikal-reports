"""Verify committed observations against locally cached page evidence, without LLMs."""

from baikal.core import ROOT, load_sources, page_dir, read_json, validate_observation


def verify():
    sources = {s["id"]: s for s in load_sources()["sources"]}
    rows = read_json(ROOT / "datasets/observations.json", [])
    errors = []
    checked = 0
    missing = 0
    for r in rows:
        errors.extend(f"{r['id']}: {e}" for e in validate_observation(r))
        source = sources.get(r["source_id"], {})
        if r["sha256"] != source.get("sha256"):
            errors.append(f"{r['id']}: stale source hash")
            continue
        page = read_json(page_dir(source) / "pages" / f"{r['page']:04d}.json")
        if not page:
            missing += 1
            continue
        checked += 1
        if r["quote"] not in page["text"]:
            errors.append(f"{r['id']}: exact quote not found on cited page")
        if r["raw_value"] not in r["quote"]:
            errors.append(f"{r['id']}: value absent from quote")
        if r["id"].startswith("emissions") and not any(
            r["raw_value"] in str(c or "")
            for t in page["tables"]
            for row in t["rows"]
            for c in row
        ):
            errors.append(f"{r['id']}: value absent from table grids")
    result = {
        "reviewed_rows": len(rows),
        "checked_against_local_pages": checked,
        "unavailable_pages": missing,
        "errors": errors,
    }
    return result


if __name__ == "__main__":
    import json

    result = verify()
    print(json.dumps(result, ensure_ascii=False, indent=2))
    raise SystemExit(bool(result["errors"] or result["unavailable_pages"]))
