import os
import time

os.environ["HF_HUB_OFFLINE"] = "1"
os.environ["TOKENIZERS_PARALLELISM"] = "false"

import torch
from common import ROOT, read
from transformers import AutoModelForSequenceClassification, AutoTokenizer


def load_model(path=None):
    path = path or ROOT / "data/field-models/base"
    model = AutoModelForSequenceClassification.from_pretrained(
        str(path), local_files_only=True, trust_remote_code=False, use_safetensors=True
    )
    tokenizer = AutoTokenizer.from_pretrained(str(path), local_files_only=True, trust_remote_code=False)
    assert model.config.num_labels == 1, "Keep the original scalar head in both conditions"
    return model, tokenizer


def encode(tokenizer, labels):
    descriptions = list(read("specledger/field_descriptions.json").values())
    queries = descriptions * len(labels)
    texts = [label for label in labels for _ in descriptions]
    return tokenizer(queries, texts, padding=True, truncation=True, max_length=128, return_tensors="pt")


def predict(model, tokenizer, labels):
    model.eval()
    scores = []
    with torch.inference_mode():
        for start in range(0, len(labels), 8):
            part = labels[start : start + 8]
            scores.extend(model(**encode(tokenizer, part)).logits.reshape(len(part), 8).tolist())
    return scores


def metrics(rows, scores):
    fields = list(read("specledger/field_descriptions.json"))
    records = []
    for row, values in zip(rows, scores, strict=True):
        order = sorted(range(8), key=lambda i: (-values[i], i))
        records.append(
            {
                **row,
                "predicted": fields[order[0]],
                "scores": values,
                "margin": values[order[0]] - values[order[1]],
            }
        )
    per_class = {}
    for field in fields:
        tp = sum(r["field"] == field and r["predicted"] == field for r in records)
        fp = sum(r["field"] != field and r["predicted"] == field for r in records)
        fn = sum(r["field"] == field and r["predicted"] != field for r in records)
        per_class[field] = {"tp": tp, "fp": fp, "fn": fn, "f1": 2 * tp / (2 * tp + fp + fn) if tp else 0.0}
    negatives = [r for r in records if r["field"] == "ignore"]
    return {
        "rows": len(rows),
        "correct": sum(r["field"] == r["predicted"] for r in records),
        "accuracy": sum(r["field"] == r["predicted"] for r in records) / len(rows),
        "macro_f1": sum(v["f1"] for v in per_class.values()) / 8,
        "distractor_false_accepts": sum(r["predicted"] != "ignore" for r in negatives),
        "distractor_total": len(negatives),
        "per_class": per_class,
        "records": records,
    }


def evaluate(model, tokenizer, split):
    rows = read(f"training/datasets/{split}.json")
    started = time.perf_counter()
    result = metrics(rows, predict(model, tokenizer, [row["label"] for row in rows]))
    result["elapsed_ms"] = round((time.perf_counter() - started) * 1000, 2)
    return result
