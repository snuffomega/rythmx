#!/bin/bash
set -euo pipefail

BASE="/mnt/user/data/scripts/rythmx"
VENV="$BASE/venv"
REQ="$BASE/requirements.txt"

echo "[rythmx] bootstrap: start"

# Create venv if missing
if [ ! -f "$VENV/bin/activate" ]; then
  echo "[rythmx] creating venv at $VENV"
  python3 -m venv "$VENV"
fi

# Activate and install deps
source "$VENV/bin/activate"
pip install --upgrade pip
pip install -r "$REQ"

echo "[rythmx] bootstrap: done"
