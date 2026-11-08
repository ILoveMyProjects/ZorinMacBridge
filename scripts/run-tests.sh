#!/usr/bin/env bash
set -euo pipefail
ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$ROOT"
export PYTHONPATH="$ROOT${PYTHONPATH:+:$PYTHONPATH}"

OBSOLETE_PRELOGIN_TEST="tests/test_prelogin_displaystream_capture.py"
if [ -e "$OBSOLETE_PRELOGIN_TEST" ]; then
  echo "ERROR: obsolete CGDisplayStream regression test is still tracked: $OBSOLETE_PRELOGIN_TEST" >&2
  echo "Remove it with: git rm -f $OBSOLETE_PRELOGIN_TEST" >&2
  exit 1
fi

mapfile -t TESTS < <(find tests -maxdepth 1 -type f -name 'test_*.py' -print | sort)
if [ "${#TESTS[@]}" -eq 0 ]; then
  echo "ERROR: no tests found under tests/" >&2
  exit 1
fi

for test_file in "${TESTS[@]}"; do
  echo "[test] $test_file"
  /usr/bin/python3 "$test_file"
done
