#!/usr/bin/env bash
# Install the extra packages for scripts/zero_shot_phone_models.py into .zero-shot-deps/ (git-ignored),
# outside the app venv. The script adds that folder to its own import path; nothing else sees it.
set -euo pipefail
ROOT="$(cd "$(dirname "$0")/.." && pwd)"
PY="${PYTHON:-$ROOT/.venv/bin/python}"
DEST="$ROOT/.zero-shot-deps"

rm -rf "$DEST"
"$PY" -m pip install --quiet --no-deps --target "$DEST/pkgs" -r "$ROOT/requirements-zero-shot.txt"

# ESPnet imports torchaudio for multi-microphone beamforming, which POWSM never runs, and there is no
# torchaudio build for torch 2.14. A placeholder lets the import succeed and fails loudly if anything uses it.
mkdir -p "$DEST/stubs/torchaudio"
cat > "$DEST/stubs/torchaudio/__init__.py" <<'EOF'
"""Placeholder: ESPnet imports torchaudio for beamforming code that POWSM never runs."""


def __getattr__(name):
    raise ImportError(f"torchaudio placeholder: '{name}' is not available (no torchaudio build for this torch)")
EOF
echo "installed into $DEST"
