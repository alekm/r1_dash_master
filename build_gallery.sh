#!/usr/bin/env bash
# Regenerate the importable dashboard bundles from the specs in examples/.
# examples/*.json = source specs (for the MCP / builder)
# gallery/*.zip           = ready-to-import for RUCKUS One       (target r1)
# gallery/analytics/*.zip = ready-to-import for RUCKUS Analytics (target analytics)
set -e
cd "$(dirname "$0")"
mkdir -p gallery/analytics
for spec in examples/*.json; do
  name=$(basename "$spec" .json)
  python3 builder.py "$spec" "gallery/${name}.zip" --target r1 >/dev/null
  python3 builder.py "$spec" "gallery/analytics/${name}.zip" --target analytics >/dev/null
  echo "  built ${name}  (r1 + analytics)"
done
echo "Gallery rebuilt: $(ls gallery/*.zip | wc -l) R1 + $(ls gallery/analytics/*.zip | wc -l) Analytics dashboards"
