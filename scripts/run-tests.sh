#!/usr/bin/env bash
set -euo pipefail
ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$ROOT"
export PYTHONPATH="$ROOT${PYTHONPATH:+:$PYTHONPATH}"

mapfile -t TESTS < <(find tests -maxdepth 1 -type f -name 'test_*.py' -print | sort)
if [ "${#TESTS[@]}" -eq 0 ]; then
  echo "ERROR: no tests found under tests/" >&2
  exit 1
fi

for test_file in "${TESTS[@]}"; do
  echo "[test] $test_file"
  /usr/bin/python3 "$test_file"
done
