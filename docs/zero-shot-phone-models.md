# Zero-shot phone models on Palestinian Arabic

Can a newer phone recognizer replace `facebook/wav2vec2-xlsr-53-espeak-cv-ft` as the base model? Every model was run
as-is (no training on our data) on the held-out Minerva lines and compared with the current base and the current
fine-tune (`checkpoints/ihki-ft-minerva`). Measured 2026-10-25 with
[scripts/zero_shot_phone_models.py](../scripts/zero_shot_phone_models.py). Raw results, including every line's
transcript, are saved locally in `data/minerva/bench/zero-shot-{dev,test}.json`.

## Verdict

- **Nothing replaces the fine-tune as-is.** It has the lowest phoneme error rate (23% on test) and the best long-vowel
  recall, and it already fits the app.
- **ZIPA isn't worth pursuing.** It's no better than the current base overall, and it never hears ħ, ʕ or the
  emphatics.
- **POWSM-CTC with the Arabic prompt is the strongest starting point for fine-tuning.** Untrained on our data, it hears
  ħ (87%) and ʕ (85%) about as well as the fine-tune. It's also the only model that hears the emphatics: tˤ 66%,
  sˤ 72%, dˤ 54%. The current vocab has no symbol for tˤ or sˤ, and the fine-tune gets dˤ only 4% of the time.
  Its weak points:
  - It never writes a long vowel: `aː` came out as `a` 235 times out of 268, though its vocab has `aː`.
  - It runs at 25 frames/s, half our 50.
  - It needs a new loader, because it runs on ESPnet rather than Hugging Face `transformers`.

**Next step:** fine-tune POWSM-CTC with the Arabic prompt on `data/minerva` instead of w2v-BERT 2.0. Training on
labels that mark length should teach it the long vowels it currently never writes. Check what 25 frames/s does to the
vowel-length check before moving it into the app.

## Results: test split (255 lines)

| Model | PER | Consonant PER | ħ | ʕ | tˤ | sˤ | dˤ | ʔ | Long vowels | Vowel length | Frames/s | s/clip | Fits app? |
|---|---|---|---|---|---|---|---|---|---|---|---|---|---|
| xlsr-53-espeak (current base) | 38% | 29% | 9% | 2% | no symbol | no symbol | 0% | 3% | 45% | 85% | 50 | 0.05 | yes |
| **ihki-ft-minerva (current fine-tune)** | **23%** | **15%** | **94%** | 69% | no symbol | no symbol | 4% | **72%** | **74%** | **91%** | 50 | 0.05 | yes |
| ZIPA large CR-CTC | 38% | 29% | 0% | 0% | 0% | 0% | 0% | 5% | 39% | 86% | 49 | 0.32 | new loader (ONNX) |
| POWSM-CTC, no language | 43% | 32% | 6% | 1% | 0% | 0% | 0% | 0% | 0% | 81% | 25 | 0.29 | new loader (ESPnet) |
| **POWSM-CTC, Arabic prompt** | 37% | 22% | 87% | **85%** | **66%** | **72%** | **54%** | 62% | 0% | 79% | 25 | 0.29 | new loader (ESPnet) |

The dev split (192 lines) gives the same ranking: fine-tune 26%, POWSM with the Arabic prompt 40%, ZIPA 39%,
base 41%, POWSM without a language 46%. The fine-tune picked its best epoch on dev, so its dev number is slightly
optimistic. That's why test is the headline.

What each column means:

- **PER**: phoneme error rate, i.e. edits needed to turn the model's output into the label, divided by the label's
  length.
- **Consonant PER**: the same with every vowel removed from both. These are the sounds the scorer grades hardest.
- **ħ … ʔ, Long vowels**: how often a labelled sound was heard as itself. "No symbol" means the model's vocab can't
  write it. Long vowels covers `aː iː uː eː oː` together.
- **Vowel length**: of the labelled vowels the model heard as some vowel, how often it got long vs short right.
- **Frames/s**: CTC output rate. The long-vowel check measures vowel length in frames, so fewer frames means coarser
  timing.
- **s/clip**: wall time on the laptop (RTX 1000 Ada, 6 GB). ZIPA runs on CPU (see Setup).

What each sound was heard as instead (test):

| Model | ħ (127) | ʕ (209) | tˤ (62) | aː (268) |
|---|---|---|---|---|
| Current base | h 79, nothing 15, x 8 | nothing 133, aː 25 | t 46 | a 70, nothing 32 |
| Fine-tune | x 5, ʃ 2 | nothing 31, ʔ 6 | t 58 (no symbol) | a 49 |
| ZIPA | h 89, nothing 19 | nothing 128, aː 22 | t 47 | a 72, nothing 28 |
| POWSM, no language | h 88, nothing 10 | nothing 135, a 22 | t 47 | a 127, e 68 |
| POWSM, Arabic prompt | x 6, a 5 | nothing 14, a 8 | t 14 | a 235 |

With the Arabic prompt POWSM also leans toward Standard Arabic: it adds case endings and some MSA forms. For example,
it wrote *bsēder* as *sadari…* and *madrase* as *madrasi(ja)*. Fine-tuning on Palestinian labels should fix that,
but it's worth watching.

## Method

- **Models:**
  - [anyspeech/zipa-large-crctc-500k](https://huggingface.co/anyspeech/zipa-large-crctc-500k): the official ONNX
    export.
  - [espnet/powsm_ctc](https://huggingface.co/espnet/powsm_ctc): run with the phone-recognition task token `<pr>`,
    either with no language (`<unk>`) or with `<ara>`, and padded to 20 s as its model card says.
- **Decoding:** greedy CTC for every model, on audio trimmed with the app's own `decode_audio`.
- **Mapping onto our labels** (`canon()` in the script):
  - Length marks join their vowel, and long consonants count as geminates.
  - Details the labels never write are dropped (dental `t̪`, aspiration).
  - Symbols with no contrast in Palestinian are merged: χ→x, ʁ→ɣ, ɾ→r, ɡ→g.
  - Vowel qualities fold to `a e i o u` (ɛ→e, ɪ→i, ə→e, ʊ→u).
  - Geminates collapse to one sound on both sides, as the scorer's `tokenize()` does.
- **Fairness caveat:** the current base and the fine-tune have no symbols for tˤ, sˤ or ðˤ. Training mapped them to
  t, s and z. Their PER counts those as errors, which costs the fine-tune a few points. The per-sound columns show it
  directly.

## Setup notes

ZIPA and POWSM need packages that conflict with the app's environment. They install into a git-ignored
`.zero-shot-deps/` folder (`scripts/setup_zero_shot_deps.sh`, pinned in `requirements-zero-shot.txt`), and only this
script uses it.

POWSM-CTC doesn't run on the newest ESPnet release for Python 3.10 (202511) as published. The script patches that
release in memory, with two fixes:

1. The encoder passes the language/task prompt to its subsampling layer, which doesn't accept it in that release. The
   patch copies ESPnet master's logic.
2. The checkpoint names that layer's weights differently from what the release expects, so the release silently leaves
   the layer randomly initialised. Before this fix the model output junk. The script copies those weights in by hand.

ESPnet also imports `torchaudio`, which has no build for torch 2.14. It's only used for multi-microphone beamforming
code that POWSM never runs, so the setup script installs a placeholder that fails loudly if anything calls it.

ZIPA runs on CPU, because onnxruntime's GPU build needs CUDA 12 and this machine has CUDA 13. At about 0.3 s per clip
that's fine for evaluation.

Rerun:

```bash
scripts/setup_zero_shot_deps.sh                                   # once
python scripts/zero_shot_phone_models.py --split test             # all models; or --models powsm-ara,ft
python scripts/zero_shot_phone_models.py --split test --report    # just the table
```
