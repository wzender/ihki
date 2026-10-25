"""The exported POWSM (app/powsm.py PowsmOnnxEmitter): what the Space runs with onnxruntime alone.
The real model is 1.6 GB, so onnxruntime's session is replaced by one that answers with known log-posteriors;
scripts/export_powsm_onnx.py --check compares the real export with ESPnet frame by frame."""
import json

import numpy as np
import pytest

import app.powsm as powsm
from app.models import availability


def export(tmp_path, pad=0.0):
    folder = tmp_path / "export"
    folder.mkdir()
    meta = {"kind": "powsm-ctc-onnx", "lang": "<ara>", "blank": 0, "vocab": {"<blank>": 0, "a": 1, "ħ": 2},
            "pad_seconds": pad, "frames_per_second": 25, "prompt_positions": 2,
            "files": {"fp32": "model.onnx", "int8": "model.int8.onnx"}}
    (folder / "powsm_onnx.json").write_text(json.dumps(meta), encoding="utf-8")
    rng = np.random.default_rng(0)
    np.savez(folder / "frontend.npz", window=np.hanning(400).astype(np.float32),
             melmat=rng.random((257, 80)).astype(np.float32), mean=np.zeros(80, np.float32),
             std=np.ones(80, np.float32), n_fft=512, win_length=400, hop_length=160)
    for name in ("model.onnx", "model.int8.onnx"):
        (folder / name).write_bytes(b"")
    return folder


class FakeSession:
    """Like the export: 4x fewer frames than feats, log-posteriors over the 3 vocab columns."""
    seen: list = []

    def __init__(self, path, opts=None, providers=None):
        self.path = path
        FakeSession.seen.append(path)

    def run(self, outputs, inputs):
        feats = inputs["feats"]
        assert feats.shape[0] == 1 and feats.shape[2] == 80 and inputs["feats_lens"][0] == feats.shape[1]
        frames = feats.shape[1] // 4
        logp = np.log(np.full((1, frames, 3), 1 / 3, dtype=np.float32))
        return [logp]


@pytest.fixture
def fake_ort(monkeypatch):
    import onnxruntime
    monkeypatch.setattr(onnxruntime, "InferenceSession", FakeSession)
    monkeypatch.delenv("POWSM_PAD_SECONDS", raising=False)
    monkeypatch.delenv("POWSM_ONNX_PRECISION", raising=False)
    FakeSession.seen = []


def test_an_unpadded_export_gives_the_scorer_50_frames_a_second(tmp_path, fake_ort):
    em = powsm.PowsmOnnxEmitter(str(export(tmp_path, pad=0.0)))
    assert em.pad == 0.0 and em.token_to_id["ħ"] == 2 and em.blank_token == "<blank>"
    logp = em(np.zeros(16000 * 2, dtype=np.float32))
    stft_frames = 1 + 2 * 16000 // 160                # a centred STFT: one frame every 10 ms, plus one
    assert logp.shape == ((stft_frames // 4) * 2, 3)  # POWSM's 25 frames/s, each repeated once
    assert np.allclose(np.exp(logp).sum(-1), 1)


def test_padding_keeps_only_the_frames_that_cover_the_speech(tmp_path, fake_ort):
    em = powsm.PowsmOnnxEmitter(str(export(tmp_path, pad=8.0)))
    one_second = em(np.zeros(16000, dtype=np.float32))
    padded_frames = (1 + 8 * 16000 // 160) // 4
    assert one_second.shape[0] == round(padded_frames / 8) * 2


def test_pad_seconds_from_the_environment_wins(tmp_path, fake_ort, monkeypatch):
    monkeypatch.setenv("POWSM_PAD_SECONDS", "4")
    assert powsm.PowsmOnnxEmitter(str(export(tmp_path, pad=0.0))).pad == 4.0


def test_fp32_is_the_default_and_int8_is_one_setting_away(tmp_path, fake_ort, monkeypatch):
    folder = export(tmp_path)
    assert powsm.PowsmOnnxEmitter(str(folder)).weights == "model.onnx"
    monkeypatch.setenv("POWSM_ONNX_PRECISION", "int8")
    assert powsm.PowsmOnnxEmitter(str(folder)).weights == "model.int8.onnx"


def test_model_ids_that_name_an_export(tmp_path, monkeypatch):
    folder = export(tmp_path)
    assert powsm.onnx_model_dir(str(folder)) == folder
    assert powsm.onnx_model_dir("facebook/wav2vec2-xlsr-53-espeak-cv-ft") is None
    assert powsm.onnx_model_dir("checkpoints/powsm-ft-shortpad/powsm_ft.pt") is None
    fetched = []
    import huggingface_hub
    monkeypatch.setattr(huggingface_hub, "snapshot_download",
                        lambda repo, allow_patterns=None: fetched.append((repo, allow_patterns)) or str(folder))
    assert powsm.onnx_model_dir("powsm-onnx:someone/powsm") == folder
    repo, patterns = fetched[0]
    assert repo == "someone/powsm" and "model.onnx" in patterns and "model.int8.onnx" not in patterns


def test_the_image_build_refuses_a_model_that_needs_espnet(monkeypatch):
    from app import prefetch
    monkeypatch.setenv("MODEL_ID", "checkpoints/powsm-ft-shortpad/powsm_ft.pt")
    with pytest.raises(SystemExit) as e:
        prefetch.main()
    assert "export_powsm_onnx" in str(e.value)


def test_the_models_tab_offers_an_export_when_onnxruntime_is_there(tmp_path):
    assert availability(f"{powsm.ONNX_PREFIX}someone/powsm") == (True, "")
    assert availability(str(export(tmp_path))) == (True, "")
    ok, why = availability("exports/not-there")
    assert not ok and "ONNX" in why
