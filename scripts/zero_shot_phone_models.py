#!/usr/bin/env python3
"""How well do other phone recognizers hear Palestinian Arabic as-is? Zero-shot, on held-out Minerva lines.

Usage:  python scripts/zero_shot_phone_models.py                          # every model, dev.csv
        python scripts/zero_shot_phone_models.py --models zipa,ft --split test
        python scripts/zero_shot_phone_models.py --report                 # print the table from the saved JSON

ZIPA and POWSM need extra packages: run scripts/setup_zero_shot_deps.sh once (it installs into .zero-shot-deps/,
outside the app venv). Results go to data/minerva/bench/zero-shot-<split>.json, merged with earlier runs, so models
can be run one at a time.

Each model's greedy CTC output is mapped onto the labels' phoneme set before scoring (canon()): length marks join
their vowel, details the labels never write (dental t̪, aspiration) are dropped, symbols with no contrast in
Palestinian are merged (χ→x, ʁ→ɣ, ɾ→r), and vowel qualities fold to a e i o u (ɛ→e, ɪ→i, ə→e). Geminates collapse
to one sound in both, as the scorer's tokenize() does. Measured:
  PER             phoneme error rate (edit distance / reference length)
  consonant PER   the same with every vowel removed from both sides: the sounds the scorer grades hardest
  per sound       how often each labelled sound was heard as itself, and what was heard instead
  vowel length    of the labelled vowels the model heard as some vowel, how often it got long/short right
  frames/s        CTC frame rate: the long-vowel check measures vowel length in frames
  s/clip          wall time per clip on this machine
The fine-tuned checkpoint picked its best epoch on dev.csv, so its dev numbers are slightly optimistic; use
--split test for reported numbers.
"""
from __future__ import annotations

import argparse
import collections
import csv
import json
import sys
import time
import unicodedata
from pathlib import Path

import numpy as np

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from app.align import greedy_decode  # noqa: E402
from app.scorer import SAMPLE_RATE, decode_audio  # noqa: E402

DATA = ROOT / "data" / "minerva"
# the sounds the table shows (the JSON has every sound)
KEY_SOUNDS = ["ħ", "ʕ", "x", "ɣ", "h", "ʔ", "tˤ", "sˤ", "dˤ"]
VOWELS = set("aeiou")

# ---------- mapping a model's IPA onto the labels' phoneme set ----------

DROP = set("̪̺̻̥̬̃̚ʰʲʷ̩̯̆̈ʼ̟̠̝̞̹̜˞ˑ̤̰")  # details the labels never write
SAME = {  # no contrast in Palestinian (or a spelling of the same symbol)
    "ɡ": "g", "ʤ": "dʒ", "ʧ": "tʃ", "χ": "x", "ʁ": "ɣ", "ɾ": "r", "ɹ": "r", "ʀ": "r", "ɫ": "l",
    "ʜ": "ħ", "ʢ": "ʕ", "ɦ": "h",
}
FOLD = {  # vowel quality -> the five the labels use
    "ɑ": "a", "æ": "a", "ɐ": "a", "ʌ": "a", "ä": "a", "ɒ": "a",
    "ɛ": "e", "ə": "e", "œ": "e", "ɘ": "e", "ɜ": "e",
    "ɪ": "i", "ɨ": "i", "y": "i", "ʏ": "i",
    "ɔ": "o", "ɵ": "o", "ø": "o",
    "ʊ": "u", "ɯ": "u", "ʉ": "u",
}


def is_modifier(ch: str) -> bool:
    return unicodedata.category(ch) in ("Mn", "Lm", "Sk") and ch not in "ʔʕ"


def canon(phone: str) -> str:
    """One phone (as a model writes it) -> the labels' notation, '' if nothing is left."""
    p = "".join(ch for ch in phone.replace("͡", "").replace("‿", "") if ch not in DROP)
    p = p.replace("ˠ", "ˤ").replace("̴", "ˤ")  # velarized ~ pharyngealized: both are how emphatics get written
    long = "ː" in p
    p = p.replace("ː", "")
    p = SAME.get(p, p)
    p = "".join(FOLD.get(ch, ch) for ch in p) if p and all(FOLD.get(ch, ch) in VOWELS for ch in p) else p
    if len(p) > 1 and all(ch in VOWELS for ch in p):  # a diphthong or a doubled vowel
        p = p[0]
    if not p:
        return ""
    return p + "ː" if long and p in VOWELS else p  # a long consonant is a geminate: collapsed below anyway


AFFRICATES = {("d", "ʒ"): "dʒ", ("t", "ʃ"): "tʃ"}


def to_phones(tokens: list[str]) -> list[str]:
    """Model tokens (whole phones, or single characters as ZIPA writes them) -> canonical phones,
    geminates collapsed."""
    phones: list[str] = []
    for tok in tokens:
        tok = tok.strip()
        if not tok or tok in ("|", "▁") or (tok.startswith("<") and tok.endswith(">")):
            continue
        if phones and all(is_modifier(ch) for ch in tok):  # ː or ˤ written as its own token
            phones[-1] += tok
        elif phones and phones[-1].endswith("͡"):        # tie bar: t͡ ʃ
            phones[-1] += tok
        else:
            phones.append(tok)
    out: list[str] = []
    for p in phones:
        c = canon(p)
        if not c:
            continue
        if out and (out[-1], c) in AFFRICATES:
            out[-1] = AFFRICATES[(out[-1], c)]
        elif not out or out[-1] != c:
            out.append(c)
    return out


def reference(ipa: str) -> list[str]:
    return to_phones([t for t in ipa.split() if t != "|"])


def align(ref: list[str], hyp: list[str]) -> tuple[int, list[tuple[str | None, str | None]]]:
    """Levenshtein distance and the aligned pairs (ref, hyp); None marks an insertion or a deletion."""
    n, m = len(ref), len(hyp)
    d = np.zeros((n + 1, m + 1), dtype=np.int32)
    d[:, 0] = np.arange(n + 1)
    d[0, :] = np.arange(m + 1)
    for i in range(1, n + 1):
        for j in range(1, m + 1):
            d[i, j] = min(d[i - 1, j - 1] + (ref[i - 1] != hyp[j - 1]), d[i - 1, j] + 1, d[i, j - 1] + 1)
    pairs: list[tuple[str | None, str | None]] = []
    i, j = n, m
    while i or j:
        if i and j and d[i, j] == d[i - 1, j - 1] + (ref[i - 1] != hyp[j - 1]):
            pairs.append((ref[i - 1], hyp[j - 1]))
            i, j = i - 1, j - 1
        elif i and d[i, j] == d[i - 1, j] + 1:
            pairs.append((ref[i - 1], None))
            i -= 1
        else:
            pairs.append((None, hyp[j - 1]))
            j -= 1
    return int(d[n, m]), pairs[::-1]


def consonants(phones: list[str]) -> list[str]:
    return [p for p in phones if p.rstrip("ː") not in VOWELS]


# ---------- models ----------

def use_extra_deps() -> None:
    from app.powsm import use_extra_deps as use
    try:
        use()
    except RuntimeError as e:
        sys.exit(str(e))


class HFModel:
    """The app's own emitter (wav2vec2 phoneme CTC)."""

    def __init__(self, model_id: str):
        from app.scorer import HFEmitter
        self.em = HFEmitter(model_id)
        self.i2t = {i: t for t, i in self.em.token_to_id.items()}
        self.blank = self.em.token_to_id["<pad>"]
        self.inventory = {canon(t) for t in self.em.token_to_id if not t.startswith("<")}

    def __call__(self, wav: np.ndarray) -> tuple[list[str], int]:
        logp = self.em(wav)
        return [self.i2t[i] for i in greedy_decode(logp, self.blank)], logp.shape[0]

    def close(self) -> None:
        del self.em


class Zipa:
    """ZIPA (Zipformer, CR-CTC) from its official ONNX export, on CPU: onnxruntime's GPU build needs CUDA 12."""

    def __init__(self, repo: str = "anyspeech/zipa-large-crctc-500k"):
        use_extra_deps()
        import onnxruntime as ort
        from huggingface_hub import hf_hub_download
        from lhotse.features.kaldi.extractors import Fbank, FbankConfig
        self.sess = ort.InferenceSession(hf_hub_download(repo, "model.onnx"), providers=["CPUExecutionProvider"])
        self.fbank = Fbank(FbankConfig(num_filters=80, dither=0.0, snip_edges=False))  # as ZIPA's inference/utils.py
        self.tokens: dict[int, str] = {}
        for line in open(hf_hub_download(repo, "tokens.txt"), encoding="utf-8"):
            tok, idx = line.rstrip("\n").rsplit(" ", 1)
            self.tokens[int(idx)] = tok
        self.inventory = None  # it writes phones letter by letter, so any combination is possible

    def __call__(self, wav: np.ndarray) -> tuple[list[str], int]:
        import torch
        feats = self.fbank.extract_batch([torch.from_numpy(wav.copy())], sampling_rate=SAMPLE_RATE)[0]
        feats = feats.unsqueeze(0).numpy()
        logp, lens = self.sess.run(None, {"x": feats, "x_lens": np.array([feats.shape[1]], dtype=np.int64)})
        logp = logp[0][: int(lens[0])]
        return [self.tokens[i] for i in greedy_decode(logp, 0)], logp.shape[0]

    def close(self) -> None:
        del self.sess


class Powsm:
    """POWSM-CTC (ESPnet), greedy CTC with the phone-recognition task token and a language token."""

    SECONDS = 20  # trained on 20 s inputs: the model card says pad to 20 s (None or 0: no padding)

    def __init__(self, lang: str = "<unk>", checkpoint: str | None = None, pad: float | None = None):
        use_extra_deps()
        self.pad = self.SECONDS if pad is None else pad
        from app.powsm import load_powsm
        self.s2t = load_powsm(lang, checkpoint)
        self.enc_frames = 0
        self.s2t.s2t_model.encoder.register_forward_hook(self._count)
        toks = self.s2t.s2t_model.token_list
        self.inventory = {canon(t[1:-1]) for t in toks if len(t) > 2 and t[0] == "/" and t[-1] == "/"}

    def _count(self, module, inputs, output):
        enc = output[0][0] if isinstance(output[0], tuple) else output[0]
        self.enc_frames = enc.shape[1] - 2  # minus the language and task positions

    def __call__(self, wav: np.ndarray) -> tuple[list[str], int]:
        n = int((self.pad or 0) * SAMPLE_RATE)
        x = np.pad(wav, (0, max(0, n - len(wav)))).astype(np.float32)  # a longer recording runs as it is
        tokens = self.s2t(x)[0][1]
        phones = [t[1:-1] for t in tokens if len(t) > 2 and t[0] == "/" and t[-1] == "/"]
        return phones, round(self.enc_frames * len(wav) / len(x))  # frames that cover the speech

    def close(self) -> None:
        del self.s2t


MODELS = {  # name -> (label, how it would plug into the app, loader)
    "base": ("xlsr-53-espeak (current base)", "yes", lambda: HFModel("facebook/wav2vec2-xlsr-53-espeak-cv-ft")),
    "ft": ("ihki-ft-minerva (current fine-tune)", "yes",
           lambda: HFModel(str(ROOT / "checkpoints" / "ihki-ft-minerva"))),
    "zipa": ("ZIPA large CR-CTC", "new loader (ONNX)", lambda: Zipa()),
    "powsm": ("POWSM-CTC, no language", "new loader (ESPnet)", lambda: Powsm("<unk>", None, POWSM_PAD)),
    "powsm-ara": ("POWSM-CTC, Arabic prompt", "new loader (ESPnet)", lambda: Powsm("<ara>", None, POWSM_PAD)),
    "powsm-ft": ("POWSM-CTC fine-tuned (Arabic prompt)", "new loader (ESPnet)",
                 lambda: Powsm("<ara>", str(ROOT / "checkpoints" / "powsm-ft-minerva" / "powsm_ft.pt"), POWSM_PAD)),
    "powsm-ft-short": ("POWSM-CTC fine-tuned on short padding", "new loader (ESPnet)",
                       lambda: Powsm("<ara>", str(ROOT / "checkpoints" / "powsm-ft-shortpad" / "powsm_ft.pt"),
                                     POWSM_PAD)),
}
POWSM_PAD: float | None = None  # --powsm-pad: seconds the POWSM models pad to (None: their 20 s)


# ---------- running and reporting ----------

def evaluate(name: str, rows: list[dict], clips: list[np.ndarray]) -> dict:
    label, fits, load = MODELS[name]
    print(f"[{name}] loading {label}", file=sys.stderr)
    model = load()
    model(clips[0])  # warm-up, not timed
    errs = c_errs = total = c_total = 0
    frames = seconds = elapsed = 0.0
    n = collections.Counter()
    heard: dict[str, collections.Counter] = collections.defaultdict(collections.Counter)
    length_ok = length_n = 0
    lines = []
    for k, (row, wav) in enumerate(zip(rows, clips)):
        t = time.perf_counter()
        tokens, n_frames = model(wav)
        elapsed += time.perf_counter() - t
        frames += n_frames
        seconds += len(wav) / SAMPLE_RATE
        ref, hyp = reference(row["ipa"]), to_phones(tokens)
        e, pairs = align(ref, hyp)
        errs, total = errs + e, total + len(ref)
        ce, _ = align(consonants(ref), consonants(hyp))
        c_errs, c_total = c_errs + ce, c_total + len(consonants(ref))
        for r, h in pairs:
            if r is None:
                continue
            n[r] += 1
            heard[r][h if h is not None else "∅"] += 1
            if h is not None and r.rstrip("ː") in VOWELS and h.rstrip("ː") in VOWELS:
                length_n += 1
                length_ok += r.endswith("ː") == h.endswith("ː")
        lines.append({"item": row["item"], "ref": " ".join(ref), "hyp": " ".join(hyp), "errors": e})
        if (k + 1) % 50 == 0:
            print(f"[{name}] {k + 1}/{len(rows)}  PER so far {errs / total:.3f}", file=sys.stderr)
    inventory = getattr(model, "inventory", None)
    model.close()
    try:
        import torch
        torch.cuda.empty_cache()
    except ImportError:
        pass
    sounds = {}
    for s in sorted(n, key=lambda s: -n[s]):
        sounds[s] = {"n": n[s], "recall": heard[s][s] / n[s],
                     "heard_as": dict(heard[s].most_common(6)),
                     "in_vocab": None if inventory is None else s in inventory}
    return {"label": label, "fits_app": fits, "clips": len(rows), "per": errs / total,
            "consonant_per": c_errs / c_total, "vowel_length_accuracy": length_ok / max(1, length_n),
            "frames_per_second": frames / seconds, "seconds_per_clip": elapsed / len(rows),
            "sounds": sounds, "lines": lines}


def ordered(results: dict) -> list[str]:
    """Saved runs in MODELS order, runs with another padding (powsm-ft@8s) after their model."""
    out = []
    for m in MODELS:
        out += [k for k in results if k == m or k.startswith(m + "@")]
    return out + [k for k in results if k not in out]


def pct(x: float | None) -> str:
    return "—" if x is None else f"{100 * x:.0f}%"


def table(results: dict) -> str:
    head = ["Model", "PER", "Consonant PER", *KEY_SOUNDS, "Long vowels", "Vowel length", "Frames/s", "s/clip",
            "Fits app?"]
    out = ["| " + " | ".join(head) + " |", "|" + "---|" * len(head)]
    for name in ordered(results):
        r = results[name]
        cells = [r["label"], pct(r["per"]), pct(r["consonant_per"])]
        for s in KEY_SOUNDS:
            info = r["sounds"].get(s)
            if info is None:
                cells.append("—")
            elif info["in_vocab"] is False:
                cells.append("no symbol")
            else:
                cells.append(pct(info["recall"]))
        longs = [v for s, v in r["sounds"].items() if s.endswith("ː")]
        hit = sum(v["recall"] * v["n"] for v in longs)
        cells += [pct(hit / max(1, sum(v["n"] for v in longs))), pct(r["vowel_length_accuracy"]),
                  f"{r['frames_per_second']:.0f}", f"{r['seconds_per_clip']:.2f}", r["fits_app"]]
        out.append("| " + " | ".join(cells) + " |")
    return "\n".join(out)


def confusions(results: dict, sounds: list[str]) -> str:
    out = []
    for name in ordered(results):
        r = results[name]
        parts = []
        for s in sounds:
            info = r["sounds"].get(s)
            if not info:
                continue
            wrong = [f"{h} {c}" for h, c in info["heard_as"].items() if h != s][:3]
            parts.append(f"{s} ({info['n']}): " + (", ".join(wrong) or "—"))
        out.append(f"- **{r['label']}**: " + "; ".join(parts))
    return "\n".join(out)


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    local = {"powsm-ft": "powsm-ft-minerva", "powsm-ft-short": "powsm-ft-shortpad"}
    default = [m for m in MODELS if m not in local or (ROOT / "checkpoints" / local[m] / "powsm_ft.pt").exists()]
    ap.add_argument("--models", default=",".join(default), help=f"comma-separated, from: {', '.join(MODELS)}")
    ap.add_argument("--split", default="dev", choices=["dev", "test"])
    ap.add_argument("--limit", type=int, help="only the first N clips (a quick check)")
    ap.add_argument("--report", action="store_true", help="print the table from the saved JSON, run nothing")
    ap.add_argument("--powsm-pad", type=float, help="seconds the POWSM models pad to (default 20; 0: none). "
                    "A run with it is saved under the model's name plus @<seconds>")
    args = ap.parse_args()
    global POWSM_PAD
    POWSM_PAD = args.powsm_pad

    out_path = DATA / "bench" / f"zero-shot-{args.split}.json"
    results = json.loads(out_path.read_text(encoding="utf-8")) if out_path.exists() else {}
    if not args.report:
        names = [m.strip() for m in args.models.split(",") if m.strip()]
        unknown = [m for m in names if m not in MODELS]
        if unknown:
            sys.exit(f"unknown model(s): {', '.join(unknown)}")
        with open(DATA / f"{args.split}.csv", encoding="utf-8") as f:
            rows = list(csv.DictReader(f))[: args.limit]
        clips = [decode_audio((DATA / r["audio"]).read_bytes()) for r in rows]
        for name in names:
            key = f"{name}@{args.powsm_pad:g}s" if args.powsm_pad is not None and name.startswith("powsm") else name
            results[key] = evaluate(name, rows, clips)
            results[key]["label"] += f" (pad {args.powsm_pad:g} s)" if key != name else ""
            if not args.limit:  # a partial run doesn't overwrite the saved numbers
                out_path.parent.mkdir(parents=True, exist_ok=True)
                out_path.write_text(json.dumps(results, ensure_ascii=False, indent=1), encoding="utf-8")
    print(table(results))
    print()
    print("What each key sound was heard as instead (count):")
    print(confusions(results, ["ħ", "ʕ", "tˤ", "sˤ", "aː", "iː"]))


if __name__ == "__main__":
    main()
