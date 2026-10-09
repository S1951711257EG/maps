#!/usr/bin/env bash
# Start the API directly (no Docker) - useful on your own PC.
# Needs: python3 -m pip install -r requirements.txt and data/ built.
cd "$(dirname "$0")"
exec python3 -m uvicorn app.main:app --host 0.0.0.0 --port "${PORT:-8000}"
