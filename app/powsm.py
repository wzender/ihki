"""POWSM-CTC (espnet/powsm_ctc) as the app's phoneme model: an Emitter like HFEmitter.

Serve a fine-tune with MODEL_ID=checkpoints/powsm-ft-minerva/powsm_ft.pt (train/finetune_powsm.py), or the
published model with MODEL_ID=powsm:<ara>. It needs the extra packages from scripts/setup_zero_shot_deps.sh
(.zero-shot-deps/), which are not in requirements.txt: this is for trying the model locally, not yet for the Space.

What the adapter changes so the scorer can read POWSM's output unchanged:
- columns: only POWSM's phone tokens (6,405 of 40,002; the rest are text pieces and task tags) and the blank,
  renormalised. Phone names lose their slashes and tie bars (/d͡ʒ/ -> dʒ), the spelling the scorer's rules use.
- frames: the language/task prompt's two positions and the padding after the speech are dropped, and every frame
  is repeated once. POWSM runs at 25 frames/s; the scorer's frame counts (vowel length, alignment padding) were
  tuned at wav2vec2's 50.
"""
from __future__ import annotations

import glob
import os
import sys
from pathlib import Path

import numpy as np

ROOT = Path(__file__).resolve().parent.parent
DEPS = ROOT / ".zero-shot-deps"
SAMPLE_RATE = 16_000
# POWSM was trained on 20 s inputs (its model card says pad to 20 s) and loses accuracy without padding (test PER
# 16% -> 21%). 8 s keeps it (16.2%) at 1.8 s a recording on 2 CPU threads instead of 4.4 s. A fine-tune trained on
# random padding (train/finetune_powsm.py --pad random) needs none: unpadded it scores 16.2% at ~0.8 s.
# POWSM_PAD_SECONDS, when set, overrides both (0: no padding).
SECONDS = 8.0


def pad_seconds(checkpoint: str | None) -> float:
    """The padding a model runs with: POWSM_PAD_SECONDS if set, none for a short-padding fine-tune, else SECONDS."""
    env = os.environ.get("POWSM_PAD_SECONDS", "").strip()
    if env:
        return float(env)
    if checkpoint:
        import torch
        args = torch.load(checkpoint, map_location="cpu").get("args") or {}
        if args.get("pad") == "random":
            return 0.0
    return SECONDS
PROMPT = 2       # the language and task tokens the encoder prepends to its output
UPSAMPLE = 2     # 25 frames/s -> the 50 the scorer was tuned at


def use_extra_deps() -> None:
    """Make .zero-shot-deps importable, after the app's own packages so its torch/numpy always win."""
    if not (DEPS / "pkgs").exists():
        raise RuntimeError("POWSM needs extra packages: run scripts/setup_zero_shot_deps.sh first")
    for sub in ("pkgs", "stubs"):
        if str(DEPS / sub) not in sys.path:
            sys.path.append(str(DEPS / sub))


def patch_espnet_for_powsm_ctc() -> None:
    """ESPnet 202511 (the newest release for Python 3.10) can't run POWSM-CTC as published: the encoder hands
    Conv2dSubsampling the language/task prompt, which that release's layer doesn't accept. Same logic as ESPnet
    master: linear -> prepend the prompt embeddings -> positional encoding."""
    import torch
    from espnet.nets.pytorch_backend.transformer import subsampling

    def forward(self, x, x_mask, prefix_embeds=None):
        x = self.conv(x.unsqueeze(1))
        b, c, t, f = x.size()
        linear, pos_enc = self.out[0], self.out[1]
        x = linear(x.transpose(1, 2).contiguous().view(b, t, c * f))
        if x_mask is not None:
            x_mask = x_mask[:, :, :-2:2][:, :, :-2:2]
        if prefix_embeds is not None:
            x = torch.cat([prefix_embeds, x], dim=1)
            if x_mask is not None:
                ones = torch.ones(x_mask.shape[0], 1, prefix_embeds.size(1), dtype=x_mask.dtype, device=x_mask.device)
                x_mask = torch.cat([ones, x_mask], dim=-1)
        return pos_enc(x), x_mask

    subsampling.Conv2dSubsampling.forward = forward


def load_powsm(lang: str = "<ara>", checkpoint: str | None = None, device: str | None = None):
    """espnet/powsm_ctc as ESPnet's greedy-CTC Speech2Text, with an optional fine-tune's weights on top."""
    use_extra_deps()
    import torch
    from huggingface_hub import snapshot_download
    patch_espnet_for_powsm_ctc()
    from espnet2.bin.s2t_inference_ctc import Speech2TextGreedySearch
    device = device or ("cuda" if torch.cuda.is_available() else "cpu")
    s2t = Speech2TextGreedySearch.from_pretrained("espnet/powsm_ctc", device=device, lang_sym=lang, task_sym="<pr>")
    model = s2t.s2t_model
    # The checkpoint stores the subsampling linear layer as `embed.out.*` (master's layout); the release expects
    # `embed.out.0.*` and silently leaves it random. Copy it in.
    ckpt = glob.glob(f"{snapshot_download('espnet/powsm_ctc')}/exp/s2t_train*/*.pth")[0]
    state = torch.load(ckpt, map_location="cpu")
    lin = model.encoder.embed.out[0]
    with torch.no_grad():
        lin.weight.copy_(state["encoder.embed.out.weight"])
        lin.bias.copy_(state["encoder.embed.out.bias"])
    if checkpoint:  # train/finetune_powsm.py saves only the weights it trained
        saved = torch.load(checkpoint, map_location="cpu")
        missing = set(saved["state"]) - set(model.state_dict())
        if missing:
            raise KeyError(f"{checkpoint} has weights this model lacks: {sorted(missing)[:5]}")
        model.load_state_dict(saved["state"], strict=False)
    model.eval()
    return s2t


def parse_model_id(model_id: str) -> tuple[str, str | None] | None:
    """(language token, checkpoint) when MODEL_ID names a POWSM model, else None.
    'powsm' or 'powsm:<ara>' is the published model; a path to a .pt file is a fine-tune (always <ara>)."""
    if model_id == "powsm" or model_id.startswith("powsm:"):
        return (model_id.split(":", 1)[1] if ":" in model_id else "<ara>"), None
    if model_id.endswith(".pt"):
        return "<ara>", model_id
    return None


class PowsmEmitter:
    """POWSM-CTC -> (T, V) log-posteriors over its phone tokens, at 50 frames/s (see the module docstring)."""

    def __init__(self, lang: str = "<ara>", checkpoint: str | None = None, device: str | None = None):
        import torch
        self.torch = torch
        self.s2t = load_powsm(lang, checkpoint, device)
        self.pad = pad_seconds(checkpoint)
        model = self.s2t.s2t_model
        tokens = model.token_list
        cols, names = [model.blank_id], {"<blank>": 0}
        for i, t in enumerate(tokens):
            if len(t) > 2 and t[0] == "/" and t[-1] == "/":
                name = t[1:-1].replace("͡", "")
                if name not in names:  # a tie-barred spelling that collides with a plain one keeps the plain one
                    names[name] = len(cols)
                    cols.append(i)
        self.cols = torch.tensor(cols)
        self.token_to_id: dict[str, int] = names
        self.blank_token = "<blank>"
        self.lang_id = self.s2t.converter.token2id[lang]
        self.task_id = self.s2t.converter.token2id["<pr>"]

    def __call__(self, wav: np.ndarray) -> np.ndarray:
        torch = self.torch
        model, device = self.s2t.s2t_model, self.s2t.device
        n = int(self.pad * SAMPLE_RATE)
        x = np.pad(wav, (0, max(0, n - len(wav)))).astype(np.float32)  # longer recordings run as they are
        speech = torch.from_numpy(x)[None].to(device)
        prefix = torch.tensor([[self.lang_id, self.task_id]], device=device)
        prev = torch.tensor([[model.na]], device=device)
        with torch.inference_mode():
            enc, _ = model.encode(speech, torch.tensor([speech.shape[1]], device=device), prev,
                                  torch.tensor([1], device=device), prefix, torch.tensor([2], device=device))
            if isinstance(enc, tuple):
                enc = enc[0]
            enc = enc[0, PROMPT:]
            keep = max(1, round(enc.shape[0] * len(wav) / len(x)))  # frames that cover the speech
            logits = model.ctc.ctc_lo(enc[:keep].float())[:, self.cols.to(device)]
            logp = torch.log_softmax(logits, -1).cpu().numpy()
        return np.repeat(logp, UPSAMPLE, axis=0)


# ---- the exported model (scripts/export_powsm_onnx.py): what the Space runs, with onnxruntime and torch only ----

ONNX_PREFIX = "powsm-onnx:"


def onnx_model_dir(model_id: str, download: bool = True) -> Path | None:
    """The folder of an exported POWSM when MODEL_ID names one, else None: powsm-onnx:<Hub repo> (downloaded once,
    then from the Hugging Face cache) or a local folder with powsm_onnx.json."""
    if model_id.startswith(ONNX_PREFIX):
        repo = model_id[len(ONNX_PREFIX):]
        if not download:
            return Path(repo)
        from huggingface_hub import snapshot_download
        return Path(snapshot_download(repo, allow_patterns=["powsm_onnx.json", "frontend.npz", onnx_precision_file()]))
    local = Path(model_id)
    return local if (local / "powsm_onnx.json").is_file() else None


def onnx_precision_file(meta: dict | None = None) -> str:
    """The weights file POWSM_ONNX_PRECISION picks: fp32 (the default) or int8. On the Minerva test lines, 2 CPU
    threads: fp32 PER 16.5% at 0.57 s a recording; int8 16.6% at 0.38 s, but ط 81% -> 76% and ص 68% -> 62%."""
    precision = os.environ.get("POWSM_ONNX_PRECISION", "fp32").strip().lower() or "fp32"
    files = (meta or {}).get("files") or {"fp32": "model.onnx", "int8": "model.int8.onnx"}
    return files.get(precision) or files["fp32"]


class PowsmOnnxEmitter:
    """An exported POWSM-CTC -> (T, V) log-posteriors over its phone tokens at 50 frames/s, like PowsmEmitter but
    without ESPnet. The front end (STFT, mel, log, global mean/variance) runs in torch from frontend.npz; the encoder
    and CTC head in onnxruntime. The prompt (<ara>, <pr>) is baked into the export."""

    def __init__(self, folder: str, precision: str | None = None):
        import json

        import onnxruntime as ort
        import torch
        self.torch, self.folder = torch, Path(folder)
        self.meta = json.loads((self.folder / "powsm_onnx.json").read_text(encoding="utf-8"))
        weights = (self.meta["files"].get(precision) or self.meta["files"]["fp32"]) if precision \
            else onnx_precision_file(self.meta)
        opts = ort.SessionOptions()
        opts.graph_optimization_level = ort.GraphOptimizationLevel.ORT_ENABLE_ALL
        # a container can see more cores than it may use (the free Space has 2): POWSM_ONNX_THREADS caps them
        threads = int(os.environ.get("POWSM_ONNX_THREADS", "0") or 0)
        if threads > 0:
            opts.intra_op_num_threads, opts.inter_op_num_threads = threads, 1
        self.session = ort.InferenceSession(str(self.folder / weights), opts, providers=["CPUExecutionProvider"])
        fe = np.load(self.folder / "frontend.npz")
        self.window = torch.from_numpy(fe["window"].astype(np.float32))
        self.melmat = torch.from_numpy(fe["melmat"].astype(np.float32))
        self.mean = torch.from_numpy(fe["mean"].astype(np.float32))
        self.std = torch.from_numpy(fe["std"].astype(np.float32))
        self.n_fft, self.win, self.hop = int(fe["n_fft"]), int(fe["win_length"]), int(fe["hop_length"])
        self.token_to_id: dict[str, int] = self.meta["vocab"]
        self.blank_token = "<blank>"
        env = os.environ.get("POWSM_PAD_SECONDS", "").strip()
        self.pad = float(env) if env else float(self.meta.get("pad_seconds", SECONDS))
        self.weights = weights

    def features(self, x: np.ndarray):
        """ESPnet's DefaultFrontend + GlobalMVN: centred STFT, power, 80 mel bands, natural log, (x - mean) / std."""
        torch = self.torch
        spec = torch.stft(torch.from_numpy(x), self.n_fft, hop_length=self.hop, win_length=self.win,
                          window=self.window, center=True, pad_mode="reflect", normalized=False, onesided=True,
                          return_complex=True)
        power = spec.real ** 2 + spec.imag ** 2                       # (F, T)
        mel = torch.clamp(power.transpose(0, 1) @ self.melmat, min=1e-10).log()
        return ((mel - self.mean) / self.std)[None].numpy()

    def __call__(self, wav: np.ndarray) -> np.ndarray:
        n = int(self.pad * SAMPLE_RATE)
        x = np.pad(wav, (0, max(0, n - len(wav)))).astype(np.float32)  # a longer recording runs as it is
        feats = self.features(x)
        logp = self.session.run(None, {"feats": feats, "feats_lens": np.array([feats.shape[1]], dtype=np.int64)})[0][0]
        keep = max(1, round(logp.shape[0] * len(wav) / len(x)))      # frames that cover the speech
        return np.repeat(logp[:keep], UPSAMPLE, axis=0)
