#!/usr/bin/env bash
#
# Deploy the integration to a live Home Assistant instance over the SMB config
# share, and optionally restart the instance.
#
# Why a script rather than one cp: two things about this path are not obvious and
# both cost an evening the first time.
#
#   1. rsync does NOT overwrite existing .py files on the HA SMB share. It fails
#      on mkstemp with a silent permission error, leaves the old file in place,
#      and reports success. Use cp -R in place, always.
#   2. __pycache__ copied from a developer machine shadows the new sources with
#      stale bytecode compiled for another Python. It is excluded here.
#
# Usage:
#   scripts/deploy_to_instance.sh                 # copy only
#   scripts/deploy_to_instance.sh --restart       # copy, then restart via REST
#
# The share must already be mounted (Finder, or open smb://<host>/config).
# The restart needs HA_URL and HA_TOKEN in the environment; the token is used and
# never printed.

set -euo pipefail

DOMAIN="neverdry_calibrator"
SHARE="${HA_CONFIG_SHARE:-/Volumes/config}"
SRC="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)/custom_components/${DOMAIN}"
DST="${SHARE}/custom_components/${DOMAIN}"
RESTART=0

for arg in "$@"; do
  case "$arg" in
    --restart) RESTART=1 ;;
    *) echo "unknown argument: $arg" >&2; exit 2 ;;
  esac
done

if [ ! -d "$SHARE" ]; then
  echo "The Home Assistant config share is not mounted at ${SHARE}." >&2
  echo "Mount it first, for example: open smb://<host>/config" >&2
  exit 1
fi

if [ ! -d "$SRC" ]; then
  echo "Source not found: ${SRC}" >&2
  exit 1
fi

echo "Source:      ${SRC}"
echo "Destination: ${DST}"

# Stage a clean copy so that __pycache__ and editor leftovers never reach the
# instance, then copy in place over whatever is already there.
STAGE="$(mktemp -d)"
trap 'rm -rf "$STAGE"' EXIT
cp -R "$SRC/." "$STAGE/"
find "$STAGE" -name '__pycache__' -type d -prune -exec rm -rf {} + 2>/dev/null || true
find "$STAGE" -name '*.pyc' -delete 2>/dev/null || true
find "$STAGE" -name '.DS_Store' -delete 2>/dev/null || true

mkdir -p "$DST"

# Stale bytecode at the destination is compiled for the instance's Python and
# survives a copy. Python would normally invalidate it by timestamp, but the SMB
# share does not preserve mtimes reliably, so it is removed rather than trusted.
find "$DST" -name '__pycache__' -type d -prune -exec rm -rf {} + 2>/dev/null || true

cp -R "$STAGE/." "$DST/"

echo "Copied $(find "$STAGE" -type f | wc -l | tr -d ' ') files."
echo "Deployed version: $(python3 -c "import json,sys;print(json.load(open('${SRC}/manifest.json'))['version'])")"

# Verify the copy actually landed: the silent-failure mode this script exists for
# shows up exactly here, as a destination file older than the source.
if ! diff -r -x '__pycache__' -x '*.pyc' -x '.DS_Store' "$STAGE" "$DST" >/dev/null 2>&1; then
  echo "WARNING: destination differs from source after copy. Check share permissions." >&2
  exit 1
fi
echo "Verified: destination matches source."

if [ "$RESTART" -eq 1 ]; then
  if [ -z "${HA_URL:-}" ] || [ -z "${HA_TOKEN:-}" ]; then
    echo "HA_URL or HA_TOKEN not set; restart the instance from the user interface instead." >&2
    exit 1
  fi
  echo "Restarting ${HA_URL} ..."
  # A restart tears down the HTTP server mid-response, so a 504 or a dropped
  # connection is the expected outcome, not a failure.
  curl -sS -m 20 -X POST \
    -H "Authorization: Bearer ${HA_TOKEN}" \
    -H "Content-Type: application/json" \
    "${HA_URL%/}/api/services/homeassistant/restart" >/dev/null 2>&1 || true
  echo "Restart requested. The instance takes about a minute to come back."
fi
