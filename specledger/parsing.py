"""Actual PDF parsers with page coordinates and explicit model provisioning."""

import hashlib
import importlib.metadata
import json
import os
import tempfile
import threading
import time
from pathlib import Path

import pdfplumber

from .config import PIPELINE_VERSION
from .models import EvidenceLine, ParsedPage, ProjectError


def digest(path):
    result = hashlib.sha256()
    with path.open("rb") as file:
        for chunk in iter(lambda: file.read(1024 * 1024), b""):
            result.update(chunk)
    return result.hexdigest()


def prepare_models(data):
    os.environ.setdefault("HF_HUB_DISABLE_XET", "1")
    from docling.models.stages.ocr.rapid_ocr_model import RapidOcrModel
    from huggingface_hub import HfApi, snapshot_download

    folder = data / "models"
    folder.mkdir(parents=True, exist_ok=True)
    models = []
    for repo, revision, patterns in [
        ("docling-project/docling-layout-heron", "main", ["*.json", "*.safetensors", "*.bin"]),
        ("docling-project/docling-models", "v2.3.0", ["model_artifacts/tableformer/fast/*"]),
    ]:
        sha = HfApi().model_info(repo, revision=revision).sha
        snapshot_download(
            repo,
            revision=sha,
            local_dir=folder / repo.replace("/", "--"),
            allow_patterns=patterns,
            cache_dir=data / "cache" / "huggingface",
        )
        models.append({"repo": repo, "revision": sha})
    RapidOcrModel.download_models(
        backend="onnxruntime", lang="en", model_size="small", local_dir=folder / "RapidOcr"
    )
    files = {
        str(path.relative_to(folder)): digest(path)
        for path in folder.rglob("*")
        if path.is_file() and ".cache" not in path.parts and path.name != "manifest.json"
    }
    manifest = {
        "models": models,
        "files": files,
        "docling": importlib.metadata.version("docling"),
        "rapidocr": importlib.metadata.version("rapidocr"),
        "ocr_backend": "onnxruntime",
        "language": "en",
        "device": "cpu",
        "tableformer": "fast",
    }
    (folder / "manifest.json").write_text(json.dumps(manifest, indent=2))
    return manifest


def normalized_box(box, width, height):
    left, top, right, bottom = box
    return [
        max(0, min(1, value / size))
        for value, size in zip((left, top, right, bottom), (width, height, width, height), strict=True)
    ]


def preflight(path):
    if path.stat().st_size > 32 * 1024 * 1024:
        raise ProjectError("PDF 大小不能超过 32MB。")
    with path.open("rb") as file:
        if file.read(5) != b"%PDF-":
            raise ProjectError("输入不是有效的 PDF 文件。")
    try:
        with pdfplumber.open(path) as document:
            if not 1 <= len(document.pages) <= 20:
                raise ProjectError("文档限 1–20 页。")
            sizes = [(float(page.width), float(page.height)) for page in document.pages]
            if any(w <= 0 or h <= 0 or w * h > 4_000_000 for w, h in sizes):
                raise ProjectError("页面尺寸超出处理范围。")
            return sizes
    except ProjectError:
        raise
    except Exception as exc:
        raise ProjectError("PDF 无法读取或需要密码。") from exc


def parse_text(path):
    pages = []
    with pdfplumber.open(path) as document:
        for number, page in enumerate(document.pages, 1):
            lines = []
            for table in page.find_tables():
                for row, cells in zip(table.rows, table.extract(), strict=True):
                    text = " | ".join(cell or "" for cell in cells)
                    lines.append(
                        EvidenceLine(
                            text=text,
                            page=number,
                            bbox=normalized_box(row.bbox, page.width, page.height),
                            precision="line",
                        )
                    )
            words = page.extract_words()
            grouped = []
            for word in sorted(words, key=lambda w: (round(w["top"] / 4), w["x0"])):
                if not grouped or abs(grouped[-1][0]["top"] - word["top"]) > 4:
                    grouped.append([])
                grouped[-1].append(word)
            for row in grouped:
                text = " ".join(w["text"] for w in row)
                box = (
                    min(w["x0"] for w in row),
                    min(w["top"] for w in row),
                    max(w["x1"] for w in row),
                    max(w["bottom"] for w in row),
                )
                lines.append(
                    EvidenceLine(text=text, page=number, bbox=normalized_box(box, page.width, page.height))
                )
            pages.append(ParsedPage(number=number, width=page.width, height=page.height, lines=lines))
    return pages


class Parser:
    def __init__(self, settings):
        self.settings = settings
        self.lock = threading.Lock()
        self.converter = None
        self.manifest = None
        self.startup_ms = 0.0
        if settings.backend == "docling":
            path = settings.data / "models" / "manifest.json"
            if not path.exists():
                raise ProjectError("Docling 模型未准备，请先运行 specledger prepare-models。")
            self.manifest = json.loads(path.read_text())
            for relative, sha in self.manifest["files"].items():
                target = (path.parent / relative).resolve()
                if (
                    not target.is_relative_to(path.parent.resolve())
                    or not target.is_file()
                    or digest(target) != sha
                ):
                    raise ProjectError("本地模型缺失或指纹变化，请重新准备模型。")
            if self.manifest["docling"] != importlib.metadata.version("docling"):
                raise ProjectError("Docling 版本变化，请重新准备模型以更新清单。")
        elif settings.backend != "text":
            raise ProjectError("后端只支持 docling 或 text。")

    def describe(self):
        return {
            "kind": self.settings.backend,
            "pipeline_version": PIPELINE_VERSION,
            "model_manifest": hashlib.sha256(json.dumps(self.manifest, sort_keys=True).encode()).hexdigest()
            if self.manifest
            else None,
            "models": self.manifest["models"] if self.manifest else [],
            "ocr": self.manifest is not None,
            "note": "Docling + RapidOCR 实际本地推理，英文设备表"
            if self.manifest
            else "pdfplumber 文本基线，不执行 OCR",
        }

    def _initialize(self):
        if self.converter is not None:
            return
        started = time.perf_counter()
        os.environ.setdefault("HF_HUB_OFFLINE", "1")
        os.environ.setdefault("TRANSFORMERS_OFFLINE", "1")
        from docling.datamodel.accelerator_options import AcceleratorOptions
        from docling.datamodel.base_models import InputFormat
        from docling.datamodel.pipeline_options import (
            PdfPipelineOptions,
            RapidOcrOptions,
            TableFormerMode,
            TableStructureOptions,
        )
        from docling.document_converter import DocumentConverter, PdfFormatOption

        options = PdfPipelineOptions(
            artifacts_path=self.settings.data / "models",
            do_ocr=True,
            do_table_structure=True,
            accelerator_options=AcceleratorOptions(device="cpu", num_threads=2),
            ocr_options=RapidOcrOptions(lang=["en"], backend="onnxruntime", model_size="small"),
            table_structure_options=TableStructureOptions(mode=TableFormerMode.FAST, do_cell_matching=True),
            document_timeout=180,
        )
        self.converter = DocumentConverter(
            format_options={InputFormat.PDF: PdfFormatOption(pipeline_options=options)}
        )
        self.converter.initialize_pipeline(InputFormat.PDF)
        self.startup_ms = round((time.perf_counter() - started) * 1000, 2)

    def _docling(self, path, sizes):
        self._initialize()
        from docling_core.types.doc import TableItem

        result = self.converter.convert(path, max_num_pages=20, max_file_size=32 * 1024 * 1024)
        if result.status.value != "success":
            raise ProjectError(f"文档未完整解析：{result.status.value}")
        document = result.document
        pages = {i: ParsedPage(number=i, width=w, height=h, lines=[]) for i, (w, h) in enumerate(sizes, 1)}
        for item, _ in document.iterate_items():
            if not getattr(item, "prov", None):
                continue
            if isinstance(item, TableItem) and len(item.prov) > 1:
                raise ProjectError("暂不支持合并后的跨页表格，无法可靠绑定每行页码；请拆分表格后导入。")
            for provenance in item.prov:
                page = pages[provenance.page_no]
                box = provenance.bbox.to_top_left_origin(page.height)
                bbox = normalized_box((box.l, box.t, box.r, box.b), page.width, page.height)
                if isinstance(item, TableItem):
                    for index in range(item.data.num_rows):
                        cells = sorted(
                            [c for c in item.data.table_cells if c.start_row_offset_idx == index],
                            key=lambda c: c.start_col_offset_idx,
                        )
                        text = " | ".join(c.text for c in cells)
                        cell_boxes = [c.bbox.to_top_left_origin(page.height) for c in cells if c.bbox]
                        row_box = bbox
                        if cell_boxes:
                            row_box = normalized_box(
                                (
                                    min(b.l for b in cell_boxes),
                                    min(b.t for b in cell_boxes),
                                    max(b.r for b in cell_boxes),
                                    max(b.b for b in cell_boxes),
                                ),
                                page.width,
                                page.height,
                            )
                        page.lines.append(
                            EvidenceLine(
                                text=text,
                                page=page.number,
                                bbox=row_box,
                                precision="cell" if cell_boxes else "table",
                            )
                        )
                elif getattr(item, "text", ""):
                    page.lines.append(EvidenceLine(text=item.text, page=page.number, bbox=bbox))
        return list(pages.values())

    def parse(self, path, *, cached=True):
        sizes = preflight(path)
        sha = digest(path)
        key = hashlib.sha256((sha + json.dumps(self.describe(), sort_keys=True)).encode()).hexdigest()
        cache = self.settings.data / "cache" / "parsed" / f"{key}.json"
        if cached and cache.exists():
            try:
                loaded = [ParsedPage.model_validate(p) for p in json.loads(cache.read_text())]
                if len(loaded) == len(sizes) and [p.number for p in loaded] == list(range(1, len(sizes) + 1)):
                    return loaded, True
            except (ValueError, TypeError, OSError):
                pass
        with self.lock:
            pages = parse_text(path) if self.settings.backend == "text" else self._docling(path, sizes)
        if cached:
            cache.parent.mkdir(parents=True, exist_ok=True)
            with tempfile.NamedTemporaryFile(mode="w", dir=cache.parent, suffix=".tmp", delete=False) as file:
                temporary = Path(file.name)
                json.dump([p.model_dump() for p in pages], file, ensure_ascii=False)
            try:
                temporary.replace(cache)
            finally:
                temporary.unlink(missing_ok=True)
        return pages, False
