#!/usr/bin/env bash

set -euo pipefail

ENGINE="${1:-docker}"
LOCAL_IMAGE="network-benchmark:latest"
REMOTE_IMAGE="quay.io/nwadekar/kubernetes-cluster-bench:latest"
SCRIPT_DIR=$(CDPATH= cd -- "$(dirname -- "$0")" && pwd)

case "$ENGINE" in
  docker|podman) ;;
  *)
    echo "Usage: $0 [docker|podman]" >&2
    exit 1
    ;;
esac

command -v "$ENGINE" >/dev/null 2>&1 || {
  echo "$ENGINE is required" >&2
  exit 1
}

"$SCRIPT_DIR/build_image.sh" "$ENGINE"

"$ENGINE" tag "$LOCAL_IMAGE" "$REMOTE_IMAGE"
"$ENGINE" push "$REMOTE_IMAGE"

echo "Published $REMOTE_IMAGE"
