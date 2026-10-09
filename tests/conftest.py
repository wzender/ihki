"""Tests run without sign-in whatever the local .env says: set before app.main loads it."""
import os

os.environ["SUPABASE_URL"] = ""
