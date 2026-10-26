"""Finite field-label mapping. Model assigns a schema key; raw values stay source text."""

import hashlib
import json
import re
import threading
from pathlib import Path

from .fields import SCHEMA, extract_fields, normalized_observation
from .models import Observation, ProjectError

DESCRIPTIONS = Path(__file__).with_name("field_descriptions.json")


def sha(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def split_candidate(text):
    text = text.strip().strip("| ").replace("**", "")
    if "|" in text:
        parts = [part.strip() for part in text.split("|")]
        if len(parts) != 2:
            return None
    else:
        parts = re.split(r"\s*:\s*|\s{2,}", text, maxsplit=1)
    if len(parts) != 2 or not 1 <= len(parts[0]) <= 180 or not 1 <= len(parts[1]) <= 256:
        return None
    label, raw = parts
    if label.casefold() in {"parameter", "attribute", "item", "characteristic"} and raw.casefold() in {
        "value",
        "declared value",
        "rating",
        "specification",
    }:
        return None
    return label, " ".join(raw.split())


class FieldMapper:
    def __init__(self, folder, variant="finetuned"):
        if variant not in ("base", "finetuned"):
            raise ProjectError("字段映射器只支持 rules、base 或 finetuned。")
        manifest_path = folder / "manifest.json"
        try:
            self.manifest = json.loads(manifest_path.read_text())
            if self.manifest["descriptions_sha256"] != sha(DESCRIPTIONS):
                raise ProjectError("字段定义与训练时的定义不同，不能加载旧检查点。")
            self.path = folder / variant
            record = self.manifest["variants"][variant]
            for name, digest in record["files"].items():
                target = (self.path / name).resolve()
                if not target.is_relative_to(self.path.resolve()) or sha(target) != digest:
                    raise ProjectError("字段映射模型文件指纹不匹配。")
            import torch
            from transformers import AutoModelForSequenceClassification, AutoTokenizer

            torch.set_num_threads(2)
            self.model = AutoModelForSequenceClassification.from_pretrained(
                str(self.path), local_files_only=True, trust_remote_code=False, use_safetensors=True
            ).eval()
            self.tokenizer = AutoTokenizer.from_pretrained(
                str(self.path), local_files_only=True, trust_remote_code=False
            )
            if self.model.config.num_labels != 1:
                raise ProjectError("字段映射器需要已验证的单 logit 架构。")
        except ProjectError:
            raise
        except (ImportError, KeyError, ValueError, OSError) as exc:
            raise ProjectError(
                "字段映射模型缺失或无效；请准备模型与 neural 依赖，或显式选择 rules。"
            ) from exc
        self.variant = variant
        self.descriptions = json.loads(DESCRIPTIONS.read_text())
        self.identity = record["files"]["model.safetensors"]
        self.lock = threading.Lock()
        self.cache = {}

    def describe(self):
        return {
            "kind": "minilm-field-label-mapper",
            "variant": self.variant,
            "weight_sha256": self.identity,
            "descriptions_sha256": sha(DESCRIPTIONS),
            "input": "field label text only",
            "generates_values": False,
            "reject_class": "ignore",
            "probabilities_calibrated": False,
        }

    def predict(self, labels):
        import torch

        with self.lock:
            pending = list(dict.fromkeys(label for label in labels if label not in self.cache))
            names, descriptions = list(self.descriptions), list(self.descriptions.values())
            with torch.inference_mode():
                for start in range(0, len(pending), 8):
                    part = pending[start : start + 8]
                    encoded = self.tokenizer(
                        descriptions * len(part),
                        [label for label in part for _ in descriptions],
                        padding=True,
                        truncation=True,
                        max_length=128,
                        return_tensors="pt",
                    )
                    values = self.model(**encoded).logits.reshape(len(part), len(names)).tolist()
                    for label, scores in zip(part, values, strict=True):
                        order = sorted(range(len(names)), key=lambda i: (-scores[i], i))
                        self.cache[label] = {
                            "field": names[order[0]],
                            "margin": scores[order[0]] - scores[order[1]],
                            "model_weight_sha256": self.identity,
                            "raw_label": label,
                        }
            return [dict(self.cache[label]) for label in labels]

    def extract(self, pages):
        return mapped_extract(pages, self)


def mapped_extract(pages, mapper):
    observations = extract_fields(pages)
    recognized = {(o.evidence.page, o.evidence.text) for o in observations}
    rows = []
    for page in pages:
        for line in page.lines:
            if (line.page, line.text) not in recognized and (parts := split_candidate(line.text)):
                rows.append((line, *parts))
    predictions = mapper.predict([row[1] for row in rows]) if rows else []
    if len(predictions) != len(rows):
        raise ProjectError("字段映射器返回条数不一致。")
    for (line, label, raw), prediction in zip(rows, predictions, strict=True):
        name = prediction["field"]
        if name == "ignore":
            continue
        if name not in SCHEMA:
            raise ProjectError("字段映射器返回了未定义字段。")
        if any(o.name == name and o.raw_value == raw and o.evidence.page == line.page for o in observations):
            continue
        observations.append(
            normalized_observation(
                Observation(
                    name=name,
                    raw_value=raw,
                    evidence=line,
                    mapping={**prediction, "raw_label": label, "method": "neural label mapping"},
                )
            )
        )
    return observations
