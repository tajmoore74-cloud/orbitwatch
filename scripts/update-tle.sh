#!/usr/bin/env bash
# Downloads current, real orbital elements (TLEs) from CelesTrak.
#
# Run this on a machine WITH internet. For an air-gapped site, carry the
# resulting file across with the bundle, just like the container image.
#
# Usage:
#   ./scripts/update-tle.sh                        # ISS + space stations -> data/current.tle
#   GROUP=gps-ops ./scripts/update-tle.sh          # any CelesTrak group
#   CATNR=25544,48274 ./scripts/update-tle.sh      # specific NORAD catalog numbers
#
# Then deploy it without rebuilding the image:
#   helm upgrade orbitwatch charts/orbitwatch --reuse-values --set-file tle.data=data/current.tle
set -euo pipefail

ROOT="$(cd "$(dirname "$0")/.." && pwd)"
OUT="${OUT:-$ROOT/data/current.tle}"
BASE="https://celestrak.org/NORAD/elements/gp.php"
mkdir -p "$(dirname "$OUT")"

TMP="$(mktemp)"
trap 'rm -f "$TMP"' EXIT

if [[ -n "${CATNR:-}" ]]; then
  IFS=',' read -ra IDS <<< "$CATNR"
  for id in "${IDS[@]}"; do
    curl -fsS --retry 3 "$BASE?CATNR=${id}&FORMAT=tle" >> "$TMP"
  done
else
  curl -fsS --retry 3 "$BASE?GROUP=${GROUP:-stations}&FORMAT=tle" > "$TMP"
fi

# Sanity checks before replacing anything: 3 lines per satellite, valid line prefixes
LINES=$(grep -cve '^\s*$' "$TMP" || true)
if (( LINES == 0 || LINES % 3 != 0 )); then
  echo "ERROR: unexpected response from CelesTrak ($LINES lines); keeping the old file" >&2
  exit 1
fi
if grep -ve '^\s*$' "$TMP" | awk 'NR%3==2 && !/^1 / || NR%3==0 && !/^2 / {bad=1} END {exit !bad}'; then
  echo "ERROR: response doesn't look like TLE data; keeping the old file" >&2
  exit 1
fi

{
  echo "# Source: CelesTrak, downloaded $(date -u +%Y-%m-%dT%H:%M:%SZ)"
  tr -d '\r' < "$TMP"
} > "$OUT"
echo "==> Wrote $(( LINES / 3 )) satellites to $OUT"
