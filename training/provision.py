"""Explicit optional public checkpoint download and local artifact identity manifests."""

import argparse
import json

from common import ROOT, read, sha


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--download", action="store_true")
    args = parser.parse_args()
    config = read("training/config.json")
    root = ROOT / "data/field-models"
    if args.download:
        if (root / "base/model.safetensors").exists():
            raise ValueError(
                "An existing base is present; retain and verify it instead of downloading over it"
            )
        from huggingface_hub import snapshot_download

        snapshot_download(
            config["base_repo"],
            revision=config["base_revision"],
            local_dir=root / "base",
            allow_patterns=["*.json", "*.txt", "*.safetensors"],
            ignore_patterns=["onnx/*", "openvino/*"],
            token=False,
        )
    variants = {}
    for name in ("base", "finetuned"):
        folder = root / name
        if not (folder / "model.safetensors").exists():
            continue
        variants[name] = {
            "files": {
                p.name: sha(p)
                for p in sorted(folder.iterdir())
                if p.is_file() and p.suffix in (".json", ".txt", ".safetensors")
            }
        }
    if "base" not in variants:
        raise ValueError("Prepare the public base explicitly with --download first")
    report = ROOT / "reports/finetuning-training.json"
    if report.exists():
        trained = json.loads(report.read_text())
        assert variants["base"]["files"]["model.safetensors"] == trained["base_weight_sha256"]
        if "finetuned" in variants:
            assert variants["finetuned"]["files"]["model.safetensors"] == trained["selected_weight_sha256"]
    manifest = {
        "repo": config["base_repo"],
        "revision": config["base_revision"],
        "descriptions_sha256": sha(ROOT / "specledger/field_descriptions.json"),
        "variants": variants,
    }
    (root / "manifest.json").write_text(json.dumps(manifest, indent=2) + "\n")
    print(json.dumps({"variants": list(variants), "base_revision": config["base_revision"]}))


if __name__ == "__main__":
    main()
