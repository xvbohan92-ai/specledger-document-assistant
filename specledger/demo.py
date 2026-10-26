"""Reproducible, visibly labeled PDF fixtures. Scans contain no hidden text layer."""

import json
from pathlib import Path

import pypdfium2 as pdfium
from PIL import ImageEnhance
from reportlab.lib import colors
from reportlab.lib.utils import ImageReader
from reportlab.pdfgen import canvas

from .parsing import digest

LABELS = [
    "Model",
    "Rated power",
    "Input voltage",
    "Input current",
    "Net mass",
    "Max operating temperature",
    "Ingress protection",
]


def draw_page(c, case, page, rows):
    c.setFillColor(colors.HexColor("#102e38"))
    c.rect(0, 742, 595, 100, fill=1, stroke=0)
    c.setFillColor(colors.HexColor("#9cd3c6"))
    c.setFont("Helvetica-Bold", 10)
    c.drawString(44, 800, "SPECLEDGER / CONTROLLED DOCUMENT FIXTURE")
    c.setFillColor(colors.white)
    c.setFont("Helvetica-Bold", 25)
    c.drawString(44, 766, "Equipment specification")
    c.setFillColor(colors.HexColor("#263d46"))
    c.setFont("Helvetica-Bold", 17)
    c.drawString(44, 690, f"{case['model']}  /  {'Electrical ratings' if page == 1 else 'Physical limits'}")
    c.setFillColor(colors.HexColor("#657981"))
    c.setFont("Helvetica", 10)
    c.drawString(44, 664, "Fictional equipment. Values are for software evaluation only.")
    x, top, width, row_height = 44, 610, 507, 49
    for i, (label, value) in enumerate([("Parameter", "Declared value")] + rows):
        y = top - (i + 1) * row_height
        c.setFillColor(colors.HexColor("#e8efec" if i == 0 else "#ffffff"))
        c.rect(x, y, width, row_height, fill=1, stroke=0)
        c.setStrokeColor(colors.HexColor("#b8c9c7"))
        c.rect(x, y, width, row_height, fill=0, stroke=1)
        c.line(x + 300, y, x + 300, y + row_height)
        c.setFillColor(colors.HexColor("#203b44"))
        c.setFont("Helvetica-Bold" if i == 0 else "Helvetica", 12)
        c.drawString(x + 14, y + 18, label)
        c.drawString(x + 315, y + 18, value)
    c.setFont("Helvetica", 10)
    c.setFillColor(colors.HexColor("#657981"))
    c.drawString(44, 180, "Retain the printed units. Missing and conflicting values require review.")
    c.drawString(44, 162, "This document makes no claim about a real manufacturer's equipment.")
    c.setStrokeColor(colors.HexColor("#b8c9c7"))
    c.line(44, 83, 551, 83)
    c.setFont("Helvetica", 9)
    c.drawString(44, 62, "SELF-AUTHORED DEMONSTRATION  /  ENGLISH ONLY")
    c.drawRightString(551, 62, f"Page {page} of 2")
    c.showPage()


def create_demo(data):
    folder = data / "demo"
    manifest_path = folder / "manifest.json"
    if manifest_path.exists():
        return json.loads(manifest_path.read_text())
    folder.mkdir(parents=True, exist_ok=True)
    (folder / "pdfs").mkdir(exist_ok=True)
    fixture = json.loads((Path(__file__).parent / "fixtures" / "cases.json").read_text())
    records = []
    for case in fixture["cases"]:
        destination = folder / "pdfs" / f"{case['id']}.pdf"
        native = destination if case["kind"] == "digital" else folder / f"{case['id']}-native.pdf"
        values = [case["model"]] + case["raw"]
        c = canvas.Canvas(str(native), pagesize=(595, 842), invariant=1, pageCompression=1)
        c.setTitle("Self-authored equipment specification fixture")
        draw_page(c, case, 1, list(zip(LABELS[:4], values[:4], strict=True)))
        rows = list(zip(LABELS[4:], values[4:], strict=True))
        if case.get("conflict_power"):
            rows.append(("Rated power", case["conflict_power"]))
        draw_page(c, case, 2, rows)
        c.save()
        if case["kind"] == "scan":
            document = pdfium.PdfDocument(native)
            c = canvas.Canvas(str(destination), pagesize=(595, 842), invariant=1, pageCompression=1)
            for index in range(len(document)):
                image = (
                    document[index].render(scale=2.2 if case.get("degraded") else 2.6).to_pil().convert("RGB")
                )
                if case.get("degraded"):
                    image = ImageEnhance.Contrast(image).enhance(0.7)
                    image = image.rotate(0.65, resample=3, expand=False, fillcolor="white")
                c.drawImage(ImageReader(image), 0, 0, width=595, height=842)
                c.showPage()
            c.save()
            document.close()
            native.unlink()
        records.append({**case, "path": str(destination.relative_to(data)), "sha256": digest(destination)})
    manifest = {
        "name": fixture["name"],
        "scope": fixture["scope"],
        "cases": records,
        "labels_sha256": digest(Path(__file__).parent / "fixtures" / "cases.json"),
    }
    manifest_path.write_text(json.dumps(manifest, indent=2, ensure_ascii=False))
    return manifest
