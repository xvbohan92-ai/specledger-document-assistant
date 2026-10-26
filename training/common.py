import hashlib
import json
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def sha(path):
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def read(path):
    return json.loads((ROOT / path).read_text())


def verify_frozen():
    manifest = read("training/datasets/manifest.json")
    for path, digest in manifest["files"].items():
        if sha(ROOT / path) != digest:
            raise ValueError(f"Frozen training input changed: {path}")
    return manifest


def prepare():
    specs = read("training/label-specs.json")
    folder = ROOT / "training/datasets"
    if (folder / "manifest.json").exists():
        raise ValueError("Frozen data already exists; use another experiment")
    folder.mkdir(parents=True)
    sets = {}
    all_labels = set()
    for split in ("train", "valid", "test"):
        rows = []
        for field, splits in specs.items():
            for i, label in enumerate(splits[split]):
                key = label.casefold().strip()
                assert key not in all_labels, f"Repeated label across splits: {label}"
                all_labels.add(key)
                rows.append({"id": f"{split}-{field}-{i}", "label": label, "field": field})
        (folder / f"{split}.json").write_text(json.dumps(rows, indent=2) + "\n")
        sets[split] = len(rows)
    files = ["training/label-specs.json", "training/config.json", "specledger/field_descriptions.json"]
    files += [f"training/datasets/{split}.json" for split in sets]
    manifest = {
        "rows": sets,
        "classes": list(specs),
        "files": {p: sha(ROOT / p) for p in files},
        "scope": "Same-author synthetic English field labels, eight closed classes. Exact label strings "
        "are disjoint, but vocabulary and schema concepts overlap; no independent annotation "
        "or unseen-vendor/OCR generalization claim. Input is label text only, never values or units.",
    }
    (folder / "manifest.json").write_text(json.dumps(manifest, indent=2) + "\n")
    print(json.dumps(sets))


if __name__ == "__main__":
    prepare()
