"""The panel's "מודלים" tab (app/models.py, /api/admin/models): what each model measured, and the developer's switch."""
import time

from fastapi.testclient import TestClient
from test_keystore import AdminAuth, KeyAuth, bearer
from test_scoring import emissions, scorer_for

import app.main
from app.main import create_app
from app.models import CATALOG

BASE = "facebook/wav2vec2-xlsr-53-espeak-cv-ft"
FT = "checkpoints/ihki-ft-minerva"


def client(auth):
    return TestClient(create_app(scorer_for(emissions(["ħ", "aː", "l"])), auth=auth))


def wait_switch(c, headers, seconds=5.0):
    for _ in range(int(seconds / 0.05)):
        data = c.get("/api/admin/models", headers=headers).json()
        if data["switch"]["state"] != "loading":
            return data
        time.sleep(0.05)
    raise AssertionError("the switch never finished")


def test_the_catalog_shows_every_model_with_its_numbers_and_the_active_one(monkeypatch):
    monkeypatch.setattr(app.main, "MODEL_ID", BASE)
    with client(KeyAuth()) as c:
        data = c.get("/api/admin/models", headers=bearer("teacher")).json()
    assert data["active"] == BASE and data["can_switch"] is True
    ids = [m["id"] for m in data["models"]]
    assert ids == [m["id"] for m in CATALOG]
    active = [m for m in data["models"] if m["active"]]
    assert [m["id"] for m in active] == [BASE]
    assert all(0 <= m["metrics"]["per"] <= 1 for m in data["models"])


def test_a_model_set_in_model_id_but_not_in_the_catalog_is_listed_first(monkeypatch):
    monkeypatch.setattr(app.main, "MODEL_ID", "someone/else")
    with client(KeyAuth()) as c:
        data = c.get("/api/admin/models", headers=bearer("teacher")).json()
    assert data["models"][0]["id"] == "someone/else" and data["models"][0]["active"]
    assert data["models"][0]["metrics"] is None


def test_an_admin_sees_the_models_but_cannot_switch(monkeypatch):
    monkeypatch.setattr(app.main, "MODEL_ID", BASE)
    with client(AdminAuth()) as c:
        data = c.get("/api/admin/models", headers=bearer("teacher")).json()
        assert data["can_switch"] is False
        res = c.put("/api/admin/models/active", json={"model_id": FT}, headers=bearer("teacher"))
    assert res.status_code == 403


def test_switching_loads_the_new_model_and_swaps_it_in(monkeypatch, tmp_path):
    monkeypatch.setattr(app.main, "MODEL_ID", BASE)
    monkeypatch.setattr(app.main, "model_availability", lambda model_id: (True, ""))
    loaded = []
    new = scorer_for(emissions(["ħ", "aː", "l"]))
    monkeypatch.setattr(app.main, "load_scorer", lambda model_id: loaded.append(model_id) or new)
    auth = KeyAuth()
    with client(auth) as c:
        res = c.put("/api/admin/models/active", json={"model_id": FT}, headers=bearer("teacher"))
        assert res.status_code == 200
        data = wait_switch(c, bearer("teacher"))
        assert loaded == [FT]
        assert data["active"] == FT and data["switch"]["state"] == "idle"
        assert c.app.state.scorer is new
        assert c.get("/api/health").json()["model"] == FT
    assert any(details == {"model": FT, "action": "switch"} for _, _, details in auth.events)


def test_a_failed_load_keeps_the_running_model_and_says_why(monkeypatch):
    monkeypatch.setattr(app.main, "MODEL_ID", BASE)
    monkeypatch.setattr(app.main, "model_availability", lambda model_id: (True, ""))

    def broken(model_id):
        raise OSError("no such model")
    monkeypatch.setattr(app.main, "load_scorer", broken)
    with client(KeyAuth()) as c:
        before = c.app.state.scorer
        c.put("/api/admin/models/active", json={"model_id": FT}, headers=bearer("teacher"))
        data = wait_switch(c, bearer("teacher"))
        assert data["active"] == BASE and data["switch"]["state"] == "error"
        assert "no such model" in data["switch"]["error"]
        assert c.app.state.scorer is before


def test_unknown_or_unavailable_models_are_refused(monkeypatch):
    monkeypatch.setattr(app.main, "MODEL_ID", BASE)
    monkeypatch.setattr(app.main, "model_availability", lambda model_id: (False, "קובץ המודל לא נמצא בשרת"))
    with client(KeyAuth()) as c:
        assert c.put("/api/admin/models/active", json={"model_id": "evil/model"},
                     headers=bearer("teacher")).status_code == 400
        res = c.put("/api/admin/models/active", json={"model_id": FT}, headers=bearer("teacher"))
        assert res.status_code == 409 and "לא נמצא" in res.json()["detail"]


# ---- the scorer's settings per model ----

class SettingsAuth(KeyAuth):
    def __init__(self, table=True):
        super().__init__()
        self.settings_rows, self.settings_table = {}, table

    def model_settings(self):
        if not self.settings_table:
            raise app.main.AuthUnavailable("Supabase answered 404: relation model_settings does not exist")
        return list(self.settings_rows.values())

    def set_model_settings(self, model, settings, by):
        self.settings_rows[model] = {"model": model, "settings": settings, "updated_by": by,
                                     "updated_at": "2026-10-25T12:00:00+00:00"}


def test_settings_show_each_default_and_the_base_models_built_in_ħ_threshold(monkeypatch):
    monkeypatch.setattr(app.main, "MODEL_ID", BASE)
    with client(SettingsAuth()) as c:
        data = c.get(f"/api/admin/model-settings?model={BASE}", headers=bearer("teacher")).json()
    assert data["stored"] and data["can_edit"] and data["active"]
    flag = next(f for f in data["fields"] if f["key"] == "flag_below")
    assert flag["default"] == 50.0 and flag["value"] == 50.0 and not flag["changed"]
    sounds = {s["sound"]: s for g in data["sounds"] for s in g["sounds"]}
    assert sounds["ħ"]["default"] == 20.0 and sounds["ʕ"]["default"] is None


def test_saving_settings_for_the_active_model_applies_them_at_once(monkeypatch):
    monkeypatch.setattr(app.main, "MODEL_ID", BASE)
    auth = SettingsAuth()
    with client(auth) as c:
        res = c.put("/api/admin/model-settings", headers=bearer("teacher"), json={
            "model": BASE, "settings": {"flag_below": 42, "gop.leniency": 0.5, "flag_below_sound": {"ħ": None, "ʕ": 35}}})
        assert res.status_code == 200, res.text
        cfg = c.app.state.scorer.cfg
        assert cfg.flag_below == 42 and cfg.gop.leniency == 0.5
        assert "ħ" not in cfg.flag_below_sound and cfg.flag_below_sound["ʕ"] == 35
        data = res.json()
        assert next(f for f in data["fields"] if f["key"] == "flag_below")["changed"]
    assert auth.settings_rows[BASE]["settings"]["flag_below"] == 42


def test_settings_for_another_model_wait_until_it_is_switched_on(monkeypatch):
    monkeypatch.setattr(app.main, "MODEL_ID", BASE)
    monkeypatch.setattr(app.main, "model_availability", lambda model_id: (True, ""))
    monkeypatch.setattr(app.main, "load_scorer", lambda model_id: scorer_for(emissions(["ħ", "aː", "l"])))
    with client(SettingsAuth()) as c:
        before = c.app.state.scorer.cfg.flag_below
        c.put("/api/admin/model-settings", headers=bearer("teacher"), json={"model": FT, "settings": {"flag_below": 33}})
        assert c.app.state.scorer.cfg.flag_below == before
        c.put("/api/admin/models/active", json={"model_id": FT}, headers=bearer("teacher"))
        wait_switch(c, bearer("teacher"))
        assert c.app.state.scorer.cfg.flag_below == 33


def test_bad_settings_are_refused_with_the_reason(monkeypatch):
    monkeypatch.setattr(app.main, "MODEL_ID", BASE)
    with client(SettingsAuth()) as c:
        for bad in ({"flag_below": 140}, {"gop.ratio_pool": "median"}, {"nonsense": 1},
                    {"flag_below_sound": {"ħ": -3}}, {"flag_below_sound": {"🙂": 30}}, {"max_issues": 2.5}):
            res = c.put("/api/admin/model-settings", headers=bearer("teacher"), json={"model": BASE, "settings": bad})
            assert res.status_code == 400, bad


def test_without_the_table_settings_are_read_only_and_say_how_to_create_it(monkeypatch):
    monkeypatch.setattr(app.main, "MODEL_ID", BASE)
    with client(SettingsAuth(table=False)) as c:
        data = c.get("/api/admin/model-settings", headers=bearer("teacher")).json()
        assert not data["stored"] and not data["can_edit"] and "create table public.model_settings" in data["create_sql"]
        res = c.put("/api/admin/model-settings", headers=bearer("teacher"), json={"model": BASE, "settings": {}})
        assert res.status_code == 503


def test_an_admin_reads_settings_but_cannot_save_them(monkeypatch):
    monkeypatch.setattr(app.main, "MODEL_ID", BASE)

    class Admin(SettingsAuth):
        developers = frozenset()
    with client(Admin()) as c:
        assert c.get("/api/admin/model-settings", headers=bearer("teacher")).json()["can_edit"] is False
        assert c.put("/api/admin/model-settings", headers=bearer("teacher"),
                     json={"model": BASE, "settings": {}}).status_code == 403


def test_stored_settings_are_applied_when_the_server_starts(monkeypatch):
    monkeypatch.setattr(app.main, "MODEL_ID", BASE)
    auth = SettingsAuth()
    auth.set_model_settings(BASE, {"issue_below": 80}, "dev@x")
    with client(auth) as c:
        assert c.app.state.scorer.cfg.issue_below == 80


# ---- the practice page's model comparison (developers) ----

def a_second_of_audio() -> bytes:
    import io
    import wave
    import numpy as np
    t = np.arange(16000) / 16000
    pcm = (0.3 * np.sin(2 * np.pi * 220 * t) * 32767).astype("<i2").tobytes()
    buf = io.BytesIO()
    with wave.open(buf, "wb") as w:
        w.setnchannels(1); w.setsampwidth(2); w.setframerate(16000); w.writeframes(pcm)
    return buf.getvalue()


def toilet_id():
    from test_scoring import TOILET
    from app.phrases import PHRASES
    return PHRASES.index(TOILET)


def test_a_recording_is_scored_by_every_comparison_model_the_running_one_first(monkeypatch):
    from test_scoring import TOILET  # noqa: F401  w eː n | i l ħ a m m aː m
    monkeypatch.setattr(app.main, "MODEL_ID", BASE)
    monkeypatch.setenv("COMPARE_MODELS", f"{FT},broken/model")
    monkeypatch.setattr(app.main, "model_availability", lambda model_id: (True, ""))
    loads = []

    def load(model_id):
        loads.append(model_id)
        if model_id == "broken/model":
            raise OSError("no such model")
        return scorer_for(emissions(["w", "eː", "n", "i", "l", "χ", "a", "m", "aː", "m"]))   # ħ said as χ
    monkeypatch.setattr(app.main, "load_scorer", load)
    with client(KeyAuth()) as c:
        files = {"audio": ("a.wav", a_second_of_audio(), "audio/wav")}
        res = c.post("/api/admin/compare-models", data={"phrase_id": toilet_id()}, files=files, headers=bearer("teacher"))
        assert res.status_code == 200, res.text
        rows = res.json()["models"]
        assert [r["model"] for r in rows] == [BASE, FT, "broken/model"]
        assert rows[0]["active"] and not rows[1]["active"]
        assert "no such model" in rows[2]["error"] and "score" not in rows[2]
        ft = rows[1]
        assert ft["score"] is not None and any(p["flagged"] and p["ipa"] == "ħ" for w in ft["words"] for p in w["phonemes"])
        # loaded once, kept for the next recording; the broken one is tried again
        c.post("/api/admin/compare-models", data={"phrase_id": toilet_id()}, files=files, headers=bearer("teacher"))
    assert loads.count(FT) == 1 and loads.count("broken/model") == 2


def test_only_developers_compare_models(monkeypatch):
    monkeypatch.setattr(app.main, "MODEL_ID", BASE)
    with client(AdminAuth()) as c:
        res = c.post("/api/admin/compare-models", data={"phrase_id": toilet_id()},
                     files={"audio": ("a.wav", a_second_of_audio(), "audio/wav")}, headers=bearer("teacher"))
    assert res.status_code == 403


def test_quick_lab_item_is_compared_without_a_phrase_id(monkeypatch):
    monkeypatch.setattr(app.main, "MODEL_ID", BASE)
    monkeypatch.setattr(app.main, "model_availability", lambda model_id: True and (True, ""))
    monkeypatch.setattr(app.main, "load_scorer", lambda model_id: scorer_for(emissions(["z", "a", "m", "a", "n"])))
    with client(KeyAuth()) as c:
        files = {"audio": ("a.wav", a_second_of_audio(), "audio/wav")}
        res = c.post("/api/admin/compare-models", data={"item": "quick-zaman"}, files=files,
                     headers=bearer("teacher"))
        assert res.status_code == 200, res.text
        data = res.json()
        assert data["phrase"] == "lab:quick-zaman"
        assert len(data["models"]) == 3
        assert all(row.get("score") is not None for row in data["models"])
        assert c.post("/api/admin/compare-models", data={"item": "quick-nope"}, files=files,
                      headers=bearer("teacher")).status_code == 404
