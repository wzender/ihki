"""Fine-tune the phoneme CTC model on Palestinian Arabic recordings.

Data: CSV files with columns `audio,ipa`.
  audio  path to any audio file ffmpeg can read (relative to the CSV's folder)
  ipa    phonemes in the phrases.py convention, e.g. "w eː n | i l ħ a m m aː m"

Label what was actually SAID, not what was intended: a learner recording of الحمام said with a
Hebrew ח is labelled "w eː n | i l χ a m m aː m". Those rows teach the model to separate ħ from χ,
which is exactly what the scorer depends on.

Example:
  python -m train.finetune --train data/train.csv --eval data/eval.csv --out checkpoints/ihki-ft
Then serve it with MODEL_ID=checkpoints/ihki-ft.
"""
from __future__ import annotations

import argparse
import csv
import json
import shutil
from pathlib import Path

import numpy as np
import torch
from huggingface_hub import hf_hub_download
from transformers import AutoFeatureExtractor, Trainer, TrainingArguments, Wav2Vec2ForCTC

from app.align import edit_distance, greedy_decode
from app.scorer import Vocab, decode_audio, tokenize


class PhonemeDataset(torch.utils.data.Dataset):
    def __init__(self, csv_path: str, vocab: Vocab, fe):
        root = Path(csv_path).parent
        self.items = []
        with open(csv_path, encoding="utf-8") as f:
            for row in csv.DictReader(f):
                wav = decode_audio((root / row["audio"]).read_bytes())
                labels = [t.id for t in tokenize(row["ipa"], vocab)]
                x = fe(wav, sampling_rate=16_000).input_values[0]
                self.items.append({"input_values": x, "labels": labels})
        print(f"{csv_path}: {len(self.items)} utterances")

    def __len__(self):
        return len(self.items)

    def __getitem__(self, i):
        return self.items[i]


class Collator:
    def __init__(self, fe):
        self.fe = fe

    def __call__(self, batch):
        x = self.fe.pad([{"input_values": b["input_values"]} for b in batch], return_tensors="pt")
        n = max(len(b["labels"]) for b in batch)
        labels = torch.full((len(batch), n), -100, dtype=torch.long)
        for i, b in enumerate(batch):
            labels[i, : len(b["labels"])] = torch.tensor(b["labels"])
        return {"input_values": x["input_values"], "labels": labels}


def per_metric(blank: int):
    """Phoneme error rate with greedy decoding."""
    def compute(pred):
        logits, labels = pred.predictions, pred.label_ids
        errs = total = 0
        for lg, lb in zip(logits, labels):
            lg = lg[~np.all(lg == -100, axis=1)]  # drop frames padded by the Trainer
            hyp = greedy_decode(lg, blank)
            ref = [int(t) for t in lb if t != -100]
            errs += edit_distance(hyp, ref)
            total += len(ref)
        return {"per": errs / max(1, total)}
    return compute


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--train", required=True)
    ap.add_argument("--eval", required=True)
    ap.add_argument("--base", default="facebook/wav2vec2-xlsr-53-espeak-cv-ft")
    ap.add_argument("--out", default="checkpoints/ihki-ft")
    ap.add_argument("--epochs", type=float, default=20)
    ap.add_argument("--lr", type=float, default=3e-5)
    ap.add_argument("--batch", type=int, default=8)
    args = ap.parse_args()

    vocab_path = hf_hub_download(args.base, "vocab.json") if not Path(args.base).is_dir() else str(Path(args.base) / "vocab.json")
    vocab = Vocab(json.loads(Path(vocab_path).read_text(encoding="utf-8")))
    fe = AutoFeatureExtractor.from_pretrained(args.base)
    model = Wav2Vec2ForCTC.from_pretrained(args.base, ctc_loss_reduction="mean", ctc_zero_infinity=True,
                                           pad_token_id=vocab.blank)
    model.freeze_feature_encoder()  # small data: train only the transformer + CTC head

    train_ds = PhonemeDataset(args.train, vocab, fe)
    eval_ds = PhonemeDataset(args.eval, vocab, fe)
    if vocab.downgraded:
        print("phoneme fallbacks used in labels:", vocab.downgraded)

    targs = TrainingArguments(
        output_dir=args.out, per_device_train_batch_size=args.batch, per_device_eval_batch_size=args.batch,
        gradient_accumulation_steps=max(1, 32 // args.batch), learning_rate=args.lr, warmup_ratio=0.1,
        num_train_epochs=args.epochs, eval_strategy="epoch", save_strategy="epoch", save_total_limit=2,
        load_best_model_at_end=True, metric_for_best_model="per", greater_is_better=False,
        fp16=torch.cuda.is_available(), logging_steps=10, report_to="none", remove_unused_columns=False,
    )
    trainer = Trainer(model=model, args=targs, train_dataset=train_ds, eval_dataset=eval_ds,
                      data_collator=Collator(fe), compute_metrics=per_metric(vocab.blank),
                      preprocess_logits_for_metrics=lambda logits, labels: torch.log_softmax(logits, -1))
    print("baseline:", trainer.evaluate())
    trainer.train()
    print("final:", trainer.evaluate())

    trainer.save_model(args.out)
    fe.save_pretrained(args.out)
    shutil.copy(vocab_path, Path(args.out) / "vocab.json")
    print(f"saved to {args.out}; serve with MODEL_ID={args.out}")


if __name__ == "__main__":
    main()
