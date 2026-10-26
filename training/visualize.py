"""Source-backed standalone figures separating raw SFT evidence from post-hoc scope diagnosis."""

import csv
import json

from common import ROOT, read, sha


def save(fig, name):
    for extension in ("png", "svg"):
        path = ROOT / f"docs/figures/{name}.{extension}"
        fig.savefig(path, dpi=160)
        if extension == "svg":
            path.write_text("\n".join(line.rstrip() for line in path.read_text().splitlines()) + "\n")


def main():
    import matplotlib

    matplotlib.use("Agg")
    import matplotlib.pyplot as plt
    import numpy as np

    trained = read("reports/finetuning-training.json")
    evaluated = read("reports/finetuning-evaluation.json")
    scope = read("reports/finetuning-scope-diagnostics.json")
    plt.rcParams.update(
        {
            "font.family": "DejaVu Sans",
            "font.size": 10,
            "svg.fonttype": "none",
            "axes.spines.top": False,
            "axes.spines.right": False,
        }
    )
    fig, axes = plt.subplots(2, 2, figsize=(12, 8.8))
    colors = ["#9faeb0", "#256e75"]
    for i, mode in enumerate(("base", "finetuned")):
        metrics = evaluated["components"][mode]
        xs = [j + (i - 0.5) * 0.3 for j in (0, 1)]
        values = [metrics["accuracy"], metrics["macro_f1"]]
        axes[0, 0].bar(xs, values, width=0.28, color=colors[i], label=mode)
        for x, value in zip(xs, values, strict=True):
            axes[0, 0].text(x, value + 0.025, f"{value:.3f}", ha="center")
    axes[0, 0].set(
        xticks=[0, 1],
        xticklabels=["Accuracy", "Macro F1"],
        ylim=(0, 1.08),
        ylabel="Score (0-1)",
        title="A. Frozen field-label test: 48 distinct strings",
    )
    axes[0, 0].legend(frameon=False, loc="upper left")
    values = [evaluated["summary"][m]["field_correct"] for m in ("rules", "base", "finetuned")]
    axes[0, 1].bar(range(3), values, color=["#c3cccc", *colors], width=0.55)
    for i, value in enumerate(values):
        axes[0, 1].text(i, value + 3, f"{value}/112", ha="center")
    axes[0, 1].set(
        xticks=range(3),
        xticklabels=["Rules", "Base mapper", "SFT mapper"],
        ylim=(0, 124),
        ylabel="Correct field value or unresolved state",
        title="B. Same actual OCR output: 16 new PDFs",
    )
    history = trained["history"]
    axes[1, 0].plot(
        [r["epoch"] for r in history],
        [r["validation_macro_f1"] for r in history],
        color=colors[1],
        marker="o",
        label="Full validation set",
    )
    axes[1, 0].axhline(
        trained["base_validation"]["macro_f1"], color=colors[0], linestyle="--", label="Base validation"
    )
    selected = trained["selected"]
    axes[1, 0].scatter(
        [selected["epoch"]],
        [selected["validation_macro_f1"]],
        marker="*",
        s=150,
        color="#b58b55",
        zorder=4,
        label=f"Selected epoch {selected['epoch']}",
    )
    axes[1, 0].set(
        xticks=range(1, 7),
        xlabel="Epoch (12 optimizer updates each)",
        ylabel="Validation macro F1",
        ylim=(0.55, 1.0),
        title="C. 32 validation labels select the checkpoint",
    )
    axes[1, 0].legend(frameon=False, fontsize=8, loc="lower right")
    diagnostic = [scope["summary"]["test"][m]["correct"] for m in ("base", "finetuned")]
    axes[1, 1].bar(range(2), diagnostic, color=colors, hatch="//", width=0.55)
    for i, value in enumerate(diagnostic):
        axes[1, 1].text(i, value + 3, f"{value}/112", ha="center")
    axes[1, 1].set(
        xticks=[0, 1],
        xticklabels=["Base + scope", "SFT + scope"],
        ylim=(0, 124),
        ylabel="Correct field value or unresolved state",
        title="D. Post-hoc scope diagnostic: reused test data",
    )
    for ax in axes.flat:
        ax.set_axisbelow(True)
        ax.grid(axis="y", alpha=0.15)
    fig.suptitle("SpecLedger | local field-label fine-tuning and explicit scope checks", fontsize=16, y=0.985)
    delta = (values[2] - values[1]) / 112 * 100
    fig.text(
        0.04,
        0.055,
        f"Raw SFT: +{delta:.2f} pp field accuracy. "
        "Raw models falsely accept all 6 held-out distractor labels; no raw test document is fully correct.",
        fontsize=9,
    )
    fig.text(
        0.04,
        0.031,
        "Panel D guard was added after failure analysis. It is engineering diagnosis, "
        "not fresh held-out performance or weight-training gain. Six clean PDFs remain blocked.",
        fontsize=9,
    )
    fig.text(
        0.04,
        0.008,
        "96/32/48 authored labels; 24 synthetic PDFs (8 validation, 16 test); "
        "test digital/scan 8/8. OCR frozen. No independent manufacturer data or general OCR accuracy claim.",
        fontsize=8,
        color="#50656b",
    )
    fig.tight_layout(rect=(0.015, 0.085, 0.99, 0.95), h_pad=3, w_pad=3)
    save(fig, "field-finetuning-evaluation")
    plt.close(fig)
    names = list(read("specledger/field_descriptions.json"))
    short = ["model", "power", "voltage", "current", "net mass", "max temp", "IP", "ignore"]
    fig, axes = plt.subplots(1, 2, figsize=(12, 5.8))
    for ax, mode in zip(axes, ("base", "finetuned"), strict=True):
        matrix = np.zeros((8, 8), dtype=int)
        for row in evaluated["components"][mode]["records"]:
            matrix[names.index(row["field"]), names.index(row["predicted"])] += 1
        im = ax.imshow(matrix, cmap="Greens", vmin=0, vmax=6)
        ax.set(
            xticks=range(8),
            yticks=range(8),
            xticklabels=short,
            yticklabels=short,
            xlabel="Predicted field",
            ylabel="Authored expected field",
            title=mode,
        )
        ax.tick_params(axis="x", rotation=45)
        for y in range(8):
            for x in range(8):
                ax.text(
                    x,
                    y,
                    str(matrix[y, x]),
                    ha="center",
                    va="center",
                    color="white" if matrix[y, x] > 3 else "#183e35",
                    fontsize=9,
                )
    fig.suptitle("Field-label confusion | same 48-label test, six labels per class", fontsize=15)
    fig.text(
        0.055,
        0.048,
        "Raw SFT improves positive-label mapping; neither raw model rejects the six distractors.",
        fontsize=9,
    )
    fig.text(
        0.055,
        0.016,
        "Scope rules are evaluated separately. Labels and judgements are same-author synthetic fixtures.",
        fontsize=9,
    )
    fig.subplots_adjust(left=0.08, right=0.90, top=0.86, bottom=0.28, wspace=0.40)
    colorbar = fig.add_axes((0.93, 0.25, 0.014, 0.55))
    fig.colorbar(im, cax=colorbar, label="Label count")
    save(fig, "field-label-confusion")
    plt.close(fig)
    path = ROOT / "docs/figures/field-finetuning-documents.csv"
    fields = [
        "mode",
        "id",
        "kind",
        "layout",
        "field_correct",
        "field_total",
        "invalid_ready",
        "clean_blocked",
    ]
    with path.open("w", newline="") as file:
        writer = csv.DictWriter(file, fieldnames=fields, extrasaction="ignore", lineterminator="\n")
        writer.writeheader()
        writer.writerows(evaluated["records"])
    values = {
        "training_sha256": sha(ROOT / "reports/finetuning-training.json"),
        "evaluation_sha256": sha(ROOT / "reports/finetuning-evaluation.json"),
        "scope_diagnostics_sha256": sha(ROOT / "reports/finetuning-scope-diagnostics.json"),
        "ocr_evidence_sha256": sha(ROOT / "reports/finetuning-ocr-evidence.json"),
        "csv_sha256": sha(path),
        "raw_summary": evaluated["summary"],
        "component_summary": {
            m: {k: v[k] for k in ("correct", "rows", "accuracy", "macro_f1", "distractor_false_accepts")}
            for m, v in evaluated["components"].items()
        },
        "scope_summary": scope["summary"],
        "raw_field_delta_pp": delta,
        "limits": evaluated["limitations"],
        "diagnostic_limits": scope["scope"],
    }
    (ROOT / "docs/figures/field-finetuning-values.json").write_text(json.dumps(values, indent=2) + "\n")
    failures = []
    for record in scope["records"]:
        if record["split"] == "test" and record["mode"] == "finetuned":
            for name, result in record["slots"].items():
                if not result["correct"]:
                    failures.append(
                        {
                            "document": record["id"],
                            "field": name,
                            **result,
                            "issues": [i for i in record["issues"] if i["field"] == name],
                        }
                    )
    analysis = {
        "evaluation_sha256": values["evaluation_sha256"],
        "post_hoc_scope_sha256": values["scope_diagnostics_sha256"],
        "raw_paired": {
            "improved": sum(r["delta"] > 0 for r in evaluated["paired_documents"]),
            "regressed": sum(r["delta"] < 0 for r in evaluated["paired_documents"]),
            "unchanged": sum(r["delta"] == 0 for r in evaluated["paired_documents"]),
        },
        "remaining_diagnostic_field_errors": failures,
        "conclusion": "Raw SFT gains are modest and distractor rejection remains weak. "
        "Explicit scope rules help on reused data, but six normal PDFs still require review. "
        "Collect independent real documents and a new holdout before further tuning.",
    }
    (ROOT / "reports/finetuning-analysis.json").write_text(json.dumps(analysis, indent=2) + "\n")
    print(json.dumps({"raw_field_delta_pp": delta, "remaining_diagnostic_errors": len(failures)}))


if __name__ == "__main__":
    main()
