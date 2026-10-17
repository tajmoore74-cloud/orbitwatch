#!/usr/bin/env bash
# Fastest way to run the whole thing on your own Linux machine or Codespace.
# Uses k3d (k3s inside Docker), so there's nothing to install on the host
# besides Docker, kubectl, helm, and k3d.
set -euo pipefail

ROOT="$(cd "$(dirname "$0")/.." && pwd)"

echo "==> Creating local k3s cluster 'orbitwatch'"
k3d cluster create orbitwatch --agents 1 --wait || echo "(cluster already exists)"

echo "==> Building image and loading it into the cluster"
docker build -t orbitwatch:local "$ROOT/app"
k3d image import orbitwatch:local -c orbitwatch

echo "==> Installing with Helm"
helm upgrade --install orbitwatch "$ROOT/charts/orbitwatch" \
  --namespace orbitwatch --create-namespace \
  --set image.registry="" \
  --set image.repository=orbitwatch \
  --set image.tag=local \
  --set image.pullPolicy=Never \
  --wait

kubectl -n orbitwatch get pods
echo
echo "Now run:  kubectl -n orbitwatch port-forward svc/orbitwatch 8080:80"
echo "Then:     curl localhost:8080/telemetry/99901"
echo "          curl 'localhost:8080/passes/99901?station=colorado-springs'"
