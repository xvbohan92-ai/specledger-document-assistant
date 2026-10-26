"""Real weight updates; no test model outputs until validation selection is complete."""

import json
import random
import time

import torch
from common import ROOT, read, sha, verify_frozen
from runtime import encode, evaluate, load_model


def main():
    manifest = verify_frozen()
    config = read("training/config.json")
    destination = ROOT / "data/field-models/finetuned"
    output = ROOT / "reports/finetuning-training.json"
    if destination.exists() or output.exists():
        raise ValueError("Do not overwrite a completed training run")
    torch.set_num_threads(config["torch_threads"])
    torch.manual_seed(config["seed"])
    torch.use_deterministic_algorithms(True)
    model, tokenizer = load_model()
    initial = {key: value.detach().clone() for key, value in model.state_dict().items()}
    descriptions = list(read("specledger/field_descriptions.json").values())
    longest = max(
        len(tokenizer(d, row["label"], truncation=False)["input_ids"])
        for row in read("training/datasets/train.json")
        for d in descriptions
    )
    assert longest <= config["max_tokens"], "Training truncation is not allowed in this experiment"
    baseline = evaluate(model, tokenizer, "valid")
    optimizer = torch.optim.AdamW(
        model.parameters(), lr=config["learning_rate"], weight_decay=config["weight_decay"]
    )
    rows = read("training/datasets/train.json")
    names = list(read("specledger/field_descriptions.json"))
    history = []
    best = -1.0
    started = time.perf_counter()
    steps = 0
    for epoch in range(1, config["epochs"] + 1):
        order = list(range(len(rows)))
        random.Random(config["seed"] + epoch).shuffle(order)
        model.train()
        losses = []
        for start in range(0, len(order), config["batch_size"]):
            batch = [rows[i] for i in order[start : start + config["batch_size"]]]
            optimizer.zero_grad(set_to_none=True)
            logits = model(**encode(tokenizer, [r["label"] for r in batch])).logits.reshape(len(batch), 8)
            loss = torch.nn.functional.cross_entropy(
                logits, torch.tensor([names.index(r["field"]) for r in batch])
            )
            assert torch.isfinite(loss)
            loss.backward()
            torch.nn.utils.clip_grad_norm_(model.parameters(), 1.0)
            optimizer.step()
            steps += 1
            losses.append(float(loss.detach()))
        validation = evaluate(model, tokenizer, "valid")
        record = {
            "epoch": epoch,
            "steps": steps,
            "loss": sum(losses) / len(losses),
            "validation_macro_f1": validation["macro_f1"],
            "validation_accuracy": validation["accuracy"],
        }
        history.append(record)
        if validation["macro_f1"] > best + 1e-12:
            best = validation["macro_f1"]
            model.save_pretrained(destination, safe_serialization=True)
            tokenizer.save_pretrained(destination)
            selected = record
            selected_validation = validation
        print(json.dumps(record), flush=True)
    model, _ = load_model(destination)
    delta = (
        sum(float(torch.sum((v.detach() - initial[k]) ** 2)) for k, v in model.state_dict().items()) ** 0.5
    )
    assert delta > 0
    report = {
        "config": config,
        "dataset": manifest,
        "base_validation": baseline,
        "selected_validation": selected_validation,
        "selected": selected,
        "history": history,
        "steps": steps,
        "parameters": sum(p.numel() for p in model.parameters()),
        "weight_l2_change": delta,
        "base_weight_sha256": sha(ROOT / "data/field-models/base/model.safetensors"),
        "selected_weight_sha256": sha(destination / "model.safetensors"),
        "descriptions_sha256": sha(ROOT / "specledger/field_descriptions.json"),
        "train_source_sha256": sha(__file__),
        "runtime_source_sha256": sha(ROOT / "training/runtime.py"),
        "maximum_train_tokens": longest,
        "elapsed_seconds": round(time.perf_counter() - started, 2),
        "deployment_variant": "finetuned" if best > baseline["macro_f1"] + 1e-12 else "base",
        "test_model_outputs_used_for_selection": False,
    }
    output.write_text(json.dumps(report, indent=2) + "\n")
    print(json.dumps({"deployment_variant": report["deployment_variant"], "selected": selected}), flush=True)


if __name__ == "__main__":
    main()
