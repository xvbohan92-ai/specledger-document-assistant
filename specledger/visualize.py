"""Portfolio figures generated only from saved evaluation records."""

import csv
import json
import math
import os

from .models import ProjectError
from .parsing import digest


def export_figures(report_path, output):
    report = json.loads(report_path.read_text())
    for mode, value in report["summary"].items():
        rows = [r for r in report["records"] if r["pipeline"] == mode]
        if (
            len(rows) != value["documents"]
            or sum(r["field_correct"] for r in rows) != value["field_correct"]
            or sum(r["field_total"] for r in rows) != value["field_total"]
            or not math.isclose(value["field_accuracy"], value["field_correct"] / value["field_total"])
        ):
            raise ProjectError("汇总指标与逐文档记录不一致。")
    output.mkdir(parents=True, exist_ok=True)
    os.environ.setdefault("MPLCONFIGDIR", str(output.parent / ".mpl-cache"))
    os.environ.setdefault("XDG_CACHE_HOME", str(output.parent / ".mpl-cache"))
    import matplotlib

    matplotlib.use("Agg")
    import matplotlib.pyplot as plt
    import numpy as np

    plt.rcParams.update(
        {
            "font.family": "DejaVu Sans",
            "font.size": 10,
            "figure.facecolor": "#f6f7f2",
            "axes.facecolor": "white",
            "text.color": "#213c44",
            "axes.labelcolor": "#6c8176",
            "xtick.color": "#7d8e7d",
            "ytick.color": "#7d8e7d",
            "axes.spines.top": False,
            "axes.spines.right": False,
            "axes.edgecolor": "#dce3d7",
            "grid.color": "#e8eee1",
            "axes.axisbelow": True,
            "svg.fonttype": "path",
        }
    )
    fig = plt.figure(figsize=(14, 9.2))
    fig.text(0.07, 0.955, "SPECLEDGER / MEASURED DOCUMENT PIPELINE", size=10, weight="bold", color="#668a73")
    fig.text(0.07, 0.905, "From PDF evidence to validated records", size=23, weight="bold")
    result = report["summary"]["docling"]
    fig.text(
        0.07,
        0.867,
        f"{result['documents']} {report['split']} PDFs | {result['pages']} pages | "
        f"{result['field_total']} labeled fields | English equipment specification fixtures",
        color="#7d8e7d",
    )
    grid = fig.add_gridspec(2, 2, left=0.09, right=0.96, bottom=0.18, top=0.77, hspace=0.60, wspace=0.35)
    ax = fig.add_subplot(grid[0, 0])
    x = np.arange(3)
    width = 0.32
    for offset, mode, label, color in [
        (-width / 2, "text", "PDF text + rules", "#b7c6b6"),
        (width / 2, "docling", "Docling OCR + rules", "#3e6c58"),
    ]:
        value = report["summary"][mode]
        values = [value[key] * 100 for key in ("field_accuracy", "digital_accuracy", "scan_accuracy")]
        bars = ax.bar(x + offset, values, width, label=label, color=color)
        for bar, v in zip(bars, values, strict=True):
            ax.text(bar.get_x() + bar.get_width() / 2, v + 3, f"{v:.0f}%", ha="center", size=9)
    ax.set_xticks(x, ["All fields", "Digital PDFs", "Scanned PDFs"])
    ax.set_ylim(0, 135)
    ax.set_yticks([0, 25, 50, 75, 100])
    ax.set_ylabel("Canonical value / state accuracy (%)")
    ax.legend(frameon=False, loc="upper left", fontsize=8, ncol=2)
    ax.grid(axis="y")
    ax.set_title("A  Extraction and validation", loc="left", weight="bold", pad=16)
    ax = fig.add_subplot(grid[0, 1])
    modes = ["text", "docling"]
    values = [report["summary"][m]["p50_ms"] for m in modes]
    bars = ax.bar(["PDF text", "Docling OCR"], values, 0.5, color=["#b7c6b6", "#3e6c58"])
    for bar, v in zip(bars, values, strict=True):
        ax.text(bar.get_x() + bar.get_width() / 2, v + max(values) * 0.05, f"{v:,.1f} ms", ha="center")
    ax.set_ylim(0, max(values) * 1.4)
    ax.set_ylabel("Warm document P50 (ms)")
    ax.grid(axis="y")
    ax.set_title("B  Actual processing cost", loc="left", weight="bold", pad=16)
    ax = fig.add_subplot(grid[1, 0])
    values = [result["naive_numeric_correct"], result["normalized_numeric_correct"]]
    bars = ax.bar(["First number only", "Unit normalization"], values, 0.5, color=["#b7c6b6", "#3e6c58"])
    for bar, v in zip(bars, values, strict=True):
        ax.text(bar.get_x() + bar.get_width() / 2, v + 1, f"{v}/{result['numeric_total']}", ha="center")
    ax.set_ylim(0, result["numeric_total"] * 1.3)
    ax.set_ylabel("Correct non-null numeric fields")
    ax.grid(axis="y")
    ax.set_title("C  Unit-conversion ablation (same OCR)", loc="left", weight="bold", pad=16)
    ax = fig.add_subplot(grid[1, 1])
    ax.axis("off")
    ax.text(0, 0.98, "D  Review gate and scope", weight="bold", size=12)
    lines = [
        f"Invalid documents incorrectly passed: {result['invalid_passed']}/{result['invalid_documents']}",
        f"Clean documents incorrectly blocked: {result['clean_blocked']}/{result['clean_documents']}",
        "Unknown units, ranges, missing and conflicting values.",
        "4 development PDFs; 8 test PDFs; one shared layout.",
        "Same-author labels; no independent manufacturer data.",
    ]
    for i, line in enumerate(lines):
        ax.text(0, 0.75 - i * 0.18, line, size=10, color="#758b76")
    fig.text(
        0.09,
        0.095,
        "Actual local Docling + RapidOCR inference. "
        "Latency excludes initialization, includes PDF parsing and rules, "
        "and disables parsed-result caching.",
        size=9,
        color="#7c8f7d",
    )
    fig.text(
        0.09,
        0.06,
        "Field scores include unresolved-value states; they are not OCR character accuracy. "
        "Controlled fixtures do not establish general real-world accuracy.",
        size=9,
        color="#7c8f7d",
    )
    for suffix in ("png", "svg"):
        fig.savefig(
            output / f"document-evaluation.{suffix}", dpi=180, bbox_inches="tight", facecolor="#f6f7f2"
        )
    plt.close(fig)
    source = {
        "summary": report["summary"],
        "records": report["records"],
        "scope": report["scope"],
        "backend": report["backend"],
        "report_sha256": digest(report_path),
        "limits": report["limits"],
    }
    (output / "source-values.json").write_text(json.dumps(source, ensure_ascii=False, indent=2))
    with (output / "per-document.csv").open("w", newline="") as file:
        keys = [
            "case_id",
            "pipeline",
            "kind",
            "field_correct",
            "field_total",
            "page_correct",
            "latency_ms",
            "error",
        ]
        writer = csv.DictWriter(file, fieldnames=keys)
        writer.writeheader()
        writer.writerows({key: row[key] for key in keys} for row in report["records"])
    return {
        "output": str(output.resolve()),
        "field_correct": result["field_correct"],
        "field_total": result["field_total"],
    }
