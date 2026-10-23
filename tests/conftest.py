"""Tests run without sign-in whatever the local .env says: set before app.main loads it."""
import os

os.environ["SUPABASE_URL"] = ""
os.environ["HEBREW_NIQQUD"] = "0"   # no calls to Dicta's Nakdan; test_nakdan.py fakes it
