#!/usr/bin/env bash
# Installs OrbitWatch on a k3s cluster with NO internet access.
# Run from inside the unpacked bundle directory on the offline machine.
#
# Two modes:
#   1) k3s node, no registry (default): load the image straight into containerd
#   2) internal registry: set LOCAL_REGISTRY=registry.local:5000 to push there first
#
# The offline machine needs: k3s, kubectl, helm, gpg (if the bundle is signed),
# and skopeo (only for registry mode). Set REQUIRE_SIGNATURE=1 to refuse
# unsigned bundles.
set -euo pipefail

cd "$(dirname "$0")"
TAG="$(cat IMAGE_TAG)"
NAMESPACE="${NAMESPACE:-orbitwatch}"

for tool in helm kubectl sha256sum; do
  command -v "$tool" >/dev/null 2>&1 || { echo "ERROR: '$tool' is not installed on this machine." >&2; exit 1; }
done

if [[ -f SHA256SUMS.asc ]]; then
  echo "==> Verifying the bundle's GPG signature (proves who built it)"
  gpg --verify SHA256SUMS.asc SHA256SUMS
elif [[ "${REQUIRE_SIGNATURE:-0}" == "1" ]]; then
  echo "ERROR: REQUIRE_SIGNATURE=1 but the bundle is not signed." >&2
  exit 1
else
  echo "==> WARNING: bundle is not signed; checksums only detect corruption, not tampering"
fi

echo "==> Verifying checksums (detects corrupted files)"
sha256sum -c SHA256SUMS

if [[ -n "${LOCAL_REGISTRY:-}" ]]; then
  echo "==> Pushing image to internal registry $LOCAL_REGISTRY"
  skopeo copy "docker-archive:images/orbitwatch.tar" "docker://$LOCAL_REGISTRY/orbitwatch:$TAG"
  REGISTRY="$LOCAL_REGISTRY"
  REPO="orbitwatch"
  PULL_POLICY="IfNotPresent"
else
  echo "==> Importing image into k3s containerd (no registry needed)"
  sudo k3s ctr images import images/orbitwatch.tar
  REGISTRY=""
  REPO="docker.io/library/orbitwatch"
  PULL_POLICY="Never"   # image is already on the node; never try the internet
fi

echo "==> Installing Helm chart from the local file (no chart repo needed)"
helm upgrade --install orbitwatch charts/orbitwatch-*.tgz \
  --namespace "$NAMESPACE" --create-namespace \
  --set image.registry="$REGISTRY" \
  --set image.repository="$REPO" \
  --set image.tag="$TAG" \
  --set image.pullPolicy="$PULL_POLICY" \
  --wait --timeout 5m

echo "==> Done. Check it with:"
echo "    kubectl -n $NAMESPACE get pods"
echo "    kubectl -n $NAMESPACE port-forward svc/orbitwatch 8080:80"
