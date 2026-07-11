#!/usr/bin/env bash
# ============================================================================
# Prepare uv/uvx binary for QA/Offline Docker builds
# ----------------------------------------------------------------------------
# Follows AGENTS.md chapter 12: QA/Offline mode, all deps pre-downloaded to packages/
# Downloads uv binary tarball (x86_64-linux) to packages/uv/uv-x86_64-linux.tar.gz
# Dockerfile.qa / Dockerfile.offline COPY packages/uv/ and install uv/uvx to /usr/local/bin
#
# Prerequisites: internet access (first run)
# After run: packages/uv/uv-x86_64-linux.tar.gz ready for Dockerfile.qa / Dockerfile.offline
# ============================================================================

set -euo pipefail

# Project root (script in scripts/, root is one level up)
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PROJECT_ROOT="$(cd "$SCRIPT_DIR/.." && pwd)"
UV_DIR="$PROJECT_ROOT/packages/uv"

# Create directory
mkdir -p "$UV_DIR"

# uv version (check https://github.com/astral-sh/uv/releases for latest)
UV_VERSION="0.7.8"
TARGET_FILE="$UV_DIR/uv-x86_64-linux.tar.gz"

echo "========== Prepare uv/uvx binary (QA/Offline) =========="
echo "Version: $UV_VERSION"
echo "Target:  $TARGET_FILE"

# Check if already downloaded
if [ -f "$TARGET_FILE" ]; then
    size_bytes=$(stat -c '%s' "$TARGET_FILE" 2>/dev/null || stat -f '%z' "$TARGET_FILE" 2>/dev/null)
    size_mb=$(awk "BEGIN {printf \"%.1f\", $size_bytes / 1048576}")
    echo "[SKIP] $TARGET_FILE already exists ($size_mb MB)"
    exit 0
fi

# Download from GitHub releases
DOWNLOAD_URL="https://github.com/astral-sh/uv/releases/download/${UV_VERSION}/uv-x86_64-unknown-linux-gnu.tar.gz"
echo "[1/2] Downloading uv $UV_VERSION from GitHub..."
echo "  URL: $DOWNLOAD_URL"

if ! curl -fSL -o "$TARGET_FILE" "$DOWNLOAD_URL"; then
    echo "[ERROR] Download failed"
    echo "  Manual download: $DOWNLOAD_URL"
    echo "  Place at: $TARGET_FILE"
    exit 1
fi

# Verify download
if [ -f "$TARGET_FILE" ]; then
    size_bytes=$(stat -c '%s' "$TARGET_FILE" 2>/dev/null || stat -f '%z' "$TARGET_FILE" 2>/dev/null)
    size_mb=$(awk "BEGIN {printf \"%.1f\", $size_bytes / 1048576}")
    echo "[2/2] Download complete: $size_mb MB"
else
    echo "[ERROR] File not found after download"
    exit 1
fi

echo ""
echo "========== Done =========="
echo "packages/uv/uv-x86_64-linux.tar.gz ready for Dockerfile.qa / Dockerfile.offline"
