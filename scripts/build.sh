#!/usr/bin/env bash
# Build script for Crasher Bot
# Usage:
#   ./scripts/build.sh          # Builds for current platform
#   ./scripts/build.sh --clean  # Clean build

set -e

ROOT="$(cd "$(dirname "$0")/.." && pwd)"
cd "$ROOT"

# Clean previous builds
if [[ "$1" == "--clean" ]]; then
    echo "Cleaning previous builds..."
    rm -rf build/ dist/ *.spec
fi

# Install dependencies
echo "Installing dependencies..."
source .venv/bin/activate
pip3.14 install -r requirements.txt 2>/dev/null || pip3.14 install -r requirements.txt
pip3.14 install pyinstaller 2>/dev/null || pip3.14 install pyinstaller

# Build using python -m to avoid PATH issues
echo "Building executable..."
python3.14 -m PyInstaller scripts/crasher_bot.spec --distpath dist/ --workpath build/ --noconfirm

# Platform-specific packaging
if [[ "$OSTYPE" == "darwin"* ]]; then
    echo ""
    echo "=== macOS: Creating .dmg ==="
    if command -v create-dmg &> /dev/null; then
        # Remove old dmg if exists
        rm -f dist/CrashOut.dmg
        create-dmg \
            --volname "Crash Out" \
            --window-pos 200 120 \
            --window-size 600 400 \
            --icon "CrashOut.app" 150 190 \
            --app-drop-link 450 190 \
            "dist/CrashOut.dmg" \
            "dist/CrashOut.app"
        echo "✓ DMG created: dist/CrashOut.dmg"
    else
        echo "Install create-dmg for .dmg packaging:"
        echo "  brew install create-dmg"
        echo ""
        echo "The .app bundle is ready at: dist/CrashOut.app"
    fi
elif [[ "$OSTYPE" == "msys" || "$OSTYPE" == "win32" ]]; then
    echo ""
    echo "✓ Windows executable: dist/CrashOut.exe"
else
    echo ""
    echo "✓ Linux executable: dist/CrashOut"
fi

echo ""
echo "Build complete!"
