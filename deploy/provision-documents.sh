#!/bin/bash
set -euo pipefail
export HOME=/opt/data/home
export UV_CACHE_DIR=/opt/data/home/.cache/uv
task_uv=/opt/hermes/tools/uv-0.13.5-linux-arm64/uv
test -x "$task_uv"
if ! test -x /opt/data/home/envs/documents/bin/python; then
  "$task_uv" venv --python /usr/local/bin/python3 /opt/data/home/envs/documents
fi
# Declared requirements are the input. The lockfile is rewritten afterward.
task_requirements=/app/deploy/documents-requirements.txt
"$task_uv" pip install --python /opt/data/home/envs/documents/bin/python -r "$task_requirements"
"$task_uv" pip freeze --python /opt/data/home/envs/documents/bin/python > /workspace/requirements-documents.lock.txt
