#!/usr/bin/env bash
set -euo pipefail
cd -- "$(dirname -- "${BASH_SOURCE[0]}")/.."
if [[ -x .venv/bin/python ]]; then
  exec .venv/bin/python -m uvicorn app.server:app --host 127.0.0.1 --port 8765
fi
exec python3 -m uvicorn app.server:app --host 127.0.0.1 --port 8765
