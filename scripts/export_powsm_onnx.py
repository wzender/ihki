#!/usr/bin/env python3
"""Export a POWSM-CTC fine-tune to ONNX, so the Space can run it with onnxruntime alone (no ESPnet).

Usage:  python scripts/export_powsm_onnx.py checkpoints/powsm-ft-shortpad/powsm_ft.pt exports/powsm-ft-shortpad
        python scripts/export_powsm_onnx.py <checkpoint> <out> --check data/minerva/dev.csv   # compare with ESPnet

Needs the export-time packages (scripts/setup_zero_shot_deps.sh). Writes to <out>:
  model.onnx        encoder + CTC head for one recording: feats (1, T, 80) and their length -> log-posteriors
                    (1, T', V) over POWSM's phone tokens only. The prompt is baked in: <ara>, <pr>, no previous
                    text, so its two positions are already dropped from T'.
  model.int8.onnx   the same with int8 weights (dynamic quantisation): ~4x smaller, faster on a CPU
  frontend.npz      what turns 16 kHz audio into feats, copied from the model: STFT window, mel matrix,
                    global mean and std (app/powsm.py PowsmOnnxEmitter applies them with torch.stft)
  powsm_onnx.json   vocab (phone -> column), blank, padding, frame rate and where it came from
Upload <out> to a Hub model repo and serve it with MODEL_ID=powsm-onnx:<repo> (or MODEL_ID=<out> locally).
"""
from __future__ import annotations

import argparse
import csv
import json
import sys
import time
from pathlib import Path

import numpy as np

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from app.powsm import PROMPT, load_powsm, pad_seconds, use_extra_deps  # noqa: E402


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("checkpoint", help="a train/finetune_powsm.py checkpoint (powsm_ft.pt)")
    ap.add_argument("out", type=Path)
    ap.add_argument("--lang", default="<ara>")
    ap.add_argument("--check", type=Path, help="a CSV whose first clips are scored both ways and compared")
    ap.add_argument("--check-n", type=int, default=20)
    ap.add_argument("--no-int8", action="store_true", help="skip the int8 copy")
    args = ap.parse_args()
    use_extra_deps()
    import torch

    s2t = load_powsm(args.lang, args.checkpoint, device="cpu")
    model = s2t.s2t_model.eval()
    conv = s2t.converter
    lang_id, task_id = conv.token2id[args.lang], conv.token2id["<pr>"]

    # the phone columns, named as the app's scorer spells them (no slashes, no tie bars)
    cols, names = [model.blank_id], {"<blank>": 0}
    for i, t in enumerate(model.token_list):
        if len(t) > 2 and t[0] == "/" and t[-1] == "/":
            name = t[1:-1].replace("͡", "")
            if name not in names:
                names[name] = len(cols)
                cols.append(i)

    class EncoderCTC(torch.nn.Module):
        """POWSM's encoder and CTC head with the prompt fixed: (feats, lengths) -> log-posteriors over phones."""

        def __init__(self):
            super().__init__()
            self.encoder, self.ctc = model.encoder, model.ctc
            with torch.no_grad():
                prefix = torch.tensor([[lang_id, task_id]])
                prev = torch.tensor([[model.na]])
                memory, mem_lens, _ = model.prompt_encoder(model.pos_enc(model.embed(prev)), torch.tensor([1]))
                self.register_buffer("prefix", model.embed_proj(model.embed(prefix)))
                self.register_buffer("memory", model.prompt_proj(memory))
                self.register_buffer("memory_mask", torch.ones(1, 1, memory.shape[1], dtype=torch.bool))
                self.register_buffer("cols", torch.tensor(cols))

        def forward(self, feats, feats_lens):
            enc, _, _ = self.encoder(feats, feats_lens, ctc=self.ctc, prefix_embeds=self.prefix,
                                     memory=self.memory, memory_mask=self.memory_mask)
            if isinstance(enc, tuple):
                enc = enc[0]
            logits = self.ctc.ctc_lo(enc[:, PROMPT:])
            return torch.log_softmax(logits.index_select(-1, self.cols), dim=-1)

    def features(wav: np.ndarray):
        x = torch.from_numpy(wav.astype(np.float32))[None]
        feats, lens = model.frontend(x, torch.tensor([x.shape[1]]))
        return model.normalize(feats, lens)

    net = EncoderCTC().eval()
    args.out.mkdir(parents=True, exist_ok=True)
    sample = (np.random.default_rng(0).standard_normal(16000 * 3) * 0.05).astype(np.float32)
    feats, lens = features(sample)
    onnx_path = args.out / "model.onnx"
    print("exporting", onnx_path, flush=True)
    with torch.no_grad():
        torch.onnx.export(net, (feats, lens), str(onnx_path), input_names=["feats", "feats_lens"],
                          output_names=["logp"], dynamic_axes={"feats": {1: "T"}, "logp": {1: "frames"}},
                          opset_version=17, dynamo=False)
    if not args.no_int8:
        from onnxruntime.quantization import QuantType, quantize_dynamic
        print("quantising to int8", flush=True)
        # only the matrix products: onnxruntime's CPU kernels have no int8 convolution (ConvInteger)
        quantize_dynamic(str(onnx_path), str(args.out / "model.int8.onnx"), weight_type=QuantType.QInt8,
                         op_types_to_quantize=["MatMul", "Gemm"])

    fe = model.frontend
    stft = fe.stft
    window = torch.hann_window(stft.win_length).numpy()
    np.savez(args.out / "frontend.npz", window=window, melmat=fe.logmel.melmat.numpy(),
             mean=model.normalize.mean.numpy(), std=model.normalize.std.numpy(),
             n_fft=stft.n_fft, win_length=stft.win_length, hop_length=stft.hop_length)
    assert stft.center and stft.window == "hann" and fe.logmel.log_base is None
    meta = {"kind": "powsm-ctc-onnx", "lang": args.lang, "blank": 0, "vocab": names,
            "pad_seconds": pad_seconds(args.checkpoint), "frames_per_second": 25, "prompt_positions": PROMPT,
            "source": {"base": "espnet/powsm_ctc", "checkpoint": str(args.checkpoint)},
            "files": {"fp32": "model.onnx", "int8": None if args.no_int8 else "model.int8.onnx"}}
    (args.out / "powsm_onnx.json").write_text(json.dumps(meta, ensure_ascii=False, indent=1), encoding="utf-8")
    print("wrote", sorted(p.name for p in args.out.iterdir()), flush=True)

    if args.check:
        check(args, net, features)


def check(args, net, features) -> None:
    """ESPnet's own model vs the app's ONNX emitter (fp32 and int8) on real clips: same frames, same argmax?"""
    import torch

    from app.powsm import PowsmOnnxEmitter
    from app.scorer import decode_audio
    rows = list(csv.DictReader(open(args.check, encoding="utf-8")))[: args.check_n]
    clips = [decode_audio((args.check.parent / r["audio"]).read_bytes()) for r in rows]
    for kind in ("fp32", "int8"):
        if kind == "int8" and args.no_int8:
            continue
        em = PowsmOnnxEmitter(str(args.out), precision=kind)
        diffs, agree, total, t = [], 0, 0, 0.0
        for wav in clips:
            x = np.pad(wav, (0, max(0, int(em.pad * 16000) - len(wav)))).astype(np.float32)
            with torch.no_grad():
                f, ln = features(x)
                ref = net(f, ln)[0].numpy()
            keep = max(1, round(ref.shape[0] * len(wav) / len(x)))
            ref = ref[:keep]
            t0 = time.perf_counter()
            got = em(wav)[::2]  # the emitter repeats each frame for the scorer's 50 frames/s
            t += time.perf_counter() - t0
            n = min(len(ref), len(got))
            diffs.append(float(np.abs(np.exp(ref[:n]) - np.exp(got[:n])).max()))
            agree += int((ref[:n].argmax(-1) == got[:n].argmax(-1)).sum())
            total += n
        print(f"{kind}: frames whose best phone matches ESPnet {100 * agree / total:.2f}% · "
              f"largest posterior difference {max(diffs):.4f} · {t / len(clips):.2f} s/clip", flush=True)


if __name__ == "__main__":
    main()
