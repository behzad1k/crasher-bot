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
pip install -r requirements.txt
pip install pyinstaller

# Build
echo "Building executable..."
pyinstaller scripts/crasher_bot.spec --distpath dist/ --workpath build/

# Platform-specific packaging
if [[ "$OSTYPE" == "darwin"* ]]; then
    echo ""
    echo "=== macOS: Creating .dmg ==="
    if command -v create-dmg &> /dev/null; then
        create-dmg \
            --volname "Crasher Bot" \
            --window-pos 200 120 \
            --window-size 600 400 \
            --icon "CrasherBot.app" 150 190 \
            --app-drop-link 450 190 \
            "dist/CrasherBot.dmg" \
            "dist/CrasherBot.app"
        echo "✓ DMG created: dist/CrasherBot.dmg"
    else
        echo "Install create-dmg for .dmg packaging:"
        echo "  brew install create-dmg"
        echo ""
        echo "The .app bundle is ready at: dist/CrasherBot.app"
    fi
elif [[ "$OSTYPE" == "msys" || "$OSTYPE" == "win32" ]]; then
    echo ""
    echo "✓ Windows executable: dist/CrasherBot.exe"
else
    echo ""
    echo "✓ Linux executable: dist/CrasherBot"
fi

echo ""
echo "Build complete!"
