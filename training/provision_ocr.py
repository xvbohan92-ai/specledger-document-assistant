"""Provision the exact recorded OCR snapshots; reject upstream or software drift."""

import argparse
import importlib.metadata

from common import ROOT, read, sha


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--download", action="store_true")
    args = parser.parse_args()
    expected = read("training/ocr-model-spec.json")
    folder = ROOT / "data/models"
    for package in ("docling", "rapidocr"):
        if importlib.metadata.version(package) != expected[package]:
            raise ValueError(f"Use the locked {package} version recorded in the experiment")
    if args.download and not (folder / "manifest.json").exists():
        from docling.models.stages.ocr.rapid_ocr_model import RapidOcrModel
        from huggingface_hub import snapshot_download

        folder.mkdir(parents=True, exist_ok=True)
        for model in expected["models"]:
            prefix = model["repo"].replace("/", "--") + "/"
            patterns = [name[len(prefix) :] for name in expected["files"] if name.startswith(prefix)]
            snapshot_download(
                model["repo"],
                revision=model["revision"],
                local_dir=folder / prefix[:-1],
                allow_patterns=patterns,
                token=False,
            )
        RapidOcrModel.download_models(
            backend="onnxruntime", lang="en", model_size="small", local_dir=folder / "RapidOcr"
        )
    for name, digest in expected["files"].items():
        if sha(folder / name) != digest:
            raise ValueError(f"OCR snapshot drift: {name}; do not alter a saved fingerprint to accept it")
    original = ROOT / "training/ocr-model-spec.json"
    if not (folder / "manifest.json").exists():
        (folder / "manifest.json").write_bytes(original.read_bytes())
    assert sha(folder / "manifest.json") == sha(original)
    print("Pinned OCR models and software match the recorded experiment.")


if __name__ == "__main__":
    main()
