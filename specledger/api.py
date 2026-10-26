import json
import tempfile
import threading
from contextlib import asynccontextmanager
from pathlib import Path
from typing import Annotated

import pypdfium2 as pdfium
from fastapi import FastAPI, File, Form, HTTPException, UploadFile
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles
from starlette.concurrency import run_in_threadpool

from .config import Settings
from .fields import SCHEMA
from .models import Approval, Correction, ProjectError, QueryRequest, ReparseRequest
from .parsing import Parser, digest
from .store import Ledger

STATIC = Path(__file__).parent / "static"
PREVIEW_LOCK = threading.Lock()


def create_app(settings=None, *, ledger=None):
    settings = settings or Settings.from_env()
    state = {"ledger": ledger}

    @asynccontextmanager
    async def lifespan(app):
        if state["ledger"] is None:
            state["ledger"] = Ledger(settings, Parser(settings))
        yield

    app = FastAPI(title="SpecLedger", version="0.3.0", lifespan=lifespan)

    @app.exception_handler(ProjectError)
    async def project_error(request, exc):
        from fastapi.responses import JSONResponse

        return JSONResponse({"detail": str(exc)}, status_code=400)

    @app.middleware("http")
    async def headers(request, call_next):
        response = await call_next(request)
        response.headers["X-Content-Type-Options"] = "nosniff"
        response.headers["Content-Security-Policy"] = (
            "default-src 'self'; script-src 'self'; style-src 'self'; img-src 'self' data:; "
            "connect-src 'self'; object-src 'none'; base-uri 'none'; frame-ancestors 'self'"
        )
        response.headers["Referrer-Policy"] = "no-referrer"
        return response

    def public_document(document):
        return {key: value for key, value in document.items() if key != "pdf_path"}

    @app.get("/api/meta")
    def meta():
        ledger = state["ledger"]
        documents = ledger.list_documents()
        return {
            "backend": ledger.describe_backend(),
            "schema": SCHEMA,
            "documents": len(documents),
            "approved": sum(d["status"] == "approved" for d in documents),
            "needs_review": sum(d["status"] == "needs_review" for d in documents),
            "pages": sum(d["page_count"] for d in documents),
        }

    @app.get("/api/documents")
    def documents():
        return state["ledger"].list_documents()

    @app.get("/api/documents/{document_id}")
    def document(document_id: str):
        return public_document(state["ledger"].get(document_id))

    @app.get("/api/documents/{document_id}/pages/{number}")
    def preview(document_id: str, number: int):
        doc = state["ledger"].get(document_id)
        if not 1 <= number <= len(doc["pages"]):
            raise HTTPException(404, "页码超出范围。")
        image_path = settings.data / "cache" / "previews" / f"{doc['sha256']}-{number}.png"
        image_path.parent.mkdir(parents=True, exist_ok=True)
        with PREVIEW_LOCK:
            if not image_path.exists():
                pdf = pdfium.PdfDocument(settings.data / doc["pdf_path"])
                try:
                    pdf[number - 1].render(scale=1.6).to_pil().save(image_path)
                finally:
                    pdf.close()
        return FileResponse(image_path, media_type="image/png")

    @app.get("/api/documents/{document_id}/pdf")
    def pdf(document_id: str):
        doc = state["ledger"].get(document_id)
        return FileResponse(
            settings.data / doc["pdf_path"], media_type="application/pdf", filename=doc["filename"]
        )

    @app.post("/api/upload")
    async def upload(file: Annotated[UploadFile, File()], source: Annotated[str, Form()]):
        if not (file.filename or "").lower().endswith(".pdf"):
            raise HTTPException(400, "请上传 PDF 文件。")
        settings.data.mkdir(parents=True, exist_ok=True)
        try:
            with tempfile.TemporaryDirectory(prefix=".upload-", dir=settings.data) as temporary:
                path = Path(temporary) / "input.pdf"
                size = 0
                with path.open("wb") as handle:
                    while chunk := await file.read(1024 * 1024):
                        size += len(chunk)
                        if size > 32 * 1024 * 1024:
                            raise HTTPException(413, "PDF 大小不能超过 32MB。")
                        handle.write(chunk)
                result = await run_in_threadpool(
                    state["ledger"].ingest, path, filename=file.filename, source=source
                )
                return {**result, "document": public_document(result["document"])}
        finally:
            await file.close()

    @app.get("/api/examples")
    def examples():
        path = settings.data / "demo" / "manifest.json"
        if not path.exists():
            return []
        manifest = json.loads(path.read_text())
        return [{"id": c["id"], "kind": c["kind"]} for c in manifest["cases"] if c["split"] == "dev"]

    @app.post("/api/examples/{case_id}")
    def ingest_example(case_id: str):
        manifest_path = settings.data / "demo" / "manifest.json"
        if not manifest_path.exists():
            raise HTTPException(404, "请先运行 specledger demo 生成演示文档。")
        manifest = json.loads(manifest_path.read_text())
        case = next((c for c in manifest["cases"] if c["id"] == case_id and c["split"] == "dev"), None)
        if not case:
            raise HTTPException(404, "演示文档不存在。")
        path = settings.data / case["path"]
        if digest(path) != case["sha256"]:
            raise ProjectError("演示文档指纹变化，不能使用旧标注。")
        result = state["ledger"].ingest(
            path, source="Self-authored equipment demonstration; not a manufacturer source"
        )
        return {**result, "document": public_document(result["document"])}

    @app.patch("/api/documents/{document_id}/fields/{field}")
    def correct(document_id: str, field: str, correction: Correction):
        return public_document(state["ledger"].correct(document_id, field, correction))

    @app.post("/api/documents/{document_id}/approve")
    def approve(document_id: str, request: Approval):
        return public_document(state["ledger"].approve(document_id, request.revision))

    @app.post("/api/documents/{document_id}/reparse")
    def reparse(document_id: str, request: ReparseRequest):
        return public_document(state["ledger"].reparse(document_id, request.revision, request.reason))

    @app.get("/api/documents/{document_id}/versions/{revision}")
    def version(document_id: str, revision: int):
        return public_document(state["ledger"].get_version(document_id, revision))

    @app.post("/api/query")
    def query(request: QueryRequest):
        return state["ledger"].query(request)

    @app.get("/api/evaluation")
    def evaluation():
        path = settings.data.parent / "reports" / "evaluation.json"
        if not path.exists():
            return {"available": False}
        try:
            report = json.loads(path.read_text())
            if report["backend"] != state["ledger"].describe_backend():
                return {"available": False, "reason": "评测后端或模型版本已变化。"}
            if report["manifest_sha256"] != digest(settings.data / "demo" / "manifest.json"):
                return {"available": False, "reason": "测试集版本已变化。"}
            manifest = json.loads((settings.data / "demo" / "manifest.json").read_text())
            if any(digest(settings.data / c["path"]) != c["sha256"] for c in manifest["cases"]):
                return {"available": False, "reason": "测试 PDF 指纹已变化。"}
            return {"available": True, **report}
        except (ValueError, KeyError, OSError):
            return {"available": False, "reason": "评测文件无法读取。"}

    @app.get("/")
    def index():
        return FileResponse(STATIC / "index.html")

    @app.get("/api/finetuning")
    def finetuning():
        from .training_evidence import evidence

        return evidence(state["ledger"].describe_backend())

    @app.get("/api/finetuning-figure")
    def finetuning_figure():
        from .config import ROOT
        from .training_evidence import evidence

        path = ROOT / "docs/figures/field-finetuning-evaluation.png"
        if not evidence(state["ledger"].describe_backend())["available"] or not path.is_file():
            raise HTTPException(404, "尚无匹配的固定实验图表。")
        return FileResponse(path, media_type="image/png", headers={"Cache-Control": "no-store"})

    app.mount("/static", StaticFiles(directory=STATIC), name="static")
    return app
