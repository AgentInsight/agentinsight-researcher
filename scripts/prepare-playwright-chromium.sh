#!/usr/bin/env bash
# Prepare Playwright chromium binary for offline Docker build (方案 E)
#
# Downloads Playwright chromium browser to packages/playwright-browsers/
# using a temporary Docker container. Required by Dockerfile.qa / Dockerfile.offline.
# Run this script on a network-connected machine before building offline images.
#
# Usage: bash scripts/prepare-playwright-chromium.sh
# Output: packages/playwright-browsers/chromium-*/ (chromium binary + deps)

set -e

# Project root (script in scripts/, root is one level up)
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PROJECT_ROOT="$(dirname "$SCRIPT_DIR")"
PACKAGES_DIR="$PROJECT_ROOT/packages"
PW_BROWSERS_DIR="$PACKAGES_DIR/playwright-browsers"

echo "========== Prepare Playwright Chromium (Offline) =========="
echo "Project root: $PROJECT_ROOT"
echo "Output dir:   $PW_BROWSERS_DIR"
echo ""

# Create output directory
mkdir -p "$PW_BROWSERS_DIR"
# Keep .gitkeep
GITKEEP="$PW_BROWSERS_DIR/.gitkeep"
if [ ! -f "$GITKEEP" ]; then
    : > "$GITKEEP"
fi

# Step 1: Pull python:3.12-slim image
echo "[1/5] Pulling python:3.12-slim image..."
if ! docker pull python:3.12-slim; then
    echo "ERROR: Failed to pull python:3.12-slim"
    exit 1
fi

# Step 2: Run temp container to install playwright + chromium
echo "[2/5] Installing playwright + chromium in temp container..."
TEMP_CONTAINER="pw-chromium-prep"
docker rm -f "$TEMP_CONTAINER" >/dev/null 2>&1 || true
# docker run -d may leave container in "Created" state on some Docker versions
# Use docker start to ensure it's actually running
docker run -d --name "$TEMP_CONTAINER" python:3.12-slim sleep 3600 >/dev/null 2>&1 || true
docker start "$TEMP_CONTAINER" >/dev/null 2>&1 || true
sleep 2
CONTAINER_STATUS=$(docker ps --filter "name=$TEMP_CONTAINER" --format "{{.Status}}")
if [ -z "$CONTAINER_STATUS" ] || [[ ! "$CONTAINER_STATUS" == Up* ]]; then
    echo "ERROR: Failed to create/start temp container (status: $CONTAINER_STATUS)"
    exit 1
fi
echo "  Container running: $CONTAINER_STATUS"

# Ensure cleanup of temp container on exit
trap "echo '[5/5] Cleaning up temp container...'; docker rm -f $TEMP_CONTAINER >/dev/null 2>&1 || true" EXIT

# Install playwright
echo "  Installing playwright Python package..."
if ! docker exec "$TEMP_CONTAINER" pip install "playwright>=1.49" >/dev/null 2>&1; then
    echo "  ERROR: pip install playwright failed"
    exit 1
fi

# Install chromium with deps
echo "  Downloading chromium binary (this may take 3-5 minutes)..."
if ! docker exec "$TEMP_CONTAINER" playwright install --with-deps chromium >/dev/null 2>&1; then
    echo "  ERROR: playwright install chromium failed"
    exit 1
fi

# Verify installation
echo "  Verifying chromium installation..."
VERIFY_RESULT=$(docker exec "$TEMP_CONTAINER" sh -c "ls /root/.cache/ms-playwright/ 2>&1")
echo "  Installed browsers: $VERIFY_RESULT"

# Step 3: Copy chromium binary from container to host
echo "[3/5] Copying chromium binary to packages/playwright-browsers/..."

# Copy entire ms-playwright directory
if ! docker cp "${TEMP_CONTAINER}:/root/.cache/ms-playwright/." "$PW_BROWSERS_DIR"; then
    echo "  ERROR: docker cp failed"
    exit 1
fi

echo "  Files copied:"
ls -1 "$PW_BROWSERS_DIR" | while read -r name; do
    echo "    $name"
done

# Calculate size
SIZE_BYTES=$(du -sb "$PW_BROWSERS_DIR" 2>/dev/null | cut -f1)
if [ -n "$SIZE_BYTES" ] && [ "$SIZE_BYTES" -gt 0 ]; then
    SIZE_MB=$(awk "BEGIN {printf \"%.2f\", $SIZE_BYTES / 1024 / 1024}")
    echo "  Total size: $SIZE_MB MB"
fi

# Step 4: Download Chrome .deb dependencies (libnspr4/libnss3/libatk1.0-0/libcups2/libgbm1 etc.)
# Chrome binary requires these shared libraries; without them it crashes with
# "error while loading shared libraries: libnspr4.so: cannot open shared object file"
echo "[4/5] Downloading Chrome .deb dependencies to packages/debs/..."

DEBS_DIR="$PACKAGES_DIR/debs"
mkdir -p "$DEBS_DIR"

# Parse deb.deps from the chromium binary directory (drop version constraints & alternatives)
DEB_DEPS_FILE="$PW_BROWSERS_DIR/chromium-1228/chrome-linux64/deb.deps"
if [ -f "$DEB_DEPS_FILE" ]; then
    # Extract package names: take first alternative, strip version constraint, skip wget/ca-certificates/xdg-utils
    DEB_PKGS=$(grep -v '^\s*#' "$DEB_DEPS_FILE" | grep -v '^\s*$' | \
        awk -F'|' '{print $1}' | awk '{print $1}' | \
        grep -v -E '^(wget|ca-certificates|xdg-utils)$' | tr '\n' ' ')

    echo "  Packages to download: $(echo "$DEB_PKGS" | wc -w)"

    # Update apt index in temp container
    if docker exec "$TEMP_CONTAINER" apt-get update >/dev/null 2>&1; then
        # Download .deb packages (download-only, no install)
        if docker exec "$TEMP_CONTAINER" apt-get install --download-only -y --no-install-recommends $DEB_PKGS >/dev/null 2>&1; then
            # Copy .deb packages from container cache to packages/debs/
            if docker cp "${TEMP_CONTAINER}:/var/cache/apt/archives/." "$DEBS_DIR" 2>/dev/null; then
                DEB_COUNT=$(ls -1 "$DEBS_DIR"/*.deb 2>/dev/null | wc -l)
                DEB_SIZE=$(du -sb "$DEBS_DIR" 2>/dev/null | cut -f1)
                DEB_SIZE_MB=$(awk "BEGIN {printf \"%.2f\", ${DEB_SIZE:-0} / 1024 / 1024}")
                echo "  Total .deb files in packages/debs/: $DEB_COUNT ($DEB_SIZE_MB MB)"
            else
                echo "  WARNING: docker cp .deb packages failed"
            fi
        else
            echo "  WARNING: apt-get install --download-only failed, skipping .deb download"
        fi
    else
        echo "  WARNING: apt-get update failed, skipping .deb download"
    fi
else
    echo "  WARNING: deb.deps not found at $DEB_DEPS_FILE, skipping .deb download"
    echo "  Chrome may crash with missing shared libraries."
fi

echo ""
echo "========== Preparation Complete =========="
echo "Chromium binary saved to: $PW_BROWSERS_DIR"
echo ""
echo "Next steps:"
echo "  1. Build offline image: docker-build.qa.bat (or docker-build.offline.sh)"
echo "  2. Verify chromium in container: docker exec agentinsight-agent-1 python -c 'from playwright.async_api import async_playwright; import asyncio; asyncio.run((lambda: (async_playwright().__aenter__(), None))[0]())'"
echo ""
