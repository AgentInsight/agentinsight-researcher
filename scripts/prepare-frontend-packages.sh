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

# ========== 补装 Linux 平台原生模块 (Docker Alpine 构建) ==========
echo "========== 2.5. Install Linux platform native binaries =========="

# --- lightningcss (Tailwind CSS v4 依赖) ---
LIGHTNINGCSS_LINUX_BINARY="node_modules/lightningcss/lightningcss.linux-x64-musl.node"
if [ ! -f "$LIGHTNINGCSS_LINUX_BINARY" ]; then
    echo "Downloading lightningcss-linux-x64-musl..."
    LC_VERSION=$(node -p "require('./node_modules/lightningcss/package.json').version")
    echo "  lightningcss version: $LC_VERSION"
    # 注意: 包名无 @lightningcss/ scope 前缀
    TARBALL=$(npm pack "lightningcss-linux-x64-musl@$LC_VERSION" 2>&1 | grep '\.tgz$' | tail -1)
    if [ -f "$TARBALL" ]; then
        TEMP_DIR=$(mktemp -d)
        tar -xzf "$TARBALL" -C "$TEMP_DIR"
        NODE_FILE=$(find "$TEMP_DIR" -name "*.node" | head -1)
        if [ -n "$NODE_FILE" ]; then
            cp "$NODE_FILE" "$LIGHTNINGCSS_LINUX_BINARY"
            echo "  lightningcss.linux-x64-musl.node installed"
        fi
        rm -rf "$TEMP_DIR" "$TARBALL"
    fi
else
    echo "lightningcss.linux-x64-musl.node already exists"
fi

# --- esbuild ---
ESBUILD_DIR="node_modules/@esbuild/linux-x64"
if [ ! -d "$ESBUILD_DIR" ] && [ -d "node_modules/esbuild" ]; then
    ESBUILD_VERSION=$(node -p "require('./node_modules/esbuild/package.json').version")
    echo "Downloading @esbuild/linux-x64@$ESBUILD_VERSION..."
    mkdir -p "node_modules/@esbuild"
    TARBALL=$(npm pack "@esbuild/linux-x64@$ESBUILD_VERSION" 2>&1 | grep '\.tgz$' | tail -1)
    if [ -f "$TARBALL" ]; then
        mkdir -p "$ESBUILD_DIR"
        tar -xzf "$TARBALL" -C "$ESBUILD_DIR" --strip-components=1
        rm -f "$TARBALL"
        echo "  @esbuild/linux-x64 installed"
    fi
fi

# --- @tailwindcss/oxide ---
OXIDE_DIR="node_modules/@tailwindcss/oxide-linux-x64-musl"
if [ ! -d "$OXIDE_DIR" ] && [ -d "node_modules/@tailwindcss/oxide" ]; then
    OXIDE_VERSION=$(node -p "require('./node_modules/@tailwindcss/oxide/package.json').version")
    echo "Downloading @tailwindcss/oxide-linux-x64-musl@$OXIDE_VERSION..."
    TARBALL=$(npm pack "@tailwindcss/oxide-linux-x64-musl@$OXIDE_VERSION" 2>&1 | grep '\.tgz$' | tail -1)
    if [ -f "$TARBALL" ]; then
        mkdir -p "$OXIDE_DIR"
        tar -xzf "$TARBALL" -C "$OXIDE_DIR" --strip-components=1
        rm -f "$TARBALL"
        echo "  @tailwindcss/oxide-linux-x64-musl installed"
    fi
fi

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
