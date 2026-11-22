#!/bin/sh
set -eu
cd "$(dirname "$0")"
uv sync --frozen
exec uv run --frozen uvicorn kyron_eval.web:app --host 127.0.0.1 --port "${KYRON_PORT:-8000}"
