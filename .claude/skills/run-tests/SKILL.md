---
name: run-tests
description: Run the pytest test suite. Pass 'unit', 'integration', or 'contract' to run a specific tier, or leave blank for all tests.
disable-model-invocation: true
---

TIER="${ARGS:-}"

if [ -z "$TIER" ]; then
  uv run pytest tests/ -v
else
  if [ ! -d "tests/$TIER" ]; then
    echo "Unknown tier '$TIER'. Valid options: unit, integration, contract"
    exit 1
  fi
  uv run pytest "tests/$TIER/" -v
fi
