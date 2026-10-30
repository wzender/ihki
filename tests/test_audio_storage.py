from datetime import datetime, timedelta, timezone

from app.auth import AuthUnavailable, HFAudioStore, SupabaseAuth


class MemoryAudio:
    def __init__(self):
        self.files = {}
        self.fail = set()

    def put(self, bucket, name, data):
        if name in self.fail:
            raise OSError("bucket unavailable")
        self.files[(bucket, name)] = data

    def get(self, bucket, name):
        return self.files[(bucket, name)]

    def delete(self, bucket, names):
        for name in names:
            self.files.pop((bucket, name), None)


class AudioAuth(SupabaseAuth):
    def __init__(self, mirror):
        super().__init__("https://p.supabase.co", "key", secret="secret", audio_store=mirror)
        self.supabase = {}
        self.rows = {"flags": [], "lab_samples": []}
        self.deleted = []

    def _put_object(self, bucket, name, data, mime):
        self.supabase[(bucket, name)] = (data, mime)
        return True

    def _get_object(self, bucket, name):
        try:
            return self.supabase[(bucket, name)]
        except KeyError:
            raise AuthUnavailable("missing")

    def _delete_objects(self, bucket, names):
        self.deleted += [(bucket, name) for name in names]
        for name in names:
            self.supabase.pop((bucket, name), None)
        return True

    def _admin(self, path, method="GET", body=None, prefer="return=representation", timeout=10):
        if path.startswith("/storage/v1/object/list/"):   # one folder level, as Supabase lists it
            bucket, prefix = path.rsplit("/", 1)[1], body["prefix"]
            names = [n[len(prefix):] for b, n in self.supabase if b == bucket and n.startswith(prefix)]
            items = sorted({n.split("/")[0] + ("/" if "/" in n else "") for n in names})
            page = items[body["offset"]:body["offset"] + body["limit"]]
            return [{"name": i.rstrip("/"), "id": None if i.endswith("/") else i} for i in page]
        table = "flags" if "/flags?" in path else "lab_samples"
        return self.rows[table]


def test_new_audio_is_written_only_to_hf():
    mirror, auth = MemoryAudio(), AudioAuth(MemoryAudio())
    auth.audio_store = mirror
    assert auth._put_audio("flags", "2026-10/a.webm", b"voice", "audio/webm")
    assert mirror.files[("flags", "2026-10/a.webm")] == b"voice"
    assert ("flags", "2026-10/a.webm") not in auth.supabase


def test_audio_prefers_hf_and_falls_back_to_supabase():
    mirror, auth = MemoryAudio(), AudioAuth(MemoryAudio())
    auth.audio_store = mirror
    mirror.files[("flags", "old.webm")] = b"hf-old"
    auth.supabase[("flags", "old.webm")] = (b"supa-old", "audio/webm")
    old = (datetime.now(timezone.utc) - timedelta(days=8)).isoformat()
    assert auth._get_audio("flags", "old.webm", old)[0] == b"hf-old"
    auth.supabase[("flags", "legacy.webm")] = (b"legacy", "audio/webm")
    assert auth._get_audio("flags", "legacy.webm", datetime.now(timezone.utc).isoformat())[0] == b"legacy"


def test_migration_moves_every_supabase_recording_and_keeps_only_failed_ones():
    mirror, auth = MemoryAudio(), AudioAuth(MemoryAudio())
    auth.audio_store = mirror
    auth.supabase[("flags", "2026-09/yes.webm")] = (b"yes", "audio/webm")
    auth.supabase[("flags", "2026-10/new.webm")] = (b"new", "audio/webm")   # recent ones move too
    auth.supabase[("flags", "no.webm")] = (b"no", "audio/webm")
    auth.supabase[("lab", "a.wav")] = (b"lab", "audio/wav")
    mirror.fail.add("no.webm")
    auth._migrate_legacy_audio()
    assert mirror.files[("flags", "2026-09/yes.webm")] == b"yes" and mirror.files[("flags", "2026-10/new.webm")] == b"new"
    assert mirror.files[("lab", "a.wav")] == b"lab"
    assert set(auth.supabase) == {("flags", "no.webm")}   # only the one HF refused stays, for the next startup
    mirror.fail.clear()
    auth._migrate_legacy_audio()
    assert not auth.supabase and mirror.files[("flags", "no.webm")] == b"no"


def test_recordings_default_to_the_tts_bucket(monkeypatch):
    from app.auth import load_auth
    monkeypatch.setenv("SUPABASE_URL", "https://p.supabase.co")
    monkeypatch.setenv("SUPABASE_PUBLISHABLE_KEY", "key")
    monkeypatch.delenv("AUDIO_BUCKET", raising=False)
    monkeypatch.setenv("TTS_BUCKET", "owner/storage")
    assert load_auth().audio_store.bucket_id == "owner/storage"
    monkeypatch.setenv("AUDIO_BUCKET", "owner/audio")
    assert load_auth().audio_store.bucket_id == "owner/audio"
    monkeypatch.setenv("AUDIO_BUCKET", "supabase")
    assert load_auth().audio_store is None


def test_hf_path_keeps_audio_namespaces_separate():
    store = HFAudioStore("owner/private", "/audio/")
    assert store.path("lab", "2026-10/a.wav") == "audio/lab/2026-10/a.wav"
