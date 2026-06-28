#!/usr/bin/env bash
# Regenerate the importable dashboard bundles in gallery/ from the specs in examples/.
# examples/*.json = source specs (for the MCP / builder); gallery/*.zip = ready-to-import.
set -e
cd "$(dirname "$0")"
mkdir -p gallery
for spec in examples/*.json; do
  name=$(basename "$spec" .json)
  python3 builder.py "$spec" "gallery/${name}.zip" >/dev/null
  echo "  built gallery/${name}.zip"
done
echo "Gallery rebuilt: $(ls gallery/*.zip | wc -l) importable dashboards"
