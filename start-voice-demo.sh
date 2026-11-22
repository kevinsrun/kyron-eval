#!/bin/sh
set -eu
cd "$(dirname "$0")"
export GUAVA_DISABLE_TELEMETRY=true
export GUAVA_HEALTH_SERVER=false
export KYRON_OPENEMR_ALLOW_WRITES=0
if [ -z "${KYRON_OPENEMR_DIR:-}" ]; then
  if [ -f integrations/openemr/.local/manifest.json ]; then
    export KYRON_OPENEMR_DIR="$PWD/integrations/openemr/.local"
  elif [ -f ../kyron-eval/integrations/openemr/.local/manifest.json ]; then
    export KYRON_OPENEMR_DIR="$(cd ../kyron-eval/integrations/openemr/.local && pwd)"
  fi
fi
uv sync --frozen
exec uv run --frozen python -m kyron_eval.voice.live_demo "$@"
