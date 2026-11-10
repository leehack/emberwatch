#!/bin/sh
# Builds the macOS debug app, runs it, and captures frames.
# Usage: tool/shoot.sh <out_dir> [shot_times] [extra env assignments...]
set -e
cd "$(dirname "$0")/.."
OUT="$1"; TIMES="${2:-4}"; shift; [ $# -gt 0 ] && shift
mkdir -p "$OUT"; rm -f "$OUT"/shot_*.png
flutter build macos --debug > "$OUT/build.log" 2>&1 || { tail -40 "$OUT/build.log"; exit 1; }
env SHOT_DIR="$OUT" SHOT_TIMES="$TIMES" "$@" \
  ./build/macos/Build/Products/Debug/emberwatch.app/Contents/MacOS/emberwatch > "$OUT/run.log" 2>&1 &
PID=$!
LAST=$(echo "$TIMES" | tr ',' '\n' | sort -n | tail -1)
LIMIT=$(( ${LAST%.*} + 45 ))
while kill -0 $PID 2>/dev/null && [ $LIMIT -gt 0 ]; do sleep 1; LIMIT=$((LIMIT-1)); done
kill $PID 2>/dev/null || true
grep -v "pipeline build took\|Dart VM service\|Impeller rendering" "$OUT/run.log" | tail -30
ls "$OUT" | grep png || echo "NO SHOTS"
