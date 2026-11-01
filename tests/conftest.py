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
