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


def test_migration_deletes_only_successfully_mirrored_supabase_audio():
    mirror, auth = MemoryAudio(), AudioAuth(MemoryAudio())
    auth.audio_store = mirror
    auth.audio_migrate_before = datetime.now(timezone.utc).isoformat()
    old = (datetime.now(timezone.utc) - timedelta(days=8)).isoformat()
    auth.rows["flags"] = [{"audio": "yes.webm", "created_at": old},
                          {"audio": "no.webm", "created_at": old}]
    auth.supabase[("flags", "yes.webm")] = (b"yes", "audio/webm")
    auth.supabase[("flags", "no.webm")] = (b"no", "audio/webm")
    mirror.fail.add("no.webm")
    auth._migrate_legacy_audio()
    assert mirror.files[("flags", "yes.webm")] == b"yes"
    assert ("flags", "yes.webm") not in auth.supabase
    assert auth.supabase[("flags", "no.webm")][0] == b"no"


def test_completed_migration_is_not_scanned_again():
    mirror, auth = MemoryAudio(), AudioAuth(MemoryAudio())
    auth.audio_store = mirror
    auth.audio_migrate_before = "2026-10-17T00:00:00+03:00"
    auth._migrate_legacy_audio()
    auth.rows["flags"] = [{"audio": "late.webm", "created_at": "2026-10-01T00:00:00Z"}]
    auth.supabase[("flags", "late.webm")] = (b"late", "audio/webm")
    auth._migrate_legacy_audio()
    assert ("flags", "late.webm") in auth.supabase


def test_hf_path_keeps_audio_namespaces_separate():
    store = HFAudioStore("owner/private", "/audio/")
    assert store.path("lab", "2026-10/a.wav") == "audio/lab/2026-10/a.wav"
