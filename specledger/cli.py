import argparse
import json
from pathlib import Path

from .config import Settings
from .models import ProjectError


def main():
    parser = argparse.ArgumentParser(description="SpecLedger PDF-to-database assistant")
    sub = parser.add_subparsers(dest="command", required=True)
    demo = sub.add_parser("demo")
    demo.add_argument(
        "--ingest", action="store_true", help="Import the four dev examples, without approving them"
    )
    sub.add_parser("prepare-models")
    ingest = sub.add_parser("ingest")
    ingest.add_argument("pdf", type=Path)
    ingest.add_argument("--source", required=True)
    reparse = sub.add_parser("reparse", help="Re-extract an existing PDF; retain history and require review")
    reparse.add_argument("document_id")
    reparse.add_argument("--revision", type=int, required=True)
    reparse.add_argument("--reason", required=True)
    serve = sub.add_parser("serve")
    serve.add_argument("--port", type=int, default=8767)
    bench = sub.add_parser("evaluate")
    bench.add_argument("--split", choices=("dev", "test"), default="test")
    bench.add_argument("--output", type=Path, default=Path("reports/evaluation.json"))
    sub.add_parser("visualize")
    args = parser.parse_args()
    settings = Settings.from_env()
    try:
        if args.command == "prepare-models":
            from .parsing import prepare_models

            manifest = prepare_models(settings.data)
            output = {"models": manifest["models"], "verified_files": len(manifest["files"])}
        elif args.command == "demo":
            from .demo import create_demo

            manifest = create_demo(settings.data)
            output = {
                "documents": len(manifest["cases"]),
                "pages": len(manifest["cases"]) * 2,
                "scope": manifest["scope"],
            }
            if args.ingest:
                from .parsing import Parser
                from .store import Ledger

                ledger = Ledger(settings, Parser(settings))
                for case in manifest["cases"]:
                    if case["split"] == "dev":
                        ledger.ingest(
                            settings.data / case["path"],
                            source="Self-authored demonstration; no real manufacturer",
                        )
                output["ingested"] = len(ledger.list_documents())
        elif args.command == "serve":
            import uvicorn

            from .api import create_app

            uvicorn.run(create_app(settings), host="127.0.0.1", port=args.port)
            return
        elif args.command == "reparse":
            from .parsing import Parser
            from .store import Ledger

            doc = Ledger(settings, Parser(settings)).reparse(args.document_id, args.revision, args.reason)
            output = {
                "id": doc["id"],
                "revision": doc["revision"],
                "status": doc["status"],
                "history_versions": len(doc["versions"]),
            }
        elif args.command == "evaluate":
            from .evaluation import evaluate

            report = evaluate(settings, split=args.split, output=args.output)
            output = {
                "summary": report["summary"],
                "report": str(args.output.resolve()),
                "limits": report["limits"],
            }
        elif args.command == "visualize":
            from .visualize import export_figures

            output = export_figures(Path("reports/evaluation.json"), Path("docs/figures"))
        else:
            from .parsing import Parser
            from .store import Ledger

            result = Ledger(settings, Parser(settings)).ingest(args.pdf, source=args.source)
            output = {
                "id": result["document"]["id"],
                "status": result["document"]["status"],
                "issues": result["document"]["issues"],
                "duplicate": result["duplicate"],
            }
        print(json.dumps(output, ensure_ascii=False, indent=2))
    except (ProjectError, OSError, ValueError) as exc:
        parser.exit(1, f"Error: {exc}\n")


if __name__ == "__main__":
    main()
