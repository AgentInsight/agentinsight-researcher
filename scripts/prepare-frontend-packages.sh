#!/usr/bin/env bash
# prepare-frontend-packages.sh
# Prepare frontend npm dependencies for offline/QA mode (Linux/macOS)
#
# This script does two things:
# 1. Run npm install in frontend/ to generate complete node_modules
# 2. Pack node_modules as tarball to packages/frontend-wheels/node-modules.tar.gz
#
# Prerequisites: Node.js 20+, npm, internet access (first run)
# After run: packages/frontend-wheels/node-modules.tar.gz ready for frontend/Dockerfile.qa / Dockerfile.offline

set -e

# Project root (script in scripts/, root is one level up)
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PROJECT_ROOT="$(cd "$SCRIPT_DIR/.." && pwd)"
FRONTEND_DIR="$PROJECT_ROOT/frontend"
PACKAGES_DIR="$PROJECT_ROOT/packages"
FRONTEND_WHEELS_DIR="$PACKAGES_DIR/frontend-wheels"
NODE_MODULES_TARBALL="$FRONTEND_WHEELS_DIR/node-modules.tar.gz"

# Create directories
mkdir -p "$FRONTEND_WHEELS_DIR"

echo "========== 1. Check frontend package.json =========="
if [ ! -f "$FRONTEND_DIR/package.json" ]; then
    echo "Error: frontend/package.json not found at $FRONTEND_DIR/package.json"
    exit 1
fi
echo "Found: $FRONTEND_DIR/package.json"

echo "========== 2. Install npm dependencies (frontend/) =========="
cd "$FRONTEND_DIR"

# Use --legacy-peer-deps for React 19 + Next.js 15 compatibility
echo "Running: npm install --legacy-peer-deps"
npm install --legacy-peer-deps

# Verify node_modules exists
if [ ! -d "node_modules" ]; then
    echo "Error: node_modules not created at $FRONTEND_DIR/node_modules"
    exit 1
fi
echo "node_modules created successfully"

echo "========== 3. Pack node_modules as tarball =========="
# Pack node_modules directory as tarball (strip root 'node_modules' dir for Docker extraction)
# tar -czf <output> -C <base-dir> node_modules  →  archive contains node_modules/...
# Dockerfile: tar -xzf ... -C node_modules --strip-components=1  →  extracts contents into node_modules/
tar -czf "$NODE_MODULES_TARBALL" -C "$FRONTEND_DIR" node_modules

TARBALL_SIZE=$(du -h "$NODE_MODULES_TARBALL" | cut -f1)
echo "Done: $NODE_MODULES_TARBALL ($TARBALL_SIZE)"

echo ""
echo "========== Summary =========="
echo "Frontend npm dependencies prepared for offline mode:"
echo "  Tarball: $NODE_MODULES_TARBALL"
echo "  Size:    $TARBALL_SIZE"
echo ""
echo "Ready for: frontend/Dockerfile.qa, frontend/Dockerfile.offline"
echo "Note: node:20-alpine image also needs to be saved to packages/images/node-20-alpine.tar"
