#!/usr/bin/env bash
# Builds a self-contained bundle for deploying OrbitWatch to a cluster with
# NO internet access (an "air-gapped" or classified environment).
#
# Run this on a machine WITH internet. Output: dist/orbitwatch-airgap-<tag>.tar.gz
#
# Inside the bundle:
#   images/orbitwatch.tar   - the container image as a file
#   charts/orbitwatch-*.tgz - the packaged Helm chart
#   airgap-install.sh       - the script to run on the offline side
#   SHA256SUMS              - checksums so the offline side can detect corrupted files
#   SHA256SUMS.asc          - (optional) GPG signature proving who built the bundle
#
# Usage:
#   IMAGE_REF=registry.gitlab.com/you/orbitwatch:abc123 ./scripts/airgap-bundle.sh
set -euo pipefail

: "${IMAGE_REF:?Set IMAGE_REF to the image to bundle, e.g. registry.gitlab.com/you/orbitwatch:abc123}"

ROOT="$(cd "$(dirname "$0")/.." && pwd)"
TAG="${IMAGE_REF##*:}"
WORK="$(mktemp -d)"
BUNDLE="$WORK/orbitwatch-airgap-$TAG"
mkdir -p "$BUNDLE/images" "$BUNDLE/charts" "$ROOT/dist"

echo "==> Saving image $IMAGE_REF"
if command -v skopeo >/dev/null 2>&1; then
  CREDS=()
  if [[ -n "${REGISTRY_USER:-}" ]]; then
    CREDS=(--src-creds "$REGISTRY_USER:$REGISTRY_PASS")
  fi
  skopeo copy "${CREDS[@]}" "docker://$IMAGE_REF" "docker-archive:$BUNDLE/images/orbitwatch.tar:orbitwatch:$TAG"
else
  docker pull "$IMAGE_REF"
  docker tag "$IMAGE_REF" "orbitwatch:$TAG"
  docker save -o "$BUNDLE/images/orbitwatch.tar" "orbitwatch:$TAG"
fi

echo "==> Packaging Helm chart"
helm package "$ROOT/charts/orbitwatch" --destination "$BUNDLE/charts"

cp "$ROOT/scripts/airgap-install.sh" "$BUNDLE/"
echo "$TAG" > "$BUNDLE/IMAGE_TAG"

echo "==> Writing checksums"
(cd "$BUNDLE" && find . -type f -print0 | sort -z | xargs -0 sha256sum > "$WORK/SHA256SUMS")
mv "$WORK/SHA256SUMS" "$BUNDLE/SHA256SUMS"

# Checksums catch corruption, but someone who swaps a file could also swap the
# checksum list. A GPG signature over SHA256SUMS proves who made the bundle.
if [[ -n "${GPG_KEY_ID:-}" ]]; then
  echo "==> Signing checksums with GPG key $GPG_KEY_ID"
  gpg --batch --yes --local-user "$GPG_KEY_ID" --armor --detach-sign \
    --output "$BUNDLE/SHA256SUMS.asc" "$BUNDLE/SHA256SUMS"
else
  echo "==> (Skipping signature: set GPG_KEY_ID to sign the bundle)"
fi

tar -C "$WORK" -czf "$ROOT/dist/orbitwatch-airgap-$TAG.tar.gz" "orbitwatch-airgap-$TAG"
rm -rf "$WORK"
echo "==> Done: dist/orbitwatch-airgap-$TAG.tar.gz"
