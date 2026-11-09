#!/bin/bash
# Run one browser command, then stop Chromium. A second copy is refused.
set -euo pipefail
if [ "$#" -lt 1 ]; then
  echo 'Usage: browser_once.sh COMMAND...' >&2
  exit 2
fi
if pgrep -f '[c]hromium|[c]hrome' >/dev/null 2>&1; then
  echo 'A browser is already running. Close it before opening another page.' >&2
  exit 1
fi
set +e
"$@"
status=$?
set -e
pkill -f '[c]hromium|[c]hrome' >/dev/null 2>&1 || true
exit "$status"
