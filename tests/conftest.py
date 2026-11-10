"""Tests run without sign-in whatever the local .env says: set before app.main loads it."""
import os

os.environ["SUPABASE_URL"] = ""
os.environ["HEBREW_NIQQUD"] = "0"   # no calls to Dicta's Nakdan; test_nakdan.py fakes it
# no logs, traces or page monitoring sent to Better Stack: a test's planned failure is not a production error
for key in ("BETTER_STACK_SOURCE_TOKEN", "BETTER_STACK_INGESTING_HOST", "BETTER_STACK_RUM_TOKEN"):
    os.environ[key] = ""
# no paid TTS synthesis and no uploads to the Hugging Face buckets from a test: the native baseline and the TTS cache
# would otherwise call Sawtak and write to TTS_BUCKET with the developer's .env
for key in ("SAWTAK_API_KEY", "TTS_BUCKET", "AUDIO_BUCKET"):
    os.environ[key] = ""


import pytest


@pytest.fixture(autouse=True)
def _library_without_lesson_sections(monkeypatch):
    """The library tests are about how sections work, on the seeded ones: a section for each lesson (library.LESSON_ROWS)
    is left out here, and test_library_lessons.py turns it on."""
    from app import library
    monkeypatch.setattr(library, "BASE_ROWS", library.SEED_ROWS)
    library.reset()
    yield
    monkeypatch.undo()
    library.reset()
