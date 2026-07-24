#!/bin/bash
# Secure build script for Wizard Launcher with Doppler
# Usage: ./build_secure.sh <azure-client-id>

set -e

if [ -z "$1" ]; then
    echo "Usage: $0 <azure-client-id>"
    echo ""
    echo "Example:"
    echo "  $0 11111111-2222-3333-4444-555555555555"
    echo ""
    echo "Or use environment variable:"
    echo "  export MC_LAUNCHER_CLIENT_ID='11111111-2222-3333-4444-555555555555'"
    echo "  $0"
    exit 1
fi

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PROJECT_ROOT="$(dirname "$SCRIPT_DIR")"
CLIENT_ID="${1:-$MC_LAUNCHER_CLIENT_ID}"

if [ -z "$CLIENT_ID" ]; then
    echo "Error: No Azure Client ID provided"
    exit 1
fi

echo "======================================"
echo "Wizard Launcher Secure Build"
echo "======================================"
echo ""
echo "⚠️  SECURITY WARNING:"
echo "   The Azure Client ID will be:"
echo "   1. Embedded (obfuscated) into the binary"
echo "   2. Removed from plaintext after build"
echo ""
echo "Client ID (obfuscated): ••••••••••••••••••"
echo ""

# Set environment variable
export MC_LAUNCHER_CLIENT_ID="$CLIENT_ID"

# Step 1: Embed the Client ID (obfuscated)
echo "📝 Step 1: Embedding Client ID (obfuscated)..."
python "$SCRIPT_DIR/embed_secret.py" --from-env

# Step 2: Build
echo ""
echo "🔨 Step 2: Building application..."
cd "$PROJECT_ROOT"
python tools/build.py --from-env --no-package || BUILD_FAILED=1

# Step 3: Clean up (remove plaintext from source)
echo ""
echo "🧹 Step 3: Cleaning up plaintext..."
python "$SCRIPT_DIR/embed_secret.py" --clean

if [ -n "$BUILD_FAILED" ]; then
    echo ""
    echo "❌ Build failed, but plaintext Client ID has been removed."
    exit 1
fi

echo ""
echo "======================================"
echo "✅ Build complete!"
echo "======================================"
echo ""
echo "Next steps:"
echo "1. Package the build_pyinstaller/WizardLauncher/WizardLauncher.exe"
echo "2. Create installer with Inno Setup"
echo "3. Distribute to users"
echo ""
echo "Users will see a login prompt on first run."
echo "No credentials are hardcoded anywhere."
