#!/usr/bin/env bash
# Run every step with its verifier, stopping at the first failure.
set -e
for s in 1 2 3 4 5 6 7 8; do
  echo ""
  echo "########## STEP $s ##########"
  python scripts/step${s}_*.py "$@"
  python verify/verify_step${s}.py
done
echo ""
echo "All steps passed."
