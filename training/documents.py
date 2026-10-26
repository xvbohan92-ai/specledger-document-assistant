"""Generate new multi-layout paired digital/scan fixtures with separately declared value oracles."""

import json
import shutil

import pdfplumber
import pypdfium2 as pdfium
from common import ROOT, read, sha
from PIL import ImageEnhance
from reportlab.lib import colors
from reportlab.lib.utils import ImageReader
from reportlab.pdfgen import canvas

VALUES = {
    "power_w": [("0.72 kW", "720"), ("850 W", "850"), ("1.2 kW", "1200"), ("900000 mW", "900")],
    "voltage_v": [
        ("24 V DC", "24", "DC"),
        ("0.23 kV AC", "230", "AC"),
        ("120 V AC", "120", "AC"),
        ("48000 mV DC", "48", "DC"),
    ],
    "current_a": [
        ("850 mA DC", "0.85", "DC"),
        ("2 A AC", "2", "AC"),
        ("1.25 A DC", "1.25", "DC"),
        ("600 mA AC", "0.6", "AC"),
    ],
    "mass_kg": [("1200 g", "1.2"), ("2.5 kg", "2.5"), ("4 lb", "1.81436948"), ("0.0001 g", "0.0000001")],
    "max_temp_c": [("176 F", "80"), ("70 C", "70"), ("158 F", "70"), ("65 C", "65")],
}


def draw(c, case, page, rows):
    wide = case["layout"] == "landscape-table"
    width, height = (842, 595) if wide else (595, 842)
    c.setFillColor(colors.HexColor("#173c43"))
    c.rect(0, height - 102, width, 102, fill=1, stroke=0)
    c.setFillColor(colors.white)
    c.setFont("Helvetica-Bold", 21)
    c.drawString(40, height - 45, "SPECLEDGER / EQUIPMENT DATA")
    c.setFont("Helvetica", 10)
    c.drawString(40, height - 73, "SELF-AUTHORED TEST FIXTURE - NOT A MANUFACTURER DOCUMENT")
    c.setFillColor(colors.HexColor("#173c43"))
    c.setFont("Helvetica-Bold", 13)
    c.drawString(
        40,
        height - 138,
        f"{case['id']} / {'Electrical attributes' if page == 1 else 'Mechanical attributes'}",
    )
    top = height - 168
    table = case["layout"] != "colon-list"
    left, total = 40, width - 80
    label_width = total * 0.72
    if table:
        rows = [("Parameter", "Value")] + rows
    for index, (label, value) in enumerate(rows):
        y = top - index * 44
        if table:
            c.setFillColor(colors.HexColor("#e9f0ed" if index == 0 else "#ffffff"))
            c.rect(left, y - 39, total, 44, fill=1, stroke=0)
            c.setStrokeColor(colors.HexColor("#b6c8c8"))
            c.rect(left, y - 39, total, 44, fill=0, stroke=1)
            c.line(left + label_width, y - 39, left + label_width, y + 5)
        c.setFillColor(colors.HexColor("#173c43"))
        c.setFont("Helvetica-Bold" if table and index == 0 else "Helvetica", 10)
        if table:
            c.drawString(left + 9, y - 22, label)
            c.drawString(left + label_width + 9, y - 22, value)
        else:
            c.drawString(left, y - 22, f"{label}: {value}")
    c.setFont("Helvetica", 9)
    c.setFillColor(colors.HexColor("#587078"))
    c.drawString(
        40, 40, "Printed values and units are authoritative; unresolved fields require human review."
    )
    c.drawRightString(width - 40, 22, f"Page {page} / 2")
    c.showPage()


def main():
    destination = ROOT / "data/finetuning-documents"
    output = ROOT / "training/datasets/documents.json"
    if output.exists() or destination.exists():
        raise ValueError("New PDF fixtures must not overwrite an existing frozen dataset")
    destination.mkdir(parents=True)
    specs = read("training/label-specs.json")
    names = [key for key in specs if key != "ignore"]
    cases = []
    for split, count in (("valid", 8), ("test", 16)):
        for i in range(count):
            case = {
                "id": f"{split}-{i:02d}",
                "split": split,
                "kind": "digital" if i % 2 == 0 else "scan",
                "layout": ("table", "colon-list", "landscape-table")[i % (2 if split == "valid" else 3)],
                "expected": {},
                "rows": [],
                "issues": [],
            }
            for n, name in enumerate(names):
                label = specs[name][split][(i + n) % len(specs[name][split])]
                page = 1 if n < 4 else 2
                if name == "model":
                    raw = f"SL-{split.upper()}-{200 + i}"
                    expected = {"value": raw, "signal": None, "page": page}
                elif name == "ip_rating":
                    raw = "IP65" if i % 2 == 0 else "IP54"
                    expected = {"value": raw, "signal": None, "page": page}
                else:
                    item = VALUES[name][(i + n) % 4]
                    raw = item[0]
                    expected = {
                        "exact_value": item[1],
                        "signal": item[2] if len(item) == 3 else None,
                        "page": page,
                    }
                if split == "test" and i == 12 and name == "power_w":
                    raw = "18 hp"
                    expected = {"error": "unsupported_unit", "page": page}
                if split == "test" and i == 13 and name == "voltage_v":
                    raw = "220-240 V AC"
                    expected = {"error": "unparsed_value_or_unit", "page": page}
                if split == "test" and i == 14 and name == "current_a":
                    expected = {"error": "conflicting_values", "page": page}
                if split == "test" and i == 15 and name == "model":
                    expected = {"error": "missing_field", "page": page}
                else:
                    case["rows"].append({"label": label, "raw_value": raw, "field": name, "page": page})
                case["expected"][name] = expected
                if "error" in expected:
                    case["issues"].append([name, expected["error"]])
            if split == "test" and i == 14:
                original = next(r for r in case["rows"] if r["field"] == "current_a")
                case["rows"].append({**original, "raw_value": "2 A DC", "page": 2})
            negatives = specs["ignore"][split]
            negative_values = ["12 V DC", "4 A DC", "12 kg", "95 C", "-10 C", "SN-TEST-991"]
            for k in range(4):
                j = (i + k) % len(negatives)
                case["rows"].append(
                    {
                        "label": negatives[j],
                        "raw_value": negative_values[j],
                        "field": "ignore",
                        "page": 1 if k < 2 else 2,
                    }
                )
            path = destination / f"{case['id']}.pdf"
            native = path if case["kind"] == "digital" else destination / f"{case['id']}-native.pdf"
            size = (842, 595) if case["layout"] == "landscape-table" else (595, 842)
            c = canvas.Canvas(str(native), pagesize=size, invariant=1, pageCompression=1)
            c.setTitle("SpecLedger self-authored held-out field mapping fixture")
            for page in (1, 2):
                draw(c, case, page, [(r["label"], r["raw_value"]) for r in case["rows"] if r["page"] == page])
            c.save()
            if case["kind"] == "scan":
                doc = pdfium.PdfDocument(native)
                c = canvas.Canvas(str(path), pagesize=size, invariant=1, pageCompression=1)
                for page in doc:
                    image = page.render(scale=2.2).to_pil().convert("RGB")
                    if split == "test" and i == 11:
                        image = ImageEnhance.Contrast(image).enhance(0.8)
                        image = image.rotate(0.35, resample=3, expand=False, fillcolor="white")
                    c.drawImage(ImageReader(image), 0, 0, width=size[0], height=size[1])
                    c.showPage()
                c.save()
                doc.close()
                native.unlink()
                with pdfplumber.open(path) as pdf:
                    assert not any(page.extract_text() for page in pdf.pages), (
                        "A scan must have no text layer"
                    )
            case["path"] = str(path.relative_to(ROOT))
            case["sha256"] = sha(path)
            cases.append(case)
    manifest = {
        "cases": cases,
        "documents": 24,
        "pages": 48,
        "test_field_slots": 112,
        "builder_sha256": sha(__file__),
        "labels_sha256": sha(ROOT / "training/label-specs.json"),
        "scope": "24 same-author synthetic 2-page PDFs, three simple layouts. 8 validation, 16 test; "
        "each balanced digital/scanned. Labels repeat across documents and vocabulary overlaps. "
        "One mildly degraded scan and four semantic/unit/missing-field problem documents. "
        "This is not an independent manufacturer-document or general OCR benchmark.",
    }
    output.write_text(json.dumps(manifest, indent=2) + "\n")
    final = ROOT / "output/pdf"
    final.mkdir(parents=True, exist_ok=True)
    shutil.copyfile(destination / "test-02.pdf", final / "specledger-heldout-sample.pdf")
    previews = ROOT / "docs/finetuning-fixtures"
    previews.mkdir(exist_ok=True)
    for number in (0, 1, 2, 11):
        path = destination / f"test-{number:02d}.pdf"
        doc = pdfium.PdfDocument(path)
        for page in range(2):
            doc[page].render(scale=1.2).to_pil().save(previews / f"test-{number:02d}-p{page + 1}.png")
        doc.close()
    print(json.dumps({"documents": 24, "pages": 48, "test_slots": 112, "sample": str(final)}))


if __name__ == "__main__":
    main()
