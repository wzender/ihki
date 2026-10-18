"""API keys a developer sets in the management panel (SAWTAK_API_KEY, ANTHROPIC_API_KEY), and the tutor's model
(TUTOR_MODEL), so changing one needs no access to the host (the Hugging Face Space).

Each key is kept in the Supabase table `app_secrets`, encrypted with Fernet under SECRETS_MASTER_KEY, which lives only in
the host's environment: the table, a backup of it or a look at the Supabase dashboard shows no key. The table has
row-level security on and no policy, so only the server's secret key reads it. A key never goes back to the page: the
panel sees whether it is set, where from, and its last four characters. A new key is tried with its provider before it
is kept, so a typo cannot switch the playback or the tutor off. TUTOR_MODEL is no secret: it is kept the same way, but
the panel shows it, and a model is checked by asking Anthropic for it with the key in use.

A key kept in the table takes the place of the environment's; without one (or without the table, the secret key or the
master key) the environment's is used, as before.

Env:  SECRETS_MASTER_KEY  (a Fernet key; make one with
                           python -c "from cryptography.fernet import Fernet; print(Fernet.generate_key().decode())"
                           and keep it as a Space secret. Losing it loses the stored keys, not the environment's.)
"""
from __future__ import annotations

import logging
import os
import re
import threading
import urllib.error
import urllib.request

from .auth import AuthUnavailable

log = logging.getLogger("ihki.keys")
# the keys the panel may set, and how the panel names them; nothing else (the master key, the Supabase keys) is settable
NAMES = {"SAWTAK_API_KEY": "Sawtak Arabi · השמעת התשובה", "ANTHROPIC_API_KEY": "Anthropic · המורה (יאללה נדבר)",
         "TUTOR_MODEL": "המודל של המורה (TUTOR_MODEL)"}
FALLBACK = {"ANTHROPIC_API_KEY": ("ANTHROPIC_API_KEY", "CLAUDE_API_KEY"), "SAWTAK_API_KEY": ("SAWTAK_API_KEY",),
            "TUTOR_MODEL": ("TUTOR_MODEL",)}
PUBLIC = frozenset({"TUTOR_MODEL"})   # settings the panel may show in full
# the panel's list of tutor models (any other id can be typed); empty = the code's default (tutor.DEFAULT_MODEL)
MODEL_CHOICES = [("claude-haiku-5-5", "Haiku 5.5 · ברירת המחדל, הזול ביותר"), ("claude-sonnet-5-5", "Sonnet 5.5"),
                 ("claude-opus-5-5", "Opus 5.5"), ("claude-fable-5-1", "Fable 5.1 · היקר ביותר"), ("claude-haiku-4-5", "Haiku 4.5")]
MODEL_ID = re.compile(r"^claude-[a-z0-9][a-z0-9.-]{2,60}$")
MAX_LEN = 300
TIMEOUT = 15
CREATE_APP_SECRETS = ("create table public.app_secrets (name text primary key check (name in ('SAWTAK_API_KEY','ANTHROPIC_API_KEY','TUTOR_MODEL')), "
                      "ciphertext text not null, last4 text not null default '', updated_by text not null default '', "
                      "updated_at timestamptz not null default now()); "
                      "alter table public.app_secrets enable row level security; "
                      "revoke all on public.app_secrets from anon, authenticated;")
# for a table made before TUTOR_MODEL could be kept in it
MIGRATE_APP_SECRETS = ("alter table public.app_secrets drop constraint if exists app_secrets_name_check; "
                       "alter table public.app_secrets add constraint app_secrets_name_check "
                       "check (name in ('SAWTAK_API_KEY','ANTHROPIC_API_KEY','TUTOR_MODEL'));")


class KeyRefused(ValueError):
    """The provider refused the key, or it could not be tried. The message is safe to show."""


class KeyStore:
    def __init__(self, auth, master: str = ""):
        self.auth = auth
        self._fernet, self.why = None, ""   # why: what keeps the panel from saving keys, in words for the page
        if master:
            try:
                from cryptography.fernet import Fernet
                self._fernet = Fernet(master.strip().encode())
            except ImportError:
                self.why = "החבילה cryptography לא מותקנת בשרת."
            except ValueError:
                log.error("SECRETS_MASTER_KEY is not a valid Fernet key: keys set in the panel are off")
                self.why = "SECRETS_MASTER_KEY בשרת אינו מפתח Fernet תקין."
        else:
            self.why = "חסר SECRETS_MASTER_KEY בסודות השרת, ולכן אי אפשר לשמור מפתחות מהפאנל."
        if not self.why and not getattr(auth, "secret", ""):
            self.why = "חסר SUPABASE_SECRET_KEY בשרת, ולכן אי אפשר לשמור מפתחות מהפאנל."
        self.missing = False   # the table is not there yet (CREATE_APP_SECRETS)
        self._rows: dict[str, dict] = {}   # name -> {"value", "last4", "updated_by", "updated_at"}
        self._lock = threading.Lock()

    @property
    def writable(self) -> bool:
        return not self.why and not self.missing

    def _seal(self, name: str, value: str) -> str:
        # the name goes inside the ciphertext, so one key's row copied into another's does not decrypt as it
        return self._fernet.encrypt(f"{name}\n{value}".encode()).decode()

    def _open(self, name: str, token: str) -> str | None:
        from cryptography.fernet import InvalidToken
        try:
            got, _, value = self._fernet.decrypt(token.encode()).decode().partition("\n")
        except (InvalidToken, ValueError):
            return None
        return value if got == name else None

    def load(self) -> None:
        """Reads the stored keys. A failure is logged and leaves the environment's keys in use."""
        if self.why or not hasattr(self.auth, "secret_rows"):
            return
        try:
            rows = self.auth.secret_rows()
        except AuthUnavailable as e:
            log.warning("stored API keys not read, the environment's are used: %s", e)
            return
        if rows is None:
            self.missing = True
            return
        found = {}
        for r in rows:
            value = self._open(r["name"], r.get("ciphertext") or "") if r.get("name") in NAMES else None
            if value is None:
                log.warning("stored key %s does not decrypt with this SECRETS_MASTER_KEY: the environment's is used", r.get("name"))
                continue
            found[r["name"]] = {"value": value, "last4": r.get("last4") or "", "updated_by": r.get("updated_by") or "",
                                "updated_at": r.get("updated_at")}
        with self._lock:
            self._rows = found

    @staticmethod
    def _env(name: str) -> str:
        return next((v for v in (os.environ.get(n, "").strip() for n in FALLBACK[name]) if v), "")

    def get(self, name: str) -> str:
        """The key in use: the panel's, else the environment's, else empty."""
        with self._lock:
            row = self._rows.get(name)
        return row["value"] if row else self._env(name)

    def status(self) -> list[dict]:
        """What the panel shows of each key: never the key itself."""
        out = []
        for name, label in NAMES.items():
            with self._lock:
                row = self._rows.get(name)
            env = self._env(name)
            item = {"name": name, "label": label, "source": "db" if row else "env" if env else "none",
                    "last4": "" if name in PUBLIC else row["last4"] if row else env[-4:] if len(env) >= 12 else "",
                    "updated_by": row["updated_by"] if row else "", "updated_at": row["updated_at"] if row else None}
            if name in PUBLIC:
                item.update(value=row["value"] if row else env, choices=[{"id": i, "label": l} for i, l in MODEL_CHOICES])
            out.append(item)
        return out

    def set(self, name: str, value: str, by: str) -> None:
        """Encrypts and keeps a key that check() accepted."""
        row = {"name": name, "ciphertext": self._seal(name, value), "last4": value[-4:], "updated_by": by}
        self.auth.save_secret(row)
        with self._lock:
            self._rows[name] = {"value": value, "last4": row["last4"], "updated_by": by, "updated_at": None}

    def clear(self, name: str) -> None:
        """Forgets the panel's key; the environment's (if any) is used again."""
        self.auth.delete_secret(name)
        with self._lock:
            self._rows.pop(name, None)


def check(name: str, value: str, anthropic_key: str = "") -> None:
    """Tries the key with its provider, with a call that costs nothing; a tutor model, by asking Anthropic for it with
    `anthropic_key` (the key in use). Raises KeyRefused."""
    if name == "TUTOR_MODEL":
        if not MODEL_ID.match(value):
            raise KeyRefused("מזהה מודל נראה כך: claude-haiku-5-5.")
        if not anthropic_key:
            raise KeyRefused("אין מפתח Anthropic, ולכן אי אפשר לבדוק את המודל. שמרו קודם מפתח.")
        import anthropic
        try:
            anthropic.Anthropic(api_key=anthropic_key, timeout=TIMEOUT, max_retries=0).models.retrieve(value)
        except anthropic.NotFoundError:
            raise KeyRefused(f"Anthropic לא מכיר את המודל {value}.") from None
        except (anthropic.AuthenticationError, anthropic.PermissionDeniedError):
            raise KeyRefused("מפתח Anthropic שבשימוש נדחה, ולכן אי אפשר לבדוק את המודל.") from None
        except anthropic.APIStatusError as e:
            raise KeyRefused(f"לא ניתן לבדוק את המודל מול Anthropic כרגע ({e.status_code}). נסו שוב.") from None
        except anthropic.APIConnectionError:
            raise KeyRefused("אין חיבור ל-Anthropic כרגע. נסו שוב.") from None
        return
    if name == "SAWTAK_API_KEY":
        req = urllib.request.Request("https://api.sawtakarabi.ai/v1/voices", headers={
            "Authorization": f"Bearer {value}", "User-Agent": "ihki/1.0"})
        try:
            with urllib.request.urlopen(req, timeout=TIMEOUT):
                return
        except urllib.error.HTTPError as e:
            if e.code == 403:   # a valid key restricted to `tts` may not list voices; speech is all it is used for
                return
            if e.code == 401:
                raise KeyRefused("Sawtak דחה את המפתח.") from None
            raise KeyRefused(f"לא ניתן לבדוק את המפתח מול Sawtak כרגע ({e.code}). נסו שוב.") from None
        except OSError:
            raise KeyRefused("אין חיבור ל-Sawtak כרגע. נסו שוב.") from None
    import anthropic
    try:
        anthropic.Anthropic(api_key=value, timeout=TIMEOUT, max_retries=0).models.list(limit=1)
    except (anthropic.AuthenticationError, anthropic.PermissionDeniedError):
        raise KeyRefused("Anthropic דחה את המפתח.") from None
    except anthropic.APIStatusError as e:
        raise KeyRefused(f"לא ניתן לבדוק את המפתח מול Anthropic כרגע ({e.status_code}). נסו שוב.") from None
    except anthropic.APIConnectionError:
        raise KeyRefused("אין חיבור ל-Anthropic כרגע. נסו שוב.") from None


def load_keys(auth) -> KeyStore:
    keys = KeyStore(auth, os.environ.get("SECRETS_MASTER_KEY", ""))
    keys.load()
    return keys
