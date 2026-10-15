#!/usr/bin/env python3
"""Check the phonemes of static/assets/arabic_verbs.json against what the scorer uses.

Usage:  python scripts/compare_verbs.py              # summary + every difference
        python scripts/compare_verbs.py --all        # also list the forms that match
        python scripts/compare_verbs.py --no-vocab   # skip the model-vocab check (no tokenizer download)

For every past / future form (and its accepted_phonemes) it reports:
  bank     the same Arabic word in the phrase bank (phrases.py, workbook LEX, grammar VERBS) and whether its IPA
           agrees: "same" (identical), "same sounds" (identical once archify() symbols and base() are applied,
           i.e. the scorer would build the same target), or "differs"
  archi    how archify() rewrites the form (ق ك ث ذ ظ ج as Q K TH DH ZH J) and the letters it could not place
  vocab    phonemes the model has no symbol for (mapped to a fallback, or not expressible at all)
"""
from __future__ import annotations

import argparse
import json
import os
import re
import sys
from collections import Counter
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from app.archi import archify, split_token, unresolved  # noqa: E402
from app.grammar import VERBS  # noqa: E402
from app.phones import base  # noqa: E402
from app.phrases import PHRASES  # noqa: E402
from app.workbook import LEX  # noqa: E402

VERBS_JSON = ROOT / "static/assets/arabic_verbs.json"
_MARKS = re.compile(r"[ً-ْٰـ]")  # harakat, shadda, sukun, dagger alif, tatweel


def loose(ar: str) -> str:
    """Arabic without diacritics and with the hamza seats of alif folded, for a lookup that tolerates spelling."""
    return _MARKS.sub("", ar).translate(str.maketrans("أإآ", "ااا")).replace("ى", "ي")


def bank_words() -> dict[str, set[str]]:
    """Arabic word -> every IPA the bank gives it (single words of each variant, LEX, grammar VERBS)."""
    out: dict[str, set[str]] = {}
    for p in PHRASES:
        for ar, ipa in p["variants"]:
            ars, ipas = ar.split(), [w.strip() for w in ipa.split("|")]
            if len(ars) == len(ipas):
                for a, i in zip(ars, ipas):
                    out.setdefault(a, set()).add(i)
    for key, (_tr, ipas) in LEX.items():
        out.setdefault(key.partition("#")[0], set()).update(ipas)
    for forms in VERBS.values():
        for _he, _tr, ar, ipas in forms:
            out.setdefault(ar, set()).update(ipas)
    return out


def sounds(ar: str, ipa: str) -> tuple[str, ...]:
    """The target as the scorer sees it: archify() symbols kept, other phonemes reduced to their base() sound."""
    out = []
    for tok in archify(ar, ipa).split():
        if tok == "|":
            continue
        sym, _members, ph = split_token(tok)
        out.append(sym if sym else base(ph))
    return tuple(out)


def load_vocab():
    from huggingface_hub import hf_hub_download

    from app.scorer import Vocab
    model_id = os.environ.get("MODEL_ID", "facebook/wav2vec2-xlsr-53-espeak-cv-ft")
    path = Path(model_id) / "vocab.json" if Path(model_id).is_dir() else hf_hub_download(model_id, "vocab.json")
    return Vocab(json.loads(Path(path).read_text(encoding="utf-8")))


def vocab_issues(vocab, ar: str, ipa: str) -> list[str]:
    issues = []
    for tok in archify(ar, ipa).split():
        before = dict(vocab.downgraded)
        try:
            vocab.token(tok)
        except KeyError as e:
            issues.append(f"{tok}: {e}")
            continue
        issues += [f"{k}->{v}" for k, v in vocab.downgraded.items() if before.get(k) != v]
    return issues


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--all", action="store_true", help="also print forms that match the bank")
    ap.add_argument("--no-vocab", action="store_true", help="skip the model-vocab check")
    args = ap.parse_args()

    data = json.loads(VERBS_JSON.read_text(encoding="utf-8"))
    bank = bank_words()
    bank_loose: dict[str, set[str]] = {}
    for a, ipas in bank.items():
        bank_loose.setdefault(loose(a), set()).update(ipas)
    vocab = None if args.no_vocab else load_vocab()

    tally: Counter[str] = Counter()
    symbols: Counter[str] = Counter()
    for section, entry in data.items():
        for verb in entry["verbs"]:
            for tense in ("past", "future"):
                form = verb[tense]
                ar = form["arabic"]
                for ipa in [form["phonemes"], *form.get("accepted_phonemes", [])]:
                    lines = []
                    for tok in ipa.split():
                        if split_token(tok)[0]:
                            symbols[tok] += 1
                    known = bank.get(ar) or bank_loose.get(loose(ar))
                    if not known:
                        status = "not in bank"
                    elif ipa in known:
                        status = "same"
                    elif sounds(ar, ipa) in {sounds(ar, k) for k in known}:
                        status = "same sounds"
                    else:
                        status = "differs"
                        how = "" if ar in bank else " (matched without diacritics: may be another word)"
                        lines.append(f"    bank:  {' / '.join(sorted(known))}{how}")
                    tally[status] += 1
                    arch = archify(ar, ipa)
                    if arch != ipa:
                        lines.append(f"    archi: {arch}")
                    if missing := unresolved(ar, ipa):
                        tally["unresolved"] += 1
                        lines.append(f"    unresolved letters: {' '.join(l for _, l in missing)}")
                    if vocab is not None and (bad := vocab_issues(vocab, ar, ipa)):
                        tally["vocab"] += 1
                        lines.append(f"    vocab: {', '.join(bad)}")
                    if status in ("differs",) or missing or args.all or (vocab is not None and bad):
                        print(f"[{section}] {verb['hebrew']} {tense} {ar}  {ipa}  -> {status}")
                        print("\n".join(lines)) if lines else None

    print("\n== summary ==")
    for k in ("same", "same sounds", "differs", "not in bank", "unresolved", "vocab"):
        print(f"{k:12} {tally[k]}")
    if symbols:
        print("symbols written in the JSON itself (archify() leaves such a word as it is):",
              ", ".join(f"{s}×{n}" for s, n in symbols.most_common()))


if __name__ == "__main__":
    main()
