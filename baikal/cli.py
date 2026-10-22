"""Local workflow: discover -> download -> extract -> index -> wiki -> lint."""

import argparse, json, sys
from .core import MANIFEST, load_sources, atomic_json


def main(argv=None):
    parser = argparse.ArgumentParser(description="Baikal LLM Wiki — evidence first")
    sub = parser.add_subparsers(dest="command", required=True)
    d = sub.add_parser("discover")
    d.add_argument("--official", action="store_true")
    for name in ("ingest", "update"):
        p = sub.add_parser(name)
        p.add_argument("--source")
        p.add_argument("--workers", type=int, default=2)
        p.add_argument("--limit-pages", type=int)
        p.add_argument("--refresh", action="store_true")
        p.add_argument("--retry-errors", action="store_true")
    a = sub.add_parser("add-source")
    a.add_argument("--url", required=True)
    a.add_argument("--name", required=True)
    a.add_argument("--year", type=int)
    a = sub.add_parser("audit-tables")
    a.add_argument("--source")
    sub.add_parser("export-candidates")
    sub.add_parser("build")
    sub.add_parser("lint")
    sub.add_parser("status")
    u = sub.add_parser("propose-updates")
    u.add_argument("--source", required=True)
    u.add_argument("--pages", type=int, nargs="+")
    u.add_argument("--limit", type=int, default=20)
    c = sub.add_parser("prepare-change")
    c.add_argument("--draft", required=True)
    c.add_argument("--claims", type=int, nargs="+", required=True)
    c = sub.add_parser("apply-change")
    c.add_argument("--change", required=True)
    c.add_argument("--approval", required=True)
    q = sub.add_parser("query")
    q.add_argument("question")
    q.add_argument("--save", action="store_true")
    s = sub.add_parser("synthesize")
    s.add_argument("topic")
    s.add_argument("--assessment-protocol", choices=["v1", "v2"], default="v1")
    s.add_argument(
        "--proposal", help="ID of a local evidence proposal; draft stays unpublished"
    )
    s.add_argument(
        "--assess-update",
        action="store_true",
        help="Compare with bounded existing article context",
    )
    s.add_argument(
        "--allow-remote",
        action="store_true",
        help="Explicit consent to call configured remote LLM (may incur costs)",
    )
    s = sub.add_parser("serve")
    s.add_argument("--host", default="127.0.0.1")
    s.add_argument("--port", type=int, default=8000)
    args = parser.parse_args(argv)
    try:
        if args.command == "discover":
            from .acquire import discover_drive, discover_official

            print(json.dumps(discover_drive(), ensure_ascii=False, indent=2))
            if args.official:
                print(json.dumps(discover_official(), ensure_ascii=False, indent=2))
        elif args.command in ("ingest", "update"):
            from .acquire import download, write_sources, discover_drive
            from .extract import process

            if args.workers < 1 or args.workers > 8:
                raise ValueError("workers must be between 1 and 8")
            if args.limit_pages is not None and args.limit_pages < 1:
                raise ValueError("limit-pages must be positive")
            if args.command == "update":
                discover_drive()
            cat = load_sources()
            selected = [
                s for s in cat["sources"] if not args.source or s["id"] == args.source
            ]
            if not selected:
                raise ValueError("No matching sources; run discover first")
            failures = []
            for s in sorted(selected, key=lambda s: s.get("expected_bytes", 0)):
                try:
                    print("Processing", s["name"], flush=True)
                    download(s, refresh=args.refresh)
                    atomic_json(MANIFEST, cat)
                    result = process(
                        s,
                        workers=args.workers,
                        limit=args.limit_pages,
                        retry_errors=args.retry_errors,
                    )
                    print(json.dumps(result, ensure_ascii=False), flush=True)
                    if result["failed_pages"] or result["ocr_failures"]:
                        failures.append(s["id"])
                except Exception as exc:
                    s["status"] = "error"
                    s["error"] = str(exc)
                    failures.append(s["id"])
                    print(str(exc), file=sys.stderr)
                atomic_json(MANIFEST, cat)
                write_sources()
            rebuild()
            if failures:
                return 1
        elif args.command == "add-source":
            from .acquire import add_official

            print(add_official(args.url, args.name, args.year))
        elif args.command == "audit-tables":
            from .tableqa import audit

            print(json.dumps(audit(args.source), ensure_ascii=False, indent=2))
        elif args.command == "export-candidates":
            from .store import export_candidates

            print(export_candidates())
        elif args.command == "build":
            rebuild()
        elif args.command == "lint":
            from .wiki import lint

            r = lint()
            print(json.dumps(r, ensure_ascii=False, indent=2))
            return 1 if r["errors"] else 0
        elif args.command == "status":
            for s in load_sources()["sources"]:
                print(
                    s["name"],
                    s["status"],
                    f"{s.get('extracted_pages', 0)}/{s.get('pages', '?')}",
                )
        elif args.command == "query":
            from .wiki import query

            print(query(args.question, args.save))
        elif args.command == "propose-updates":
            from .updates import propose

            print(
                json.dumps(
                    propose(args.source, pages=args.pages, limit=args.limit),
                    ensure_ascii=False,
                    indent=2,
                )
            )
        elif args.command == "synthesize":
            from .llm import synthesize

            print(
                synthesize(
                    args.topic,
                    allow_remote=args.allow_remote,
                    proposal=args.proposal,
                    assessment=args.assess_update,
                    assessment_protocol=args.assessment_protocol,
                )
            )
        elif args.command == "prepare-change":
            from .editorial import prepare

            print(
                json.dumps(
                    prepare(args.draft, args.claims), ensure_ascii=False, indent=2
                )
            )
        elif args.command == "apply-change":
            from .editorial import apply

            print(
                json.dumps(
                    apply(args.change, args.approval), ensure_ascii=False, indent=2
                )
            )
        elif args.command == "serve":
            import uvicorn

            uvicorn.run("baikal.app:app", host=args.host, port=args.port)
        return 0
    except Exception as exc:
        print(f"Error: {exc}", file=sys.stderr)
        return 1


def rebuild():
    from .store import index_sources
    from .wiki import build, lint
    from .acquire import write_sources

    print("Index:", index_sources())
    print("Wiki:", build())
    write_sources()
    result = lint()
    print("Lint:", json.dumps(result, ensure_ascii=False))
    if result["errors"]:
        raise ValueError("Wiki lint failed")
