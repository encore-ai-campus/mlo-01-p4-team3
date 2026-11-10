#!/usr/bin/env bash
# D-10-01. Execute from any directory with the project ML Python environment.
set -euo pipefail
cd -- "$(dirname -- "${BASH_SOURCE[0]}")/.."
exec "${PYTHON:-python}" -m intent.retrain "$@"
