#!/bin/sh
set -eu
cd "$(dirname "$0")"
if ! command -v python3 >/dev/null 2>&1; then
  echo "Python 3 is not installed. Install Python 3.8 or newer."
  exit 1
fi
python3 -c 'import sys; raise SystemExit(0 if sys.version_info >= (3,8) else 1)' || {
  echo "Python 3.8 or newer is required."
  exit 1
}
exec python3 app.py
