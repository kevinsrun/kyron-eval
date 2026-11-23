#!/bin/sh
set -eu
cd "$(dirname "$0")"
export KYRON_BOOKING_ENABLED=1
export KYRON_EMAIL_CONNECTOR=1
exec ./start-voice-demo.sh "$@"
