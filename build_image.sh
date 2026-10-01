#!/bin/bash

set -e

RED='\033[0;31m'
GREEN='\033[0;32m'
BLUE='\033[0;34m'
YELLOW='\033[0;33m'
NC='\033[0m'

ENGINE="${1:-docker}"
VARIANT="${2:-default}"
IMAGE_BASE="kubernetes-cluster-bench"

case "$ENGINE" in
    docker|podman)
        CONTAINER_ENGINE="$ENGINE"
        ;;
    *)
        echo -e "${RED}Unknown container engine: $ENGINE${NC}"
        echo ""
        echo "Usage: $0 [docker|podman] [default|efa]"
        exit 1
        ;;
esac

case "$VARIANT" in
    default)
        DOCKERFILE="Dockerfile"
        IMAGE_TAG="${IMAGE_BASE}:latest"
        DESC="IB/RoCE (Mellanox OFED + CUDA + ROCm)"
        ;;
    efa)
        DOCKERFILE="Dockerfile.efa"
        IMAGE_TAG="${IMAGE_BASE}:efa"
        DESC="AWS EFA (EFA installer + CUDA)"
        ;;
    *)
        echo -e "${RED}Unknown variant: $VARIANT${NC}"
        echo ""
        echo "Usage: $0 [docker|podman] [default|efa]"
        echo ""
        echo "  default  IB/RoCE image with Mellanox OFED, CUDA, ROCm"
        echo "  efa      AWS EFA image with EFA installer, CUDA"
        exit 1
        ;;
esac

if ! command -v "$CONTAINER_ENGINE" >/dev/null 2>&1; then
    echo -e "${RED}$CONTAINER_ENGINE is required${NC}" >&2
    exit 1
fi

echo -e "${BLUE}============================================${NC}"
echo -e "${BLUE}Building Container Image${NC}"
echo -e "${BLUE}============================================${NC}"
echo "Engine:     $CONTAINER_ENGINE"
echo "Variant:    $DESC"
echo "Dockerfile: $DOCKERFILE"
echo "Image tag:  $IMAGE_TAG"
echo ""

if [ "$CONTAINER_ENGINE" = "docker" ] && docker buildx version &> /dev/null; then
    echo "Using docker buildx to build for linux/amd64..."
    docker buildx build --platform linux/amd64 --load -f "$DOCKERFILE" -t "$IMAGE_TAG" .
elif [ "$CONTAINER_ENGINE" = "docker" ]; then
    echo "Using docker build for linux/amd64..."
    docker build --platform linux/amd64 -f "$DOCKERFILE" -t "$IMAGE_TAG" .
else
    echo "Using podman build for linux/amd64..."
    podman build --platform linux/amd64 -f "$DOCKERFILE" -t "$IMAGE_TAG" .
fi

echo ""
echo -e "${GREEN}✓ Build complete: $IMAGE_TAG${NC}"

SIZE=$($CONTAINER_ENGINE image inspect "$IMAGE_TAG" --format='{{.Size}}' | awk '{printf "%.2f", $1/1024/1024/1024}')
echo -e "Image size: ${GREEN}${SIZE} GB${NC}"
echo ""