#!/usr/bin/env bash
set -euo pipefail
python scripts/e2e/run_stack.py &
stack_pid=$!
trap 'kill -INT "$stack_pid" 2>/dev/null || true' EXIT
for attempt in {1..120}; do
  if curl -fsS http://127.0.0.1:8802/api/e2e/isolation >/dev/null && curl -fsS http://127.0.0.1:3302 >/dev/null; then break; fi
  if ! kill -0 "$stack_pid" 2>/dev/null; then exit 1; fi
  sleep 1
done
cd frontend
npx playwright test
