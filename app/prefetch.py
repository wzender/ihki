"""Downloads MODEL_ID's files while the Docker image is built, so a cold start doesn't (see the Dockerfile).

    MODEL_ID=powsm-onnx:<repo> python -m app.prefetch     # an exported POWSM (scripts/export_powsm_onnx.py)
    MODEL_ID=facebook/wav2vec2-xlsr-53-espeak-cv-ft python -m app.prefetch

A private repo needs HF_TOKEN (the Dockerfile passes the Space secret of that name to this step only).
"""
from __future__ import annotations

import os
import sys

from .powsm import ONNX_PREFIX, onnx_model_dir, parse_model_id

DEFAULT = "facebook/wav2vec2-xlsr-53-espeak-cv-ft"


def main() -> None:
    model_id = os.environ.get("MODEL_ID", "").strip() or DEFAULT
    if model_id.startswith(ONNX_PREFIX):
        print("exported POWSM:", onnx_model_dir(model_id))
        return
    if parse_model_id(model_id):
        sys.exit(f"MODEL_ID={model_id} needs ESPnet, which the image doesn't have: export it with "
                 f"scripts/export_powsm_onnx.py and use MODEL_ID={ONNX_PREFIX}<repo>")
    from huggingface_hub import hf_hub_download
    from transformers import AutoFeatureExtractor, Wav2Vec2ForCTC
    AutoFeatureExtractor.from_pretrained(model_id)
    Wav2Vec2ForCTC.from_pretrained(model_id)
    hf_hub_download(model_id, "vocab.json")
    print("wav2vec2:", model_id)


if __name__ == "__main__":
    main()
