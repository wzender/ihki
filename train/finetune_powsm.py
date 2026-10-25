"""Fine-tune POWSM-CTC (espnet/powsm_ctc) on Palestinian Arabic recordings: phone recognition with the <ara> prompt.

Data: the same CSVs as train/finetune.py (`audio,ipa`, plus the other Minerva columns). Labels are written in the
phrases.py convention and mapped onto POWSM's phone tokens (all but dʒ -> d͡ʒ and g -> ɡ are the same symbol).

Training uses POWSM's own loss, with targets built the way its recipe builds them (egs2/powsm/s2t1,
local/process_ipapack.py):
  text      "<ara><pr><notimestamps> /t//a//m//aː//m/"   the final layer and layer 14 learn this
  text_ctc  the same phones without length marks         layers 4, 8, 10 learn this (the recipe's "nosup" target)
  text_prev <na>
Audio is padded to POWSM's 20 s, with random leading silence in training as its preprocessor does. --pad random
pads each clip to a random length instead (none, 2, 4, 8, 12 or 20 s), so the model stops depending on 20 s of padding:
unpadded, the 20 s fine-tune lost 5 points of PER (16% -> 21%) and is 5x faster on a CPU.

Memory (6 GB): only the top --train-layers encoder layers train. The CTC head stays frozen unless --train-head:
it also feeds its predictions back into layers 4, 8 and 10, so training it means backpropagating through nearly the
whole encoder.

Needs the extra packages from scripts/setup_zero_shot_deps.sh. Example:
  python -m train.finetune_powsm --train data/minerva/train.csv --eval data/minerva/dev.csv \
      --out checkpoints/powsm-ft-minerva
Then compare on test:  python scripts/zero_shot_phone_models.py --models powsm-ft,ft --split test

Short padding, from the 20 s fine-tune:
  python -m train.finetune_powsm --train data/minerva/train.csv --eval data/minerva/dev.csv \
      --init checkpoints/powsm-ft-minerva/powsm_ft.pt --pad random --eval-pad 0 --epochs 6 --lr 3e-5 \
      --out checkpoints/powsm-ft-shortpad
"""
from __future__ import annotations

import argparse
import csv
import json
import math
import random
import sys
import time
from pathlib import Path

import numpy as np
import torch

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "scripts"))

import zero_shot_phone_models as zs  # noqa: E402
from app.scorer import SAMPLE_RATE, decode_audio  # noqa: E402

LANG, TASK = "<ara>", "<pr>"


class Targets:
    """Our phonemes -> POWSM token ids, for the `text` and `text_ctc` targets."""

    def __init__(self, s2t):
        self.tokenizer, self.converter = s2t.tokenizer, s2t.converter
        phones = [t[1:-1] for t in s2t.s2t_model.token_list if len(t) > 2 and t[0] == "/" and t[-1] == "/"]
        self.phones = set(phones)
        self.map: dict[str, str] = {}
        self._phones_by_canon: dict[str, list[str]] = {}
        for p in phones:
            self._phones_by_canon.setdefault(zs.canon(p), []).append(p)

    def phone(self, ours: str) -> str:
        if ours not in self.map:
            if ours in self.phones:
                self.map[ours] = ours
            else:  # the plainest POWSM spelling of the same sound (dʒ -> d͡ʒ, g -> ɡ)
                cands = sorted(self._phones_by_canon.get(ours, []), key=len)
                if not cands:
                    raise KeyError(f"POWSM has no phone token for {ours!r}")
                self.map[ours] = cands[0]
        return self.map[ours]

    def ids(self, text: str) -> list[int]:
        return self.converter.tokens2ids(self.tokenizer.text2tokens(text))

    def __call__(self, ipa: str) -> tuple[list[int], list[int]]:
        ours = [t for t in ipa.split() if t != "|"]
        collapsed = [p for i, p in enumerate(ours) if i == 0 or p != ours[i - 1]]  # geminates -> one sound
        powsm = [self.phone(p) for p in collapsed]
        text = self.ids(f"{LANG}{TASK}<notimestamps> " + "".join(f"/{p}/" for p in powsm))[1:]  # drop leading ▁
        nosup = [p.replace("ː", "") for p in powsm]
        nosup = [p for i, p in enumerate(nosup) if i == 0 or p != nosup[i - 1]]
        text_ctc = text[:2] + self.ids("".join(f"/{p}/" for p in nosup))
        return text, text_ctc


def load_rows(path: str, targets: Targets) -> list[dict]:
    root = Path(path).parent
    rows = []
    with open(path, encoding="utf-8") as f:
        for row in csv.DictReader(f):
            wav = decode_audio((root / row["audio"]).read_bytes())
            text, text_ctc = targets(row["ipa"])
            rows.append({"wav": wav, "text": text, "text_ctc": text_ctc, "ipa": row["ipa"], "item": row.get("item")})
    print(f"{path}: {len(rows)} utterances", flush=True)
    return rows


def pad_ids(seqs: list[list[int]], device) -> tuple[torch.Tensor, torch.Tensor]:
    n = max(len(s) for s in seqs)
    out = torch.full((len(seqs), n), -1, dtype=torch.long)
    for i, s in enumerate(seqs):
        out[i, : len(s)] = torch.tensor(s)
    return out.to(device), torch.tensor([len(s) for s in seqs], device=device)


SHORT_PADS = (0, 2, 4, 8, 12, 20)   # --pad random: the lengths a training clip is padded to (0: not at all)


def make_batch(items: list[dict], model, train: bool, device, pad: str = "20") -> dict:
    """POWSM's input: every clip padded to the same length, with some silence before it in training.
    pad "20": 20 s, as POWSM was trained. "random": a length from SHORT_PADS for the batch (at least its longest clip),
    and a random split of the padding before and after the speech."""
    longest = max(len(it["wav"]) for it in items)
    seconds = random.choice(SHORT_PADS) if pad == "random" else zs.Powsm.SECONDS
    n = max(longest, int(seconds * SAMPLE_RATE))
    speech = np.zeros((len(items), n), dtype=np.float32)
    for i, it in enumerate(items):
        wav = it["wav"][:n]
        start = random.randint(0, n - len(wav)) if train else 0  # the preprocessor's random leading silence
        speech[i, start: start + len(wav)] = wav
    text, text_len = pad_ids([it["text"] for it in items], device)
    text_ctc, text_ctc_len = pad_ids([it["text_ctc"] for it in items], device)
    prev, prev_len = pad_ids([[model.na]] * len(items), device)
    prefix, prefix_len = pad_ids([it["text"][:2] for it in items], device)
    return {"speech": torch.from_numpy(speech).to(device),
            "speech_lengths": torch.full((len(items),), n, dtype=torch.long, device=device),
            "text": text, "text_lengths": text_len, "text_ctc": text_ctc, "text_ctc_lengths": text_ctc_len,
            "text_prev": prev, "text_prev_lengths": prev_len, "prefix": prefix, "prefix_lengths": prefix_len}


def set_trainable(model, train_layers: int, train_head: bool) -> list[str]:
    for p in model.parameters():
        p.requires_grad = False
    layers = model.encoder.encoders
    parts = [*layers[len(layers) - train_layers:], model.encoder.after_norm]
    if train_head:
        parts += [model.ctc, model.encoder.conditioning_layer]
    for part in parts:
        for p in part.parameters():
            p.requires_grad = True
    return [k for k, p in model.named_parameters() if p.requires_grad]


def use_checkpointing(model, train_layers: int) -> None:
    """Recompute the trained layers' activations in the backward pass instead of keeping them."""
    from torch.utils.checkpoint import checkpoint
    layers = model.encoder.encoders
    for layer in layers[len(layers) - train_layers:]:
        fwd = layer.forward

        def wrapped(*args, _fwd=fwd, **kwargs):
            if not torch.is_grad_enabled():
                return _fwd(*args, **kwargs)
            return checkpoint(_fwd, *args, use_reentrant=False, **kwargs)
        layer.forward = wrapped


@torch.no_grad()
def evaluate(powsm, rows: list[dict], limit: int | None = None, pad: float | None = None) -> dict:
    """Greedy-decode every line (as the bake-off does) and score it with the bake-off's phoneme error rate,
    with the clips padded to `pad` seconds (None: POWSM's 20; 0: not padded)."""
    powsm.s2t.s2t_model.eval()
    powsm.pad = zs.Powsm.SECONDS if pad is None else pad
    errs = c_errs = total = c_total = 0
    long_n = long_ok = 0
    for it in rows[:limit]:
        tokens, _ = powsm(it["wav"])
        ref, hyp = zs.reference(it["ipa"]), zs.to_phones(tokens)
        e, pairs = zs.align(ref, hyp)
        errs, total = errs + e, total + len(ref)
        ce, _ = zs.align(zs.consonants(ref), zs.consonants(hyp))
        c_errs, c_total = c_errs + ce, c_total + len(zs.consonants(ref))
        for r, h in pairs:
            if r is not None and r.endswith("ː"):
                long_n += 1
                long_ok += r == h
    if torch.cuda.is_available():
        torch.cuda.empty_cache()  # hand decoding's buffers back before training resumes (6 GB is tight)
    return {"per": errs / total, "consonant_per": c_errs / c_total, "long_vowels": long_ok / max(1, long_n)}


def main():
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("--train", required=True)
    ap.add_argument("--eval", required=True)
    ap.add_argument("--out", default="checkpoints/powsm-ft")
    ap.add_argument("--epochs", type=float, default=10)
    ap.add_argument("--lr", type=float, default=5e-5)
    ap.add_argument("--batch", type=int, default=4)
    ap.add_argument("--accum", type=int, default=8, help="batches per optimizer step (effective batch = batch x accum)")
    ap.add_argument("--train-layers", type=int, default=8, help="train the top N of the 18 encoder layers")
    ap.add_argument("--train-head", action="store_true", help="also train the CTC head (needs much more memory)")
    ap.add_argument("--checkpointing", action="store_true", help="gradient checkpointing: less memory, slower")
    ap.add_argument("--max-steps", type=int, default=0, help="stop after this many optimizer steps (a memory check)")
    ap.add_argument("--eval-limit", type=int, help="score only the first N eval lines (a quick check)")
    ap.add_argument("--pad", choices=["20", "random"], default="20",
                    help="training clips padded to POWSM's 20 s, or to a random length (see SHORT_PADS)")
    ap.add_argument("--eval-pad", type=float, help="seconds the eval clips are padded to when picking the best epoch "
                    "(default 20; 0: none). 8 s is also reported when it differs")
    ap.add_argument("--init", help="start from a fine-tune's weights (powsm_ft.pt) instead of the published model")
    ap.add_argument("--seed", type=int, default=0)
    args = ap.parse_args()
    random.seed(args.seed)
    torch.manual_seed(args.seed)

    powsm = zs.Powsm(LANG, args.init)
    model, device = powsm.s2t.s2t_model, powsm.s2t.device
    targets = Targets(powsm.s2t)
    train_rows = load_rows(args.train, targets)
    eval_rows = load_rows(args.eval, targets)
    print("phone tokens:", {k: v for k, v in targets.map.items() if k != v} or "all the same symbol", flush=True)

    init_keys = set(torch.load(args.init, map_location="cpu")["state"]) if args.init else set()
    names = set_trainable(model, args.train_layers, args.train_head)
    trainable = [p for p in model.parameters() if p.requires_grad]
    print(f"training {sum(p.numel() for p in trainable) / 1e6:.0f}M of "
          f"{sum(p.numel() for p in model.parameters()) / 1e6:.0f}M parameters", flush=True)
    if args.checkpointing:
        use_checkpointing(model, args.train_layers)

    import bitsandbytes as bnb
    opt = bnb.optim.AdamW8bit(trainable, lr=args.lr, weight_decay=0.01)
    steps_per_epoch = math.ceil(len(train_rows) / (args.batch * args.accum))
    total_steps = args.max_steps or math.ceil(steps_per_epoch * args.epochs)
    warmup = max(1, int(0.1 * total_steps))
    sched = torch.optim.lr_scheduler.LambdaLR(
        opt, lambda s: min((s + 1) / warmup, max(0.0, (total_steps - s) / max(1, total_steps - warmup))))

    out = Path(args.out)
    out.mkdir(parents=True, exist_ok=True)
    def scores() -> dict:
        m = evaluate(powsm, eval_rows, args.eval_limit, args.eval_pad)
        if args.eval_pad is not None and args.eval_pad != 8:
            m["at_8s"] = evaluate(powsm, eval_rows, args.eval_limit, 8)
        return m

    best = scores()
    print(f"baseline: {json.dumps(best)}", flush=True)
    best_per, history, step = best["per"], [{"epoch": 0, **best}], 0

    def save(metrics: dict) -> None:
        keep = set(names) | init_keys   # with --init, also the weights the earlier fine-tune changed
        state = {k: v.detach().cpu() for k, v in model.state_dict().items() if k in keep}
        torch.save({"state": state, "lang": LANG, "phone_map": targets.map, "args": vars(args),
                    "metrics": metrics}, out / "powsm_ft.pt")

    epoch, t0 = 0, time.time()
    while step < total_steps:
        epoch += 1
        model.train()
        order = random.sample(range(len(train_rows)), len(train_rows))
        batches = [order[i: i + args.batch] for i in range(0, len(order), args.batch)]
        running, n_loss = 0.0, 0
        for b, idx in enumerate(batches):
            batch = make_batch([train_rows[i] for i in idx], model, True, device, args.pad)
            with torch.autocast("cuda", dtype=torch.bfloat16, enabled=device != "cpu"):
                loss, stats, _ = model(**batch)
            (loss / args.accum).backward()
            running, n_loss = running + float(loss), n_loss + 1
            if (b + 1) % args.accum == 0 or b + 1 == len(batches):
                torch.nn.utils.clip_grad_norm_(trainable, 1.0)
                opt.step()
                sched.step()
                opt.zero_grad(set_to_none=True)
                step += 1
                if step % 10 == 0:
                    mem = torch.cuda.max_memory_allocated() / 1e9 if device != "cpu" else 0
                    print(f"epoch {epoch} step {step}/{total_steps} loss {running / n_loss:.3f} "
                          f"lr {sched.get_last_lr()[0]:.2e} mem {mem:.1f}GB {time.time() - t0:.0f}s", flush=True)
                    running, n_loss = 0.0, 0
                if step >= total_steps:
                    break
        metrics = scores()
        history.append({"epoch": epoch, "step": step, **metrics})
        print(f"epoch {epoch}: {json.dumps(metrics)}", flush=True)
        if metrics["per"] < best_per:
            best_per = metrics["per"]
            save({"epoch": epoch, **metrics})
            print(f"  saved (best PER so far) to {out / 'powsm_ft.pt'}", flush=True)
        (out / "history.json").write_text(json.dumps(history, indent=1), encoding="utf-8")
    if not (out / "powsm_ft.pt").exists():
        print("no epoch beat the baseline; nothing saved", flush=True)
    print(f"best dev PER {best_per:.3f}; history in {out / 'history.json'}", flush=True)


if __name__ == "__main__":
    main()
