#!/usr/bin/env bash
set -euo pipefail
cd -- "$(dirname -- "${BASH_SOURCE[0]}")"
exec .venv/bin/python -X utf8 scripts/run_local.py "$@"
